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
MIN_SCORE = 40.0


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

    qualified = [r for r in results if r["composite_score"] >= MIN_SCORE and r["confidence"] in ("HIGH", "MEDIUM")]
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


def _fetch_finnhub_quotes(tickers: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Get real-time quotes from Finnhub. Returns (current_prices, prev_closes)."""
    prices = {}
    prev_closes = {}
    for ticker in tickers:
        try:
            resp = _req.get("https://finnhub.io/api/v1/quote",
                            params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                price = data.get("c", 0)
                pc = data.get("pc", 0)
                if price > 0:
                    prices[ticker] = price
                if pc > 0:
                    prev_closes[ticker] = pc
        except Exception as e:
            print(f"[finnhub] {ticker}: {e}")
    return prices, prev_closes


def _alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}


def _alpaca_buy(ticker: str, amount: float) -> dict:
    """Place a market buy order on Alpaca for a dollar amount (fractional shares)."""
    resp = _req.post(
        f"{ALPACA_BASE_URL}/v2/orders",
        headers=_alpaca_headers(),
        json={
            "symbol": ticker,
            "notional": str(round(amount, 2)),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        },
        timeout=10,
    )
    result = {"ticker": ticker, "status_code": resp.status_code}
    if resp.status_code in (200, 201):
        order = resp.json()
        result["order_id"] = order["id"]
        result["status"] = order["status"]
    else:
        result["error"] = resp.text[:200]
    print(f"[alpaca_buy] {ticker} ${amount}: {result}")
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
    live_prices, finnhub_prev = _fetch_finnhub_quotes(picked_tickers)
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

    # Live trading via Alpaca
    live_results = []
    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        live_per_stock = LIVE_DAILY_BUDGET / len(picks)
        for r in picks:
            ticker = r["ticker"]
            result = _alpaca_buy(ticker, live_per_stock)
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

    # --- Close trades ---
    day_pnl = 0
    day_optimal = 0
    if today_trades:
        tickers = [t["ticker"] for t in today_trades]
        close_prices, _ = _fetch_finnhub_quotes(tickers)
        print(f"Close prices from Finnhub: {close_prices}")

        for t in today_trades:
            ticker = t["ticker"]
            close_price = close_prices.get(ticker, t["open_price"])
            high_price = close_price

            pnl = t["shares"] * (close_price - t["open_price"])
            optimal_pnl = t["shares"] * (high_price - t["open_price"])

            t["close_price"] = round(close_price, 2)
            t["high_price"] = round(high_price, 2)
            t["pnl"] = round(pnl, 2)
            t["optimal_pnl"] = round(optimal_pnl, 2)
            t["missed_pnl"] = round(optimal_pnl - pnl, 2)

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
