"""Intraday exit strategy - detect optimal sell timing using historical patterns.

Strategies:
1. Profit target: Sell when up X% (based on historical avg high-from-open)
2. Trailing stop: Lock in gains, sell if price drops X% from intraday high
3. Time-based: Some stocks peak in first 1-2 hours then fade
"""
import pandas as pd
import numpy as np
from src.data_fetcher import _fetch_single_ticker
from datetime import datetime, timedelta


def analyze_intraday_profile(ticker: str, days: int = 20) -> dict:
    """Analyze when a stock typically hits its intraday high/low.

    Uses daily OHLC to compute:
    - avg % gain from open to high (potential profit target)
    - avg % drop from high to close (money left on table)
    - % of days where close < high by significant amount (early exit would help)
    """
    end = datetime.now()
    start = end - timedelta(days=days + 5)
    df = _fetch_single_ticker(ticker, start, end)
    if df is None or len(df) < 5:
        return {}
    df = df.tail(days)
    
    # How much does it typically run up from open?
    open_to_high_pct = ((df["High"] - df["Open"]) / df["Open"] * 100)
    
    # How much does it give back from high to close?
    high_to_close_pct = ((df["High"] - df["Close"]) / df["High"] * 100)
    
    # Days where selling at high would have been significantly better than close
    missed_gain = df["High"] - df["Close"]
    significant_miss = (missed_gain / df["Open"] * 100) > 0.5  # missed > 0.5%
    
    # Open to close (what we actually capture)
    open_to_close_pct = ((df["Close"] - df["Open"]) / df["Open"] * 100)
    
    avg_open_to_high = float(open_to_high_pct.mean())
    avg_high_to_close = float(high_to_close_pct.mean())
    avg_open_to_close = float(open_to_close_pct.mean())
    pct_days_missed = float(significant_miss.mean() * 100)
    
    # Suggested profit target: 70% of avg open-to-high (conservative)
    suggested_target = round(avg_open_to_high * 0.7, 2)
    
    # Suggested trailing stop: based on typical giveback
    suggested_trail = round(avg_high_to_close * 0.8, 2)
    
    return {
        "ticker": ticker,
        "avg_open_to_high": round(avg_open_to_high, 3),
        "avg_high_to_close_giveback": round(avg_high_to_close, 3),
        "avg_open_to_close": round(avg_open_to_close, 3),
        "pct_days_early_exit_better": round(pct_days_missed, 1),
        "suggested_profit_target_pct": max(suggested_target, 0.3),
        "suggested_trailing_stop_pct": max(suggested_trail, 0.3),
        "strategy": _pick_strategy(avg_open_to_high, avg_high_to_close, pct_days_missed),
    }


def _pick_strategy(open_to_high, high_to_close, pct_missed) -> str:
    """Recommend exit strategy based on stock behavior."""
    if pct_missed > 60 and high_to_close > 1.0:
        return "PROFIT_TARGET"  # Stock regularly gives back gains — take profit early
    elif high_to_close > 0.5:
        return "TRAILING_STOP"  # Some giveback — use trailing stop to lock in
    else:
        return "HOLD_TO_CLOSE"  # Stock tends to close near high — just hold


def compute_exit_plan(tickers: list[str]) -> list[dict]:
    """Generate exit plan for today's picks."""
    plans = []
    for ticker in tickers:
        profile = analyze_intraday_profile(ticker)
        if profile:
            plans.append(profile)
    return plans


def backtest_exit_strategies(ticker: str, days: int = 20) -> dict:
    """Compare hold-to-close vs profit-target vs trailing-stop on historical data."""
    end = datetime.now()
    start = end - timedelta(days=days + 5)
    df = _fetch_single_ticker(ticker, start, end)
    if df is None or len(df) < 5:
        return {}
    df = df.tail(days)
    
    profile = analyze_intraday_profile(ticker, days)
    target_pct = profile.get("suggested_profit_target_pct", 1.0)
    trail_pct = profile.get("suggested_trailing_stop_pct", 0.5)
    
    hold_profits = []
    target_profits = []
    trail_profits = []
    
    for _, row in df.iterrows():
        o, h, c = float(row["Open"]), float(row["High"]), float(row["Close"])
        
        # Strategy 1: Hold to close
        hold_profits.append((c - o) / o * 100)
        
        # Strategy 2: Profit target (sell at target or close, whichever first)
        target_price = o * (1 + target_pct / 100)
        if h >= target_price:
            target_profits.append(target_pct)  # hit target
        else:
            target_profits.append((c - o) / o * 100)  # held to close
        
        # Strategy 3: Trailing stop (approximate — sell at high minus trail)
        # Best case: captured high minus trailing stop amount
        trail_sell = h * (1 - trail_pct / 100)
        trail_profit = (trail_sell - o) / o * 100
        # But only if it actually went up first
        if h > o:
            trail_profits.append(max(trail_profit, (c - o) / o * 100))
        else:
            trail_profits.append((c - o) / o * 100)
    
    return {
        "ticker": ticker,
        "days": len(df),
        "hold_to_close": {"total": round(sum(hold_profits), 2), "avg": round(np.mean(hold_profits), 3)},
        "profit_target": {"total": round(sum(target_profits), 2), "avg": round(np.mean(target_profits), 3), "target": target_pct},
        "trailing_stop": {"total": round(sum(trail_profits), 2), "avg": round(np.mean(trail_profits), 3), "trail": trail_pct},
        "best_strategy": max(
            [("HOLD_TO_CLOSE", sum(hold_profits)), ("PROFIT_TARGET", sum(target_profits)), ("TRAILING_STOP", sum(trail_profits))],
            key=lambda x: x[1]
        )[0],
    }


if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["GOOG", "TSLA", "AMD", "AAPL"]
    
    print("EXIT STRATEGY ANALYSIS")
    print("=" * 65)
    
    for ticker in tickers:
        profile = analyze_intraday_profile(ticker)
        if not profile:
            continue
        bt = backtest_exit_strategies(ticker)
        
        print(f"\n{ticker}:")
        print(f"  Avg run-up from open: +{profile['avg_open_to_high']:.2f}%")
        print(f"  Avg giveback (high→close): -{profile['avg_high_to_close_giveback']:.2f}%")
        print(f"  Days where early exit wins: {profile['pct_days_early_exit_better']:.0f}%")
        print(f"  Recommended: {profile['strategy']}")
        print(f"    Profit target: +{profile['suggested_profit_target_pct']:.2f}%")
        print(f"    Trailing stop: -{profile['suggested_trailing_stop_pct']:.2f}%")
        
        if bt:
            print(f"  Backtest ({bt['days']} days):")
            print(f"    Hold to close:  {bt['hold_to_close']['total']:+.2f}% total")
            print(f"    Profit target:  {bt['profit_target']['total']:+.2f}% total")
            print(f"    Trailing stop:  {bt['trailing_stop']['total']:+.2f}% total")
            print(f"    → Best: {bt['best_strategy']}")
