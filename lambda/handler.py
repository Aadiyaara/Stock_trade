import json
import os
import boto3
import requests as _req
from datetime import datetime, date, timedelta
from src.data_fetcher import get_sp500_tickers, fetch_ohlc, fetch_ohlc_cached, fetch_open_price, fetch_current_prices
from src.screener import run_full_analysis
from src.exit_strategy import analyze_intraday_profile
from src.learn import analyze_performance, generate_insights, suggest_weight_adjustments, readiness_report

s3 = boto3.client("s3")
BUCKET = os.environ["TRADES_BUCKET"]
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "DT3pw8H1EFAcMF8LtysDQwOMfmtyAzqO")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
TRADES_KEY = "paper_trades.json"
LEARNINGS_KEY = "learnings.json"
DASHBOARD_KEY = "docs/paper_trades.json"
RECOMMENDATIONS_KEY = "recommendations.json"

# Alpaca live trading
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_LIVE = os.environ.get("ALPACA_LIVE_TRADING", "false").lower() == "true"
ALPACA_BASE_URL = "https://api.alpaca.markets" if ALPACA_LIVE else "https://paper-api.alpaca.markets"
LIVE_DAILY_BUDGET = float(os.environ.get("LIVE_DAILY_BUDGET", "1000"))

DAILY_BUDGET = 100.0
TOP_PICKS = 5
MIN_SCORE = 65.0
PROFIT_TARGET_PCT = 0.25
STOP_LOSS_PCT = 1.0


def load_trades():
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=TRADES_KEY)
        return json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"trades": [], "summary": {"total_invested": 0, "total_pnl": 0, "days": 0}}


def save_trades(data):
    body = json.dumps(data, indent=2)
    s3.put_object(Bucket=BUCKET, Key=TRADES_KEY, Body=body, ContentType="application/json")
    s3.put_object(Bucket=BUCKET, Key=DASHBOARD_KEY, Body=body, ContentType="application/json")


def save_learnings(data):
    data["last_updated"] = datetime.now().isoformat()
    s3.put_object(Bucket=BUCKET, Key=LEARNINGS_KEY, Body=json.dumps(data, indent=2), ContentType="application/json")


def pre_market_recommend(event, context):
    """Runs at 4:30 AM ET — screens S&P 500 using previous day's data and saves picks to S3."""
    today = date.today().isoformat()

    tickers = get_sp500_tickers()
    ohlc_data = fetch_ohlc_cached(tickers, s3_client=s3, bucket=BUCKET)

    if not ohlc_data:
        return {"statusCode": 200, "body": "No OHLC data available"}

    results = run_full_analysis(ohlc_data, top_n=50, include_news=False, daytrade_mode=True)

    qualified = [r for r in results if r["composite_score"] >= MIN_SCORE and r["confidence"] == "HIGH"]
    picks = qualified[:TOP_PICKS * 2]

    # Get previous close as reference price
    pick_tickers = [r["ticker"] for r in picks]
    prev_closes = fetch_current_prices(pick_tickers)

    recommendations = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "picks": [],
        "total_screened": len(ohlc_data),
        "total_qualified": len(qualified),
    }

    for r in picks:
        recommendations["picks"].append({
            "ticker": r["ticker"],
            "prev_close": prev_closes.get(r["ticker"]),
            "composite_score": r["composite_score"],
            "confidence": r["confidence"],
            "technical_score": r["technical_score"],
            "pattern_score": r["pattern_score"],
            "patterns_detected": r.get("patterns_detected", {}),
            "technical_signals": r.get("technical_signals", {}),
            "intraday_stats": r.get("intraday_stats", {}),
        })

    s3.put_object(
        Bucket=BUCKET,
        Key=RECOMMENDATIONS_KEY,
        Body=json.dumps(recommendations, indent=2),
        ContentType="application/json",
    )

    ticker_list = [p["ticker"] for p in picks]
    return {"statusCode": 200, "body": f"Recommendations for {today}: {', '.join(ticker_list) or 'NONE'}"}


