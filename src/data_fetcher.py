"""Fetch S&P 500 tickers and OHLC data."""
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


def get_sp500_tickers() -> list[str]:
    """Scrape S&P 500 tickers from Wikipedia."""
    import requests
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    table = pd.read_html(resp.text)[0]
    tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()
    return tickers


def fetch_ohlc(tickers: list[str], period_days: int = 365) -> dict[str, pd.DataFrame]:
    """Download 1yr OHLC data for given tickers. Returns {ticker: DataFrame}."""
    end = datetime.now()
    start = end - timedelta(days=period_days)
    data = {}
    raw = yf.download(tickers, start=start, end=end, group_by="ticker", progress=False, threads=True)
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = raw.copy()
            else:
                df = raw[ticker].copy()
            df = df.dropna(subset=["Close"])
            if len(df) >= 50:
                data[ticker] = df
        except (KeyError, TypeError):
            continue
    return data


def fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Get current/latest prices via yfinance."""
    prices = {}
    data = yf.download(tickers, period="1d", progress=False)
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                prices[ticker] = float(data["Close"].iloc[-1])
            else:
                prices[ticker] = float(data["Close"][ticker].iloc[-1])
        except (KeyError, IndexError):
            continue
    return prices
