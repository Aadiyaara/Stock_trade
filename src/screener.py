"""Screener: rank S&P 500 stocks and pick top 50 bullish by multi-signal confluence."""
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.indicators import compute_bullish_score
from src.patterns import recent_pattern_score
from src.news_sentiment import get_news_score


def intraday_momentum_score(df: pd.DataFrame, lookback: int = 10) -> dict:
    """Score how well a stock trends intraday (open→close) vs overnight.
    
    Returns:
        intraday_ratio: % of total gains that happen intraday (higher = better for day trading)
        intraday_win_rate: % of days with positive open→close
        avg_intraday_pct: average intraday return %
        gap_fade_rate: % of days that gap up then close lower (bad for day trading)
        overextended: True if stock had abnormally large recent move (skip for day trading)
    """
    recent = df.tail(lookback)
    
    intraday_gains = 0.0
    overnight_gains = 0.0
    intraday_wins = 0
    gap_fades = 0
    intraday_pcts = []
    
    for i in range(len(recent)):
        o = float(recent.iloc[i]["Open"])
        c = float(recent.iloc[i]["Close"])
        intraday = c - o
        intraday_gains += intraday
        intraday_pcts.append((intraday / o) * 100)
        if intraday > 0:
            intraday_wins += 1
        
        if i > 0:
            prev_c = float(recent.iloc[i - 1]["Close"])
            overnight = o - prev_c
            overnight_gains += overnight
            # Gap up then fade
            if overnight > 0 and intraday < 0:
                gap_fades += 1
    
    total_move = abs(intraday_gains) + abs(overnight_gains)
    intraday_ratio = (intraday_gains / total_move * 100) if total_move > 0 else 50
    
    # Overextension detection
    overextended = _is_overextended(df)
    
    return {
        "intraday_ratio": round(intraday_ratio, 1),
        "intraday_win_rate": round(intraday_wins / len(recent) * 100, 1),
        "avg_intraday_pct": round(sum(intraday_pcts) / len(intraday_pcts), 3),
        "gap_fade_rate": round(gap_fades / max(len(recent) - 1, 1) * 100, 1),
        "overextended": overextended,
    }


def _is_overextended(df: pd.DataFrame) -> bool:
    """Detect if stock is overextended (had abnormally large move, due for pullback).
    
    Checks:
    1. Last day's move > 2x the 20-day average daily range
    2. Price is > 2 standard deviations above 20-day SMA
    3. At or near 52-week high (within 2%)
    """
    if len(df) < 20:
        return False
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Check 1: Yesterday's move was abnormally large
    daily_returns = ((df["Close"] - df["Open"]) / df["Open"]).abs()
    avg_range = daily_returns.rolling(20).mean().iloc[-2]  # avg before last day
    last_move = abs(float(last["Close"]) - float(prev["Close"])) / float(prev["Close"])
    if avg_range > 0 and last_move > avg_range * 3:
        return True
    
    # Check 2: Price far above moving average (> 2 std devs)
    sma20 = df["Close"].rolling(20).mean().iloc[-1]
    std20 = df["Close"].rolling(20).std().iloc[-1]
    if std20 > 0 and float(last["Close"]) > sma20 + 2 * std20:
        return True
    
    # Check 3: At 52-week high (within 2%)
    if len(df) >= 200:
        high_52w = df["High"].tail(252).max()
        if float(last["High"]) >= high_52w * 0.98:
            # Only flag if also had a big recent move (>5% in last 3 days)
            ret_3d = (float(last["Close"]) - float(df.iloc[-4]["Close"])) / float(df.iloc[-4]["Close"])
            if ret_3d > 0.05:
                return True
    
    return False


def quick_bullish_rank(df: pd.DataFrame, daytrade_mode: bool = False) -> float:
    """Fast pre-screen score for initial filtering. Returns 0-100."""
    score = 0
    close = df["Close"]
    
    # Price above 50-day SMA
    if len(close) >= 50 and close.iloc[-1] > close.rolling(50).mean().iloc[-1]:
        score += 25
    
    # Positive momentum (last 20 days)
    if len(close) >= 20:
        ret_20d = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]
        if ret_20d > 0:
            score += min(25, ret_20d * 100)
    
    # Positive 5-day momentum
    if len(close) >= 5:
        ret_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]
        if ret_5d > 0:
            score += min(25, ret_5d * 200)
    
    # Volume increasing
    if len(df) >= 20:
        vol_ratio = df["Volume"].iloc[-5:].mean() / df["Volume"].iloc[-20:].mean()
        if vol_ratio > 1.1:
            score += 25
    
    # Day trading penalty/bonus
    if daytrade_mode and len(df) >= 10:
        im = intraday_momentum_score(df)
        # HARD SKIP: overextended stocks (just had abnormal spike)
        if im["overextended"]:
            return 0  # exclude entirely
        # Penalize gap-and-fade stocks
        if im["gap_fade_rate"] > 40:
            score -= 30
        # Bonus for strong intraday trending
        if im["intraday_win_rate"] >= 60:
            score += 20
        if im["avg_intraday_pct"] > 0.3:
            score += 15
    
    return min(max(score, 0), 100)


