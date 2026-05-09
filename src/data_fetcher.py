"""Fetch S&P 500 tickers and OHLC data via Polygon.io API."""
import os
import json
import time
import pandas as pd
import requests
from datetime import datetime, timedelta
from io import StringIO

POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "DT3pw8H1EFAcMF8LtysDQwOMfmtyAzqO")
BASE_URL = "https://api.polygon.io"
RATE_LIMIT_DELAY = 12.5  # 5 calls/min = 1 call per 12s


def get_sp500_tickers() -> list[str]:
    """Scrape S&P 500 tickers from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    table = pd.read_html(StringIO(resp.text))[0]
    tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()
    return tickers


def fetch_ohlc(tickers: list[str], period_days: int = 365) -> dict[str, pd.DataFrame]:
    """Fetch OHLC data for tickers via Polygon with rate limiting.

    On free tier (5 calls/min), fetches as many as possible within timeout.
    """
    end = datetime.now()
    start = end - timedelta(days=period_days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    data = {}
    for i, ticker in enumerate(tickers):
        if i > 0 and i % 5 == 0:
            time.sleep(RATE_LIMIT_DELAY)
        try:
            df = _fetch_ticker_aggs(ticker, start_str, end_str)
            if df is not None and len(df) >= 50:
                data[ticker] = df
        except Exception:
            continue
    return data


def fetch_ohlc_cached(tickers: list[str], s3_client=None, bucket: str = None) -> dict[str, pd.DataFrame]:
    """Load cached OHLC from S3, only fetch missing/stale tickers from Polygon."""
    cache = {}
    if s3_client and bucket:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key="cache/ohlc_cache.json")
            cache = json.loads(obj["Body"].read())
        except Exception:
            pass

    today = datetime.now().strftime("%Y-%m-%d")
    data = {}
    to_fetch = []

    for ticker in tickers:
        if ticker in cache and cache[ticker].get("last_updated") == today:
            df = pd.DataFrame(cache[ticker]["bars"])
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date")
            if len(df) >= 50:
                data[ticker] = df
        else:
            to_fetch.append(ticker)

    # Fetch missing tickers (rate limited)
    end = datetime.now()
    start = end - timedelta(days=365)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    fetched = 0
    for i, ticker in enumerate(to_fetch):
        if fetched >= 5 and fetched % 5 == 0:
            time.sleep(RATE_LIMIT_DELAY)
        try:
            df = _fetch_ticker_aggs(ticker, start_str, end_str)
            if df is not None and len(df) >= 50:
                data[ticker] = df
                cache[ticker] = {
                    "last_updated": today,
                    "bars": df.reset_index().assign(Date=lambda x: x["Date"].astype(str)).to_dict("records"),
                }
                fetched += 1
        except Exception:
            continue
        # Stay within Lambda timeout (4 min budget for fetching)
        if fetched >= 20:
            break

    # Save updated cache
    if s3_client and bucket and fetched > 0:
        try:
            s3_client.put_object(
                Bucket=bucket,
                Key="cache/ohlc_cache.json",
                Body=json.dumps(cache),
                ContentType="application/json",
            )
        except Exception:
            pass

    return data


def _fetch_ticker_aggs(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch daily aggregates for a single ticker."""
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start_str}/{end_str}"
    resp = requests.get(url, params={"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": POLYGON_KEY}, timeout=10)
    if resp.status_code == 429:
        time.sleep(RATE_LIMIT_DELAY)
        resp = requests.get(url, params={"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": POLYGON_KEY}, timeout=10)
    if resp.status_code != 200:
        return None
    results = resp.json().get("results", [])
    if not results:
        return None
    df = pd.DataFrame(results)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume", "t": "Timestamp"})
    df["Date"] = pd.to_datetime(df["Timestamp"], unit="ms")
    df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
    return df


def _fetch_single_ticker(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch OHLC for a single ticker (used by exit_strategy)."""
    return _fetch_ticker_aggs(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


def fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Get previous close prices via Polygon prev endpoint."""
    prices = {}
    for i, ticker in enumerate(tickers):
        if i > 0 and i % 5 == 0:
            time.sleep(RATE_LIMIT_DELAY)
        try:
            url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev"
            resp = requests.get(url, params={"adjusted": "true", "apiKey": POLYGON_KEY}, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    prices[ticker] = float(results[0]["c"])
        except Exception:
            continue
    return prices


def fetch_open_price(tickers: list[str]) -> dict[str, float]:
    """Get today's open price via 1-min aggs, fallback to prev close."""
    prices = {}
    today = datetime.now().strftime("%Y-%m-%d")
    for i, ticker in enumerate(tickers):
        if i > 0 and i % 5 == 0:
            time.sleep(RATE_LIMIT_DELAY)
        try:
            url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{today}/{today}"
            resp = requests.get(url, params={"adjusted": "true", "sort": "asc", "limit": 1, "apiKey": POLYGON_KEY}, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    prices[ticker] = float(results[0]["o"])
        except Exception:
            continue

    # Fallback: previous close for any missing
    missing = [t for t in tickers if t not in prices]
    if missing:
        prev = fetch_current_prices(missing)
        prices.update(prev)
    return prices
