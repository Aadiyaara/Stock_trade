import json
import os
import boto3
from datetime import datetime, date, timedelta
from src.data_fetcher import get_sp500_tickers, fetch_ohlc, fetch_ohlc_cached, fetch_open_price, fetch_current_prices
from src.screener import run_full_analysis
from src.exit_strategy import analyze_intraday_profile
from src.learn import analyze_performance, generate_insights, suggest_weight_adjustments, readiness_report

s3 = boto3.client("s3")
BUCKET = os.environ["TRADES_BUCKET"]
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "DT3pw8H1EFAcMF8LtysDQwOMfmtyAzqO")
TRADES_KEY = "paper_trades.json"
LEARNINGS_KEY = "learnings.json"
DASHBOARD_KEY = "docs/paper_trades.json"
RECOMMENDATIONS_KEY = "recommendations.json"

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
    picks = qualified[:TOP_PICKS]

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

    top = recs["picks"][:TOP_PICKS]
    picked_tickers = [r["ticker"] for r in top]
    print(f"Fetching live prices for: {picked_tickers}")
    live_prices = fetch_open_price(picked_tickers)
    print(f"Live prices: {live_prices}")

    if not live_prices:
        return {"statusCode": 200, "body": "Market data unavailable"}

    qualified = [r for r in top if r["composite_score"] >= MIN_SCORE and r["confidence"] in ("HIGH", "MEDIUM")]

    if not qualified:
        trades["trades"].append({
            "date": today, "ticker": "CASH", "open_price": 0, "shares": 0,
            "invested": 0, "close_price": 0, "pnl": 0, "confidence": "SKIP",
            "composite_score": 0,
        })
        save_trades(trades)
        return {"statusCode": 200, "body": "No qualified trades today — sitting out"}

    picks = qualified[:TOP_PICKS]
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
    return {"statusCode": 200, "body": f"Bought: {', '.join(bought)}"}


def close_and_learn(event, context):
    """Runs after market close: records P/L and runs learning analysis."""
    import requests as _req
    today = date.today().isoformat()
    trades = load_trades()

    today_trades = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is None]

    # --- Close trades ---
    day_pnl = 0
    day_optimal = 0
    if today_trades:
        tickers = [t["ticker"] for t in today_trades]
        day_bars = {}
        for ticker in tickers:
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{today}/{today}"
                resp = _req.get(url, params={"adjusted": "true", "apiKey": POLYGON_KEY}, timeout=10)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        day_bars[ticker] = results[0]
            except Exception:
                continue

        for t in today_trades:
            ticker = t["ticker"]
            bar = day_bars.get(ticker)
            if bar:
                actual_open = float(bar["o"])
                if actual_open > 0 and abs(actual_open - t["open_price"]) / t["open_price"] > 0.001:
                    t["open_price"] = round(actual_open, 2)
                    t["shares"] = t["invested"] / actual_open

        for t in today_trades:
            ticker = t["ticker"]
            bar = day_bars.get(ticker, {})
            close_price = float(bar.get("c", t["open_price"]))
            high_price = float(bar.get("h", t["open_price"]))

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
