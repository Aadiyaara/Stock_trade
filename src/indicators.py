"""Technical indicators for bullish signal detection."""
import pandas as pd
import numpy as np


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def macd(df: pd.DataFrame) -> dict[str, pd.Series]:
    """MACD line, signal, and histogram."""
    ema12 = df["Close"].ewm(span=12).mean()
    ema26 = df["Close"].ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    hist = macd_line - signal
    return {"macd": macd_line, "signal": signal, "histogram": hist}


def bollinger_bands(df: pd.DataFrame, period: int = 20) -> dict[str, pd.Series]:
    """Bollinger Bands."""
    sma = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()
    return {"upper": sma + 2 * std, "middle": sma, "lower": sma - 2 * std}


def sma(df: pd.DataFrame, period: int) -> pd.Series:
    return df["Close"].rolling(period).mean()


def ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df["Close"].ewm(span=period).mean()


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df["Volume"].rolling(period).mean()


def compute_bullish_score(df: pd.DataFrame) -> dict:
    """Compute a bullish technical score (0-100) from indicators.
    
    Returns score and breakdown of which signals fired.
    """
    score = 0
    signals = {}
    
    # RSI: oversold bounce (30-50 range = bullish setup)
    r = rsi(df).iloc[-1]
    if 30 <= r <= 50:
        score += 15
        signals["rsi_oversold_bounce"] = round(r, 1)
    elif r < 30:
        score += 10  # deeply oversold, potential bounce
        signals["rsi_deeply_oversold"] = round(r, 1)
    
    # MACD: bullish crossover (histogram turning positive)
    m = macd(df)
    hist_now = m["histogram"].iloc[-1]
    hist_prev = m["histogram"].iloc[-2]
    if hist_now > 0 and hist_prev <= 0:
        score += 20
        signals["macd_bullish_crossover"] = True
    elif hist_now > hist_prev and hist_now > 0:
        score += 10
        signals["macd_momentum_increasing"] = True
    
    # Bollinger: price near lower band (potential bounce)
    bb = bollinger_bands(df)
    price = df["Close"].iloc[-1]
    bb_lower = bb["lower"].iloc[-1]
    bb_upper = bb["upper"].iloc[-1]
    bb_width = bb_upper - bb_lower
    if bb_width > 0:
        bb_position = (price - bb_lower) / bb_width
        if bb_position <= 0.2:
            score += 15
            signals["bb_near_lower"] = round(bb_position, 2)
    
    # Moving average alignment: 20 > 50 > 200 (bullish trend)
    sma20 = sma(df, 20).iloc[-1]
    sma50 = sma(df, 50).iloc[-1]
    sma200 = sma(df, 200).iloc[-1] if len(df) >= 200 else None
    
    if price > sma20 > sma50:
        score += 15
        signals["ma_bullish_alignment"] = True
    if sma200 and price > sma200:
        score += 10
        signals["above_200sma"] = True
    
    # Golden cross: 50 SMA crossing above 200 SMA recently
    if sma200 and len(df) >= 200:
        sma50_prev = sma(df, 50).iloc[-5]
        sma200_prev = sma(df, 200).iloc[-5]
        if sma50_prev <= sma200_prev and sma50 > sma200:
            score += 15
            signals["golden_cross"] = True
    
    # Volume surge: current volume > 1.5x average
    vol_avg = volume_sma(df).iloc[-1]
    vol_now = df["Volume"].iloc[-1]
    if vol_avg > 0 and vol_now > vol_avg * 1.5:
        score += 10
        signals["volume_surge"] = round(vol_now / vol_avg, 1)
    
    return {"technical_score": min(score, 100), "signals": signals}
