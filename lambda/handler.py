import sys
import types
import requests as _requests

# yfinance requires curl_cffi for browser impersonation but we can use plain requests.
_cffi = types.ModuleType("curl_cffi")
_cffi_req = types.ModuleType("curl_cffi.requests")


class _CffiSession(_requests.Session):
    def __init__(self, *args, **kwargs):
        kwargs.pop("impersonate", None)
        kwargs.pop("browser_type", None)
        super().__init__()
        self.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })


_cffi_req.Session = _CffiSession
_cffi_req.Response = _requests.Response
_cffi_req.BrowserType = type("BrowserType", (), {"chrome": "chrome"})

_cffi_session = types.ModuleType("curl_cffi.requests.session")
_cffi_session.Session = _CffiSession
_cffi_req.session = _cffi_session

_cffi.requests = _cffi_req
_cffi.CurlHttpVersion = type("CurlHttpVersion", (), {"V2_0": 2})
sys.modules["curl_cffi"] = _cffi
sys.modules["curl_cffi.requests"] = _cffi_req
sys.modules["curl_cffi.requests.session"] = _cffi_session

import json
import os
import boto3
from datetime import datetime, date
from src.data_fetcher import get_sp500_tickers, fetch_ohlc
from src.screener import run_full_analysis
from src.exit_strategy import analyze_intraday_profile
from src.learn import analyze_performance, generate_insights, suggest_weight_adjustments, readiness_report
import yfinance as yf

s3 = boto3.client("s3")
BUCKET = os.environ["TRADES_BUCKET"]
TRADES_KEY = "paper_trades.json"
LEARNINGS_KEY = "learnings.json"
DASHBOARD_KEY = "docs/paper_trades.json"

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


def morning_buy(event, context):
    today = date.today().isoformat()
    trades = load_trades()

    if any(t["date"] == today and t.get("open_price") for t in trades["trades"]):
        return {"statusCode": 200, "body": f"Already have picks for {today}"}

    tickers = get_sp500_tickers()
    ohlc_data = fetch_ohlc(tickers)
    results = run_full_analysis(ohlc_data, top_n=50, include_news=False, daytrade_mode=True)
    top = results[:TOP_PICKS]

    picked_tickers = [r["ticker"] for r in top]
    live_prices = {}
    try:
        intraday = yf.download(picked_tickers, period="1d", interval="1m", progress=False)
        for ticker in picked_tickers:
            try:
                if len(picked_tickers) == 1:
                    live_prices[ticker] = float(intraday["Open"].iloc[0])
                else:
                    live_prices[ticker] = float(intraday["Open"][ticker].iloc[0])
            except (KeyError, IndexError):
                pass
    except (ValueError, KeyError):
        pass

    # Fallback: use daily open if intraday not available
    if not live_prices:
        try:
            daily = yf.download(picked_tickers, period="5d", progress=False)
            for ticker in picked_tickers:
                try:
                    if len(picked_tickers) == 1:
                        live_prices[ticker] = float(daily["Open"].iloc[-1])
                    else:
                        live_prices[ticker] = float(daily["Open"][ticker].iloc[-1])
                except (KeyError, IndexError):
                    pass
        except (ValueError, KeyError):
            pass

    if not live_prices:
        return {"statusCode": 200, "body": "Market data unavailable — likely outside trading hours"}

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


def afternoon_close(event, context):
    today = date.today().isoformat()
    trades = load_trades()

    today_trades = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is None]
    if not today_trades:
        return {"statusCode": 200, "body": f"No open trades for {today}"}

    tickers = [t["ticker"] for t in today_trades]
    prices = yf.download(tickers, period="1d", progress=False)

    for t in today_trades:
        ticker = t["ticker"]
        try:
            if len(tickers) == 1:
                actual_open = float(prices["Open"].iloc[-1])
            else:
                actual_open = float(prices["Open"][ticker].iloc[-1])
            if actual_open > 0 and abs(actual_open - t["open_price"]) / t["open_price"] > 0.001:
                t["open_price"] = round(actual_open, 2)
                t["shares"] = t["invested"] / actual_open
        except (KeyError, IndexError):
            pass

    day_pnl = 0
    day_optimal = 0
    for t in today_trades:
        ticker = t["ticker"]
        try:
            if len(tickers) == 1:
                close_price = float(prices["Close"].iloc[-1])
                high_price = float(prices["High"].iloc[-1])
            else:
                close_price = float(prices["Close"][ticker].iloc[-1])
                high_price = float(prices["High"][ticker].iloc[-1])
        except (KeyError, IndexError):
            close_price = t["open_price"]
            high_price = t["open_price"]

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
    return {"statusCode": 200, "body": f"Day P/L: ${day_pnl:+.2f} | Optimal: ${day_optimal:+.2f}"}


def learn(event, context):
    trades_data = load_trades()
    perf = analyze_performance(trades_data)

    if perf["status"] == "NO_DATA":
        return {"statusCode": 200, "body": "No trades to analyze"}

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
    return {"statusCode": 200, "body": json.dumps(readiness)}
