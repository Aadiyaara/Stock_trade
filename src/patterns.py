"""Candlestick pattern detection using OHLC data.

Each function returns a Series of signals: 1=bullish, -1=bearish, 0=none.
Focus on bullish patterns for screening.
"""
import pandas as pd
import numpy as np


def _body(df):
    return df["Close"] - df["Open"]

def _body_abs(df):
    return _body(df).abs()

def _upper_shadow(df):
    return df["High"] - df[["Open", "Close"]].max(axis=1)

def _lower_shadow(df):
    return df[["Open", "Close"]].min(axis=1) - df["Low"]

def _range(df):
    return df["High"] - df["Low"]

def _avg_body(df, n=14):
    return _body_abs(df).rolling(n).mean()


def doji(df) -> pd.Series:
    """Doji - indecision, potential reversal."""
    body = _body_abs(df)
    rng = _range(df)
    return ((body <= rng * 0.1) & (rng > 0)).astype(int)


def hammer(df) -> pd.Series:
    """Hammer - bullish reversal at bottom."""
    body = _body_abs(df)
    lower = _lower_shadow(df)
    upper = _upper_shadow(df)
    rng = _range(df)
    signal = (
        (lower >= body * 2) &
        (upper <= body * 0.3) &
        (body > rng * 0.1)
    )
    return signal.astype(int)


def inverted_hammer(df) -> pd.Series:
    """Inverted hammer - bullish reversal."""
    body = _body_abs(df)
    lower = _lower_shadow(df)
    upper = _upper_shadow(df)
    rng = _range(df)
    signal = (
        (upper >= body * 2) &
        (lower <= body * 0.3) &
        (body > rng * 0.1)
    )
    return signal.astype(int)


def bullish_engulfing(df) -> pd.Series:
    """Bullish engulfing - strong reversal."""
    prev_body = _body(df).shift(1)
    curr_body = _body(df)
    signal = (
        (prev_body < 0) &  # prev bearish
        (curr_body > 0) &  # curr bullish
        (df["Open"] <= df["Close"].shift(1)) &
        (df["Close"] >= df["Open"].shift(1))
    )
    return signal.astype(int)


def piercing_line(df) -> pd.Series:
    """Piercing line - bullish reversal."""
    prev_body = _body(df).shift(1)
    curr_body = _body(df)
    prev_mid = (df["Open"].shift(1) + df["Close"].shift(1)) / 2
    signal = (
        (prev_body < 0) &
        (curr_body > 0) &
        (df["Open"] < df["Close"].shift(1)) &
        (df["Close"] > prev_mid) &
        (df["Close"] < df["Open"].shift(1))
    )
    return signal.astype(int)


def morning_star(df) -> pd.Series:
    """Morning star - 3-candle bullish reversal."""
    body1 = _body(df).shift(2)
    body2_abs = _body_abs(df).shift(1)
    body3 = _body(df)
    avg = _avg_body(df)
    signal = (
        (body1 < 0) &
        (body1.abs() > avg.shift(2)) &
        (body2_abs < avg.shift(1) * 0.5) &
        (body3 > 0) &
        (df["Close"] > (df["Open"].shift(2) + df["Close"].shift(2)) / 2)
    )
    return signal.astype(int)


def three_white_soldiers(df) -> pd.Series:
    """Three white soldiers - strong bullish continuation."""
    b1 = _body(df).shift(2)
    b2 = _body(df).shift(1)
    b3 = _body(df)
    signal = (
        (b1 > 0) & (b2 > 0) & (b3 > 0) &
        (df["Close"].shift(1) > df["Close"].shift(2)) &
        (df["Close"] > df["Close"].shift(1)) &
        (df["Open"].shift(1) > df["Open"].shift(2)) &
        (df["Open"] > df["Open"].shift(1))
    )
    return signal.astype(int)


def bullish_harami(df) -> pd.Series:
    """Bullish harami - reversal inside bar."""
    prev_body = _body(df).shift(1)
    curr_body = _body(df)
    signal = (
        (prev_body < 0) &
        (curr_body > 0) &
        (df["Open"] > df["Close"].shift(1)) &
        (df["Close"] < df["Open"].shift(1))
    )
    return signal.astype(int)


def tweezer_bottom(df) -> pd.Series:
    """Tweezer bottom - double bottom reversal."""
    tolerance = _range(df) * 0.05
    signal = (
        (_body(df).shift(1) < 0) &
        (_body(df) > 0) &
        ((df["Low"] - df["Low"].shift(1)).abs() <= tolerance)
    )
    return signal.astype(int)


def dragonfly_doji(df) -> pd.Series:
    """Dragonfly doji - bullish at support."""
    body = _body_abs(df)
    rng = _range(df)
    lower = _lower_shadow(df)
    upper = _upper_shadow(df)
    signal = (
        (body <= rng * 0.1) &
        (lower >= rng * 0.6) &
        (upper <= rng * 0.1) &
        (rng > 0)
    )
    return signal.astype(int)


def rising_three_methods(df) -> pd.Series:
    """Rising three methods - bullish continuation (simplified)."""
    b1 = _body(df).shift(4)
    b5 = _body(df)
    # Middle 3 candles are small and contained
    mid_small = (
        (_body_abs(df).shift(3) < b1.abs() * 0.5) &
        (_body_abs(df).shift(2) < b1.abs() * 0.5) &
        (_body_abs(df).shift(1) < b1.abs() * 0.5)
    )
    signal = (
        (b1 > 0) & (b5 > 0) &
        mid_small &
        (df["Close"] > df["Close"].shift(4))
    )
    return signal.astype(int)


# Registry of all bullish patterns
PATTERNS = {
    "doji": doji,
    "hammer": hammer,
    "inverted_hammer": inverted_hammer,
    "bullish_engulfing": bullish_engulfing,
    "piercing_line": piercing_line,
    "morning_star": morning_star,
    "three_white_soldiers": three_white_soldiers,
    "bullish_harami": bullish_harami,
    "tweezer_bottom": tweezer_bottom,
    "dragonfly_doji": dragonfly_doji,
    "rising_three_methods": rising_three_methods,
}


def detect_all_patterns(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Run all pattern detectors. Returns {pattern_name: signal_series}."""
    return {name: func(df) for name, func in PATTERNS.items()}


def recent_pattern_score(df: pd.DataFrame, lookback: int = 5) -> dict:
    """Score recent pattern confluence. Returns pattern details and total score."""
    patterns = detect_all_patterns(df)
    recent = {}
    total = 0
    for name, signals in patterns.items():
        recent_signals = signals.iloc[-lookback:]
        count = int(recent_signals.sum())
        if count > 0:
            recent[name] = count
            total += count
    return {"patterns_detected": recent, "pattern_score": total}