def _fetch_finnhub_quotes(tickers: list[str]) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Get real-time quotes from Finnhub. Returns (current_prices, prev_closes, day_highs)."""
    prices = {}
    prev_closes = {}
    day_highs = {}
    for ticker in tickers:
        try:
            resp = _req.get("https://finnhub.io/api/v1/quote",
                            params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                price = data.get("c", 0)
                pc = data.get("pc", 0)
                high = data.get("h", 0)
                if price > 0:
                    prices[ticker] = price
                if pc > 0:
                    prev_closes[ticker] = pc
                if high > 0:
                    day_highs[ticker] = high
        except Exception as e:
            print(f"[finnhub] {ticker}: {e}")
    return prices, prev_closes, day_highs


def _fetch_analyst_ratings(tickers: list[str]) -> dict[str, float]:
    """Get analyst buy ratio for each ticker. Returns {ticker: buy_ratio (0-1)}."""
    ratings = {}
    for ticker in tickers:
        try:
            resp = _req.get("https://finnhub.io/api/v1/stock/recommendation",
                            params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    latest = data[0]
                    total = latest["strongBuy"] + latest["buy"] + latest["hold"] + latest["sell"] + latest["strongSell"]
                    if total > 0:
                        buy_ratio = (latest["strongBuy"] + latest["buy"]) / total
                        ratings[ticker] = round(buy_ratio, 2)
        except Exception as e:
            print(f"[analyst] {ticker}: {e}")
    return ratings


def _has_recent_earnings(tickers: list[str]) -> set[str]:
    """Check which tickers reported earnings in last 3 trading days using calendar only."""
    today = date.today()
    from_date = (today - timedelta(days=5)).isoformat()
    to_date = today.isoformat()
    recent = set()
    tickers_set = set(tickers)
    try:
        resp = _req.get("https://finnhub.io/api/v1/calendar/earnings",
                        params={"from": from_date, "to": to_date, "token": FINNHUB_KEY}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for e in data.get("earningsCalendar", []):
                if e.get("symbol") in tickers_set:
                    recent.add(e["symbol"])
    except Exception as e:
        print(f"[earnings_calendar] {e}")
    return recent


def _alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}


def _alpaca_buy(ticker: str, amount: float, entry_price: float = 0) -> dict:
    """Place a bracket order: market buy + take-profit limit + stop-loss stop."""
    order_body = {
        "symbol": ticker,
        "notional": str(round(amount, 2)),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    if entry_price > 0:
        take_profit_price = round(entry_price * (1 + PROFIT_TARGET_PCT / 100), 2)
        stop_loss_price = round(entry_price * (1 - STOP_LOSS_PCT / 100), 2)
        order_body["order_class"] = "bracket"
        order_body["take_profit"] = {"limit_price": str(take_profit_price)}
        order_body["stop_loss"] = {"stop_price": str(stop_loss_price)}

    resp = _req.post(
        f"{ALPACA_BASE_URL}/v2/orders",
        headers=_alpaca_headers(),
        json=order_body,
        timeout=10,
    )
    result = {"ticker": ticker, "status_code": resp.status_code}
    if resp.status_code in (200, 201):
        order = resp.json()
        result["order_id"] = order["id"]
        result["status"] = order["status"]
    else:
        result["error"] = resp.text[:200]
    print(f"[alpaca_buy] {ticker} ${amount} (TP=${entry_price*(1+PROFIT_TARGET_PCT/100):.2f} SL=${entry_price*(1-STOP_LOSS_PCT/100):.2f}): {result}")
    return result


def _alpaca_get_position(ticker: str) -> dict:
    """Get current position details for a ticker."""
    resp = _req.get(
        f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
        headers=_alpaca_headers(),
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json()
    return {}


def _alpaca_place_limit_sell(ticker: str, qty: str, limit_price: float) -> dict:
    """Place a limit sell order (profit target) that auto-executes when price is hit."""
    resp = _req.post(
        f"{ALPACA_BASE_URL}/v2/orders",
        headers=_alpaca_headers(),
        json={
            "symbol": ticker,
            "qty": qty,
            "side": "sell",
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": "day",
        },
        timeout=10,
    )
    result = {"ticker": ticker, "status_code": resp.status_code}
    if resp.status_code in (200, 201):
        order = resp.json()
        result["order_id"] = order["id"]
        result["status"] = order["status"]
        result["limit_price"] = limit_price
    else:
        result["error"] = resp.text[:200]
    print(f"[alpaca_limit_sell] {ticker} @ ${limit_price:.2f}: {result}")
    return result


def _alpaca_sell(ticker: str) -> dict:
    """Close entire position for a ticker on Alpaca."""
    resp = _req.delete(
        f"{ALPACA_BASE_URL}/v2/positions/{ticker}",
        headers=_alpaca_headers(),
        timeout=10,
    )
    result = {"ticker": ticker, "status_code": resp.status_code}
    if resp.status_code in (200, 201, 204):
        if resp.text:
            order = resp.json()
            result["order_id"] = order.get("id")
        result["status"] = "closed"
    else:
        result["error"] = resp.text[:200]
    print(f"[alpaca_sell] {ticker}: {result}")
    return result


def morning_buy(event, context):
    today = date.today().isoformat()
    trades = load_trades()

    if any(t["date"] == today and t.get("open_price") for t in trades["trades"]):
        return {"statusCode": 200, "body": f"Already have picks for {today}"}

    # Read pre-computed recommendations instead of re-running analysis
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="recommendations.json")
        recs = json.loads(obj["Body"].read())
    except Exception:
        return {"statusCode": 200, "body": "No recommendations found — run stock-recommend first"}

    if recs.get("date") != today:
        return {"statusCode": 200, "body": f"Stale recommendations (from {recs.get('date')}), skipping"}

    candidates = recs["picks"][:TOP_PICKS * 2]
    picked_tickers = [r["ticker"] for r in candidates]
    print(f"Fetching live prices for {len(picked_tickers)} candidates: {picked_tickers}")
    live_prices, finnhub_prev, _ = _fetch_finnhub_quotes(picked_tickers)
    print(f"Live prices: {live_prices}")

    if not live_prices:
        return {"statusCode": 200, "body": "Market data unavailable"}

    # Filter: skip stocks that gapped > 2% from prev close
    MAX_GAP_PCT = 2.0
    filtered = []
    for r in candidates:
        ticker = r["ticker"]
        price = live_prices.get(ticker)
        prev = r.get("prev_close") or finnhub_prev.get(ticker)
        if not price or not prev:
            continue
        gap_pct = (price - prev) / prev * 100
        r["_gap_pct"] = round(gap_pct, 2)
        r["_live_price"] = price
        if gap_pct > MAX_GAP_PCT:
            print(f"  SKIP {ticker}: gapped +{gap_pct:.1f}%")
            continue
        if gap_pct < -MAX_GAP_PCT:
            print(f"  SKIP {ticker}: gapped {gap_pct:.1f}%")
            continue
        filtered.append(r)

    # Filter: skip stocks that reported earnings in last 3 days
    earnings_tickers = [r["ticker"] for r in filtered]
    recent_earnings = _has_recent_earnings(earnings_tickers)
    if recent_earnings:
        print(f"  SKIP post-earnings: {recent_earnings}")
    filtered = [r for r in filtered if r["ticker"] not in recent_earnings]

    # Filter: no-repeat rule — skip tickers picked yesterday
    yesterday_tickers = set()
    past_dates = sorted(set(t["date"] for t in trades["trades"] if t["date"] < today and t["ticker"] != "CASH" and t.get("open_price")), reverse=True)
    if past_dates:
        last_trade_date = past_dates[0]
        yesterday_tickers = set(t["ticker"] for t in trades["trades"] if t["date"] == last_trade_date and t["ticker"] != "CASH")
    if yesterday_tickers:
        before_repeat = len(filtered)
        filtered = [r for r in filtered if r["ticker"] not in yesterday_tickers]
        if len(filtered) < before_repeat:
            skipped_tickers = yesterday_tickers & set(r["ticker"] for r in candidates)
            print(f"  SKIP {before_repeat - len(filtered)} repeat tickers from {last_trade_date}: {skipped_tickers}")

    # Filter: blacklist sectors/stocks with 0% win rate
    BLACKLIST = {"DVA", "ON"}
    AVOID_SECTORS = {"Semis", "Industrial"}
    SECTOR_MAP = {
        "NVDA": "Semis", "ON": "Semis", "AMD": "Semis", "INTC": "Semis", "AVGO": "Semis",
        "ANET": "Networking", "FFIV": "Networking", "AKAM": "Networking", "NET": "Networking", "CSCO": "Networking",
        "DD": "Industrial", "EMR": "Industrial", "HON": "Industrial", "CAT": "Industrial", "GE": "Industrial",
        "DVA": "Healthcare", "HUM": "Healthcare", "ELV": "Healthcare", "UNH": "Healthcare", "CNC": "Healthcare", "BIIB": "Healthcare",
        "DDOG": "Software", "CRM": "Software", "ORCL": "Software", "CDNS": "Software", "SNPS": "Software",
        "FTNT": "Cybersecurity", "CRWD": "Cybersecurity", "PANW": "Cybersecurity",
        "CBOE": "Finance", "GS": "Finance", "FICO": "Finance", "CINF": "Finance", "IBKR": "Finance",
    }

    before_sector = len(filtered)
    filtered = [r for r in filtered if r["ticker"] not in BLACKLIST]
    filtered = [r for r in filtered if SECTOR_MAP.get(r["ticker"], "") not in AVOID_SECTORS]

    # Sector diversification: max 2 picks per sector
    sector_count = {}
    diversified = []
    for r in filtered:
        sector = SECTOR_MAP.get(r["ticker"], "Other")
        if sector_count.get(sector, 0) >= 2:
            print(f"  SKIP {r['ticker']}: already have 2 from {sector}")
            continue
        sector_count[sector] = sector_count.get(sector, 0) + 1
        diversified.append(r)
    filtered = diversified

    if before_sector != len(filtered):
        print(f"  Sector/blacklist filter: {before_sector} -> {len(filtered)} candidates")

    if not filtered:
        trades["trades"].append({
            "date": today, "ticker": "CASH", "open_price": 0, "shares": 0,
            "invested": 0, "close_price": 0, "pnl": 0, "confidence": "SKIP",
            "composite_score": 0,
        })
        save_trades(trades)
        return {"statusCode": 200, "body": "No qualified trades today — all gapped too much"}

    picks = filtered[:TOP_PICKS]
    per_stock = DAILY_BUDGET / len(picks)
    bought = []

    for r in picks:
        ticker = r["ticker"]
        open_price = live_prices.get(ticker, 0)
        if open_price <= 0:
            continue
        shares = per_stock / open_price
        trade = {
            "date": today,
            "ticker": ticker,
            "open_price": round(open_price, 2),
            "shares": round(shares, 6),
            "invested": per_stock,
            "close_price": None,
            "pnl": None,
            "confidence": r["confidence"],
            "composite_score": r["composite_score"],
        }
        trades["trades"].append(trade)
        bought.append(ticker)

    save_trades(trades)

    # Live trading via Alpaca (bracket orders: buy + take-profit + stop-loss)
    live_results = []
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        live_per_stock = LIVE_DAILY_BUDGET / len(picks)
        for r in picks:
            ticker = r["ticker"]
            entry_price = live_prices.get(ticker, 0)
            result = _alpaca_buy(ticker, live_per_stock, entry_price)
            live_results.append(result)

    body = f"Bought: {', '.join(bought)}"
    if live_results:
        body += f" | LIVE: {len([r for r in live_results if r.get('status')])} orders placed"
    return {"statusCode": 200, "body": body}


def close_and_learn(event, context):
    """Runs after market close: records P/L and runs learning analysis."""
    today = date.today().isoformat()
    trades = load_trades()

    today_trades = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is None]
    already_closed = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is not None and t["ticker"] != "CASH"]

    # --- Close remaining trades ---
    day_pnl = sum(t.get("pnl", 0) for t in already_closed)
    day_optimal = sum(t.get("optimal_pnl", 0) for t in already_closed)
    if today_trades:
        tickers = [t["ticker"] for t in today_trades]
        close_prices, _, day_highs = _fetch_finnhub_quotes(tickers)
        print(f"Close prices from Finnhub: {close_prices}")
        print(f"Day highs from Finnhub: {day_highs}")

        for t in today_trades:
            ticker = t["ticker"]
            close_price = close_prices.get(ticker, t["open_price"])
            high_price = day_highs.get(ticker, close_price)

            pnl = t["shares"] * (close_price - t["open_price"])
            optimal_pnl = t["shares"] * (high_price - t["open_price"])

            t["close_price"] = round(close_price, 2)
            t["high_price"] = round(high_price, 2)
            t["pnl"] = round(pnl, 2)
            t["optimal_pnl"] = round(optimal_pnl, 2)
            t["missed_pnl"] = round(optimal_pnl - pnl, 2)
            t["exit_reason"] = "HELD_TO_CLOSE"

            day_pnl += pnl
            day_optimal += optimal_pnl

        trades["summary"]["total_invested"] += DAILY_BUDGET
        trades["summary"]["total_pnl"] += round(day_pnl, 2)
        trades["summary"]["total_optimal_pnl"] = trades["summary"].get("total_optimal_pnl", 0) + round(day_optimal, 2)
        trades["summary"]["days"] += 1
        save_trades(trades)

        # Close live positions on Alpaca
        if ALPACA_API_KEY and ALPACA_SECRET_KEY:
            for t in today_trades:
                if t["ticker"] != "CASH":
                    _alpaca_sell(t["ticker"])

    # Safety net: close any orphaned Alpaca positions not tracked in paper_trades
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        resp = _req.get(f"{ALPACA_BASE_URL}/v2/positions", headers=_alpaca_headers(), timeout=10)
        if resp.status_code == 200:
            positions = resp.json()
            if positions:
                print(f"[close] Sweeping {len(positions)} orphaned position(s)")
                for p in positions:
                    _alpaca_sell(p["symbol"])

    # --- Learn ---
    perf = analyze_performance(trades)
    if perf["status"] != "NO_DATA":
        insights = generate_insights(perf)
        adjustments = suggest_weight_adjustments(perf)
        readiness = readiness_report(perf)
        learnings = {
            "weight_adjustments": adjustments,
            "insights": insights,
            "performance": {k: v for k, v in perf.items() if k != "_raw_trades"},
            "readiness": readiness,
        }
        save_learnings(learnings)

    return {"statusCode": 200, "body": f"Day P/L: ${day_pnl:+.2f} | Learning complete"}


def midday_exit_check(event, context):
    """Runs at 11:00 AM and 1:00 PM ET — sells positions that hit profit target or stop loss."""
    today = date.today().isoformat()
    trades = load_trades()

    open_trades = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is None]
    if not open_trades:
        return {"statusCode": 200, "body": "No open trades to check"}

    tickers = [t["ticker"] for t in open_trades]
    current_prices, _, _ = _fetch_finnhub_quotes(tickers)
    print(f"[midday] Prices: {current_prices}")

    exits = []
    for t in open_trades:
        ticker = t["ticker"]
        price = current_prices.get(ticker)
        if not price:
            continue

        open_price = t["open_price"]
        gain_pct = (price - open_price) / open_price * 100

        exit_reason = None
        if gain_pct >= PROFIT_TARGET_PCT:
            exit_reason = "PROFIT_TARGET"
            print(f"  TARGET HIT {ticker}: +{gain_pct:.2f}%")
        elif gain_pct <= -STOP_LOSS_PCT:
            exit_reason = "STOP_LOSS"
            print(f"  STOP LOSS {ticker}: {gain_pct:.2f}%")

        if exit_reason:
            pnl = t["shares"] * (price - open_price)
            t["close_price"] = round(price, 2)
            t["high_price"] = round(price, 2)
            t["pnl"] = round(pnl, 2)
            t["optimal_pnl"] = round(pnl, 2) if gain_pct > 0 else 0.0
            t["missed_pnl"] = 0.0
            t["exit_reason"] = exit_reason
            exits.append(ticker)

            if ALPACA_API_KEY and ALPACA_SECRET_KEY:
                _alpaca_sell(ticker)

    if exits:
        save_trades(trades)

    return {"statusCode": 200, "body": f"Midday check: {len(exits)} exits ({', '.join(exits) or 'none'})"}


def build_cache(event, context):
    """Incrementally fetches OHLC data and caches in S3. Runs every 10 min to stay within rate limits."""
    import time
    from src.data_fetcher import _fetch_ticker_aggs, RATE_LIMIT_DELAY

    tickers = get_sp500_tickers()
    today = datetime.now().strftime("%Y-%m-%d")
    start_str = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    # Load existing cache
    cache = {}
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="cache/ohlc_cache.json")
        cache = json.loads(obj["Body"].read())
    except Exception:
        pass

    # Find stale/missing tickers
    to_fetch = [t for t in tickers if t not in cache or cache[t].get("last_updated") != today]

    fetched = 0
    max_fetch = 50
    for ticker in to_fetch[:max_fetch]:
        if fetched > 0 and fetched % 5 == 0:
            time.sleep(RATE_LIMIT_DELAY)
        try:
            df = _fetch_ticker_aggs(ticker, start_str, today)
            if df is not None and len(df) >= 50:
                cache[ticker] = {
                    "last_updated": today,
                    "bars": df.reset_index().assign(Date=lambda x: x["Date"].astype(str)).to_dict("records"),
                }
                fetched += 1
        except Exception:
            continue

    # Save cache
    s3.put_object(Bucket=BUCKET, Key="cache/ohlc_cache.json", Body=json.dumps(cache), ContentType="application/json")

    total_cached = sum(1 for t in tickers if t in cache and cache[t].get("last_updated") == today)
    return {"statusCode": 200, "body": f"Cached {fetched} new tickers. Total fresh: {total_cached}/{len(tickers)}"}