def screen_top_bullish(ohlc_data: dict[str, pd.DataFrame], top_n: int = 50, 
                       daytrade_mode: bool = False) -> list[str]:
    """Quick-screen all stocks and return top N most bullish tickers."""
    scores = {}
    for ticker, df in ohlc_data.items():
        try:
            scores[ticker] = quick_bullish_rank(df, daytrade_mode)
        except Exception:
            continue
    
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [ticker for ticker, _ in ranked[:top_n]]


def analyze_stock(ticker: str, df: pd.DataFrame, include_news: bool = True,
                  daytrade_mode: bool = False) -> dict:
    """Full analysis on a single stock: patterns + indicators + news + intraday."""
    result = {"ticker": ticker}
    
    # Technical indicators (0-100)
    tech = compute_bullish_score(df)
    result["technical_score"] = tech["technical_score"]
    result["technical_signals"] = tech["signals"]
    
    # Pattern confluence (0-N, more = stronger)
    pat = recent_pattern_score(df, lookback=5)
    result["pattern_score"] = pat["pattern_score"]
    result["patterns_detected"] = pat["patterns_detected"]
    
    # News sentiment (0-20)
    if include_news:
        news = get_news_score(ticker)
        result["news_score"] = news["sentiment_score"]
        result["news_polarity"] = news["avg_polarity"]
    else:
        result["news_score"] = 10  # neutral default
    
    # Intraday momentum (for day trading)
    im = intraday_momentum_score(df)
    result["intraday_stats"] = im
    
    # Hard reject overextended stocks in daytrade mode
    if daytrade_mode and im["overextended"]:
        result["composite_score"] = 0
        result["confidence"] = "SKIP"
        result["skip_reason"] = "OVEREXTENDED — abnormal recent spike, high pullback risk"
        return result
    
    # Composite score: weighted combination
    if daytrade_mode:
        # Day trade: 35% technical, 20% patterns, 10% news, 35% intraday momentum
        pattern_normalized = min(pat["pattern_score"] * 10, 20)
        intraday_score = max(0, min(35, im["intraday_win_rate"] * 0.35 + im["avg_intraday_pct"] * 10 - im["gap_fade_rate"] * 0.2))
        composite = (
            tech["technical_score"] * 0.35 +
            pattern_normalized +
            result["news_score"] * 0.5 +
            intraday_score
        )
    else:
        # Swing: 50% technical, 30% patterns, 20% news
        pattern_normalized = min(pat["pattern_score"] * 10, 30)
        composite = (
            tech["technical_score"] * 0.5 +
            pattern_normalized +
            result["news_score"]
        )
    result["composite_score"] = round(composite, 1)
    
    # Confidence level
    total_signals = len(tech["signals"]) + len(pat["patterns_detected"])
    if daytrade_mode:
        # Higher bar for day trading confidence
        if total_signals >= 4 and im["intraday_win_rate"] >= 60 and im["gap_fade_rate"] <= 30:
            result["confidence"] = "HIGH"
        elif total_signals >= 3 and im["intraday_win_rate"] >= 50:
            result["confidence"] = "MEDIUM"
        else:
            result["confidence"] = "LOW"
    else:
        if total_signals >= 5:
            result["confidence"] = "HIGH"
        elif total_signals >= 3:
            result["confidence"] = "MEDIUM"
        else:
            result["confidence"] = "LOW"
    
    return result


def run_full_analysis(ohlc_data: dict[str, pd.DataFrame], top_n: int = 50, 
                      include_news: bool = True, max_workers: int = 5,
                      daytrade_mode: bool = False) -> list[dict]:
    """Full pipeline: screen top N, then deep-analyze each."""
    mode_str = "day trading" if daytrade_mode else "swing/position"
    print(f"Screening {len(ohlc_data)} stocks for top {top_n} bullish ({mode_str} mode)...")
    top_tickers = screen_top_bullish(ohlc_data, top_n, daytrade_mode)
    print(f"Analyzing {len(top_tickers)} stocks in detail...")
    
    results = []
    
    if include_news:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(analyze_stock, t, ohlc_data[t], True, daytrade_mode): t
                for t in top_tickers
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    continue
    else:
        for t in top_tickers:
            try:
                results.append(analyze_stock(t, ohlc_data[t], False, daytrade_mode))
            except Exception:
                continue
    
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results
