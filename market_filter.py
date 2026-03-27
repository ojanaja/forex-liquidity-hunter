"""
Forex Liquidity Hunter – Market Filter Module (V18)
====================================================
Central module for all pre-entry market condition checks:
  1. HTF Trend Analysis (EMA 50/200 + Market Structure)
  2. Sideways Detection (ATR + Bollinger Band Squeeze)
  3. LTF Entry Confirmations (RSI, Candle Patterns, Volume)
  4. Master Validation Gate
"""
import logging
from typing import Optional

import pandas as pd
import numpy as np

import config
import mt5_bridge

logger = logging.getLogger(__name__)


# ======================================================================
# 1. HTF Trend Analysis (EMA 50 + EMA 200 + Market Structure)
# ======================================================================

def _compute_market_structure(df: pd.DataFrame, lookback: int) -> str:
    """
    Detect market structure from swing highs/lows.
    Returns: "UPTREND" (HH + HL), "DOWNTREND" (LH + LL), or "SIDEWAYS".
    """
    if len(df) < lookback:
        return "SIDEWAYS"

    highs = df["high"].iloc[-lookback:]
    lows = df["low"].iloc[-lookback:]

    # Find swing points (local extremes over 5-bar window)
    swing_highs = []
    swing_lows = []
    window = 5

    for i in range(window, len(highs) - window):
        if highs.iloc[i] == highs.iloc[i - window:i + window + 1].max():
            swing_highs.append(highs.iloc[i])
        if lows.iloc[i] == lows.iloc[i - window:i + window + 1].min():
            swing_lows.append(lows.iloc[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "SIDEWAYS"

    # Check last 2 swing points
    hh = swing_highs[-1] > swing_highs[-2]  # Higher High
    hl = swing_lows[-1] > swing_lows[-2]     # Higher Low
    lh = swing_highs[-1] < swing_highs[-2]   # Lower High
    ll = swing_lows[-1] < swing_lows[-2]     # Lower Low

    if hh and hl:
        return "UPTREND"
    elif lh and ll:
        return "DOWNTREND"
    else:
        return "SIDEWAYS"


def get_htf_trend(symbol: str) -> Optional[str]:
    """
    Determine HTF trend using EMA 50/200 crossover + market structure.

    Returns:
        "UPTREND"   — EMA50 > EMA200 AND structure confirms HH/HL
        "DOWNTREND" — EMA50 < EMA200 AND structure confirms LH/LL
        "SIDEWAYS"  — EMAs tangled or structure unclear
        None        — Data unavailable
    """
    if not getattr(config, "USE_HTF_FILTER", False):
        return "UPTREND"  # No filter = allow all

    # Need enough bars for EMA 200 + structure lookback
    bars_needed = max(config.HTF_EMA_SLOW, config.HTF_STRUCTURE_LOOKBACK) + 50
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.HTF_TIMEFRAME_MINUTES,
        count=bars_needed,
    )

    if df is None or len(df) < config.HTF_EMA_SLOW:
        logger.warning(f"[HTF] Insufficient data for {symbol}")
        return None

    # Compute dual EMAs
    df["ema_fast"] = df["close"].ewm(span=config.HTF_EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=config.HTF_EMA_SLOW, adjust=False).mean()

    last_ema_fast = df["ema_fast"].iloc[-1]
    last_ema_slow = df["ema_slow"].iloc[-1]

    # EMA trend direction
    if last_ema_fast > last_ema_slow:
        ema_trend = "UPTREND"
    elif last_ema_fast < last_ema_slow:
        ema_trend = "DOWNTREND"
    else:
        ema_trend = "SIDEWAYS"

    # Market structure confirmation
    structure = _compute_market_structure(df, config.HTF_STRUCTURE_LOOKBACK)

    # Both must agree for a clear trend
    if ema_trend == structure:
        logger.debug(f"[HTF] {symbol}: {ema_trend} (EMA + Structure confirmed)")
        return ema_trend
    else:
        logger.debug(
            f"[HTF] {symbol}: SIDEWAYS (EMA={ema_trend}, Structure={structure})"
        )
        return "SIDEWAYS"


# ======================================================================
# 2. Sideways Detection (ATR + Bollinger Band Squeeze)
# ======================================================================

def _compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Compute Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def is_sideways(symbol: str) -> bool:
    """
    Detect sideways/ranging market using:
    1. ATR: Current ATR < ATR_LOW_VOLATILITY_FACTOR * rolling average ATR
    2. Bollinger Band Squeeze: bandwidth / price < BB_SQUEEZE_THRESHOLD

    Returns True if BOTH indicators suggest low volatility / range.
    """
    bars_needed = max(config.ATR_PERIOD, config.BB_PERIOD) * 3
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.HTF_TIMEFRAME_MINUTES,
        count=bars_needed,
    )

    if df is None or len(df) < bars_needed:
        return False  # Can't determine, assume not sideways

    # --- ATR Check ---
    atr = _compute_atr(df, config.ATR_PERIOD)
    current_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-config.ATR_PERIOD * 2:-config.ATR_PERIOD].mean()

    if pd.isna(current_atr) or pd.isna(avg_atr) or avg_atr <= 0:
        return False

    atr_is_low = current_atr < (avg_atr * config.ATR_LOW_VOLATILITY_FACTOR)

    # --- Bollinger Band Squeeze Check ---
    sma = df["close"].rolling(window=config.BB_PERIOD).mean()
    std = df["close"].rolling(window=config.BB_PERIOD).std()
    upper_band = sma + (config.BB_STD_DEV * std)
    lower_band = sma - (config.BB_STD_DEV * std)

    last_price = df["close"].iloc[-1]
    band_width = (upper_band.iloc[-1] - lower_band.iloc[-1])

    if last_price <= 0:
        return False

    bb_squeeze = (band_width / last_price) < config.BB_SQUEEZE_THRESHOLD

    is_range = atr_is_low and bb_squeeze

    if is_range:
        logger.info(
            f"[SIDEWAYS] {symbol}: ATR={current_atr:.5f} "
            f"(avg={avg_atr:.5f}, threshold={avg_atr * config.ATR_LOW_VOLATILITY_FACTOR:.5f}), "
            f"BB width={band_width / last_price:.4%}"
        )

    return is_range


# ======================================================================
# 3. LTF Entry Confirmations (RSI, Candle Patterns, Volume)
# ======================================================================

def _detect_engulfing(df: pd.DataFrame, direction: str) -> bool:
    """Check if the last candle is an engulfing pattern."""
    if len(df) < 2:
        return False

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if direction == "BUY":
        # Bullish engulfing: prev bearish, curr bullish and body covers prev
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        engulfs = curr["close"] > prev["open"] and curr["open"] < prev["close"]
        return prev_bearish and curr_bullish and engulfs

    elif direction == "SELL":
        # Bearish engulfing: prev bullish, curr bearish and body covers prev
        prev_bullish = prev["close"] > prev["open"]
        curr_bearish = curr["close"] < curr["open"]
        engulfs = curr["close"] < prev["open"] and curr["open"] > prev["close"]
        return prev_bullish and curr_bearish and engulfs

    return False


def _detect_rejection(df: pd.DataFrame, direction: str) -> bool:
    """Check if the last candle shows rejection (long wick)."""
    if len(df) < 1:
        return False

    candle = df.iloc[-1]
    body = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]

    if total_range <= 0:
        return False

    if direction == "BUY":
        # Pin bar: long lower wick (wick > 2x body)
        lower_wick = min(candle["open"], candle["close"]) - candle["low"]
        return lower_wick > body * 2

    elif direction == "SELL":
        # Pin bar: long upper wick (wick > 2x body)
        upper_wick = candle["high"] - max(candle["open"], candle["close"])
        return upper_wick > body * 2

    return False


def get_ltf_confirmations(symbol: str, direction: str) -> int:
    """
    Count LTF confirmations for entry quality.

    Checks:
    1. RSI not in extreme zone opposing the entry
    2. Engulfing candle pattern
    3. Rejection / pin bar pattern
    4. Volume spike (tick_volume > 1.5x average)

    Returns: number of confirmations (0-4)
    """
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.LTF_TIMEFRAME_MINUTES,
        count=30,
    )

    if df is None or len(df) < 20:
        return 0

    confirmations = 0
    reasons = []

    # --- 1. RSI Check ---
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()

    if loss.iloc[-1] != 0:
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
    else:
        rsi = 100.0

    # RSI should NOT be in extreme opposing zone
    if direction == "BUY" and rsi < config.RSI_OB:
        # Not overbought = good for buy
        confirmations += 1
        reasons.append(f"RSI={rsi:.1f} (not OB)")
    elif direction == "SELL" and rsi > config.RSI_OS:
        # Not oversold = good for sell
        confirmations += 1
        reasons.append(f"RSI={rsi:.1f} (not OS)")

    # --- 2. Engulfing Pattern ---
    if _detect_engulfing(df, direction):
        confirmations += 1
        reasons.append("Engulfing")

    # --- 3. Rejection / Pin Bar ---
    if _detect_rejection(df, direction):
        confirmations += 1
        reasons.append("Rejection")

    # --- 4. Volume Spike ---
    if "tick_volume" in df.columns:
        avg_vol = df["tick_volume"].iloc[-20:-1].mean()
        last_vol = df["tick_volume"].iloc[-1]
        if avg_vol > 0 and last_vol > avg_vol * 1.5:
            confirmations += 1
            reasons.append(f"Vol spike ({last_vol:.0f} vs avg {avg_vol:.0f})")

    logger.debug(
        f"[LTF] {symbol} {direction}: {confirmations} confirmations — "
        + ", ".join(reasons) if reasons else "none"
    )
    return confirmations


# ======================================================================
# 4. Master Validation Gate (6-Point Pre-Entry Check)
# ======================================================================

def validate_entry(
    symbol: str,
    direction: str,
    rr_ratio: float,
    risk_manager=None,
) -> tuple[bool, str]:
    """
    Master validation function — ALL checks must pass.

    6-Point Logic Gate:
    1. Is trend clear? (HTF not SIDEWAYS)
    2. Is entry aligned with trend? (direction matches HTF trend)
    3. Are there strong confirmations? (LTF >= MIN_CONFIRMATIONS)
    4. Is risk-reward valid? (RR >= MIN_RISK_REWARD_RATIO)
    5. Is pair not correlated with open positions? (correlation filter)
    6. Is daily trade limit not exceeded? (checked via risk_manager)

    Returns:
        (True, "OK") if all pass
        (False, reason) if any fail
    """
    # --- Check 1: Trend clarity ---
    htf_trend = get_htf_trend(symbol)
    if htf_trend is None:
        return False, "HTF data unavailable"
    if htf_trend == "SIDEWAYS":
        return False, f"Market SIDEWAYS — no clear trend on HTF"

    # --- Check 2: Entry aligned with trend ---
    if direction == "BUY" and htf_trend != "UPTREND":
        return False, f"BUY against HTF trend ({htf_trend})"
    if direction == "SELL" and htf_trend != "DOWNTREND":
        return False, f"SELL against HTF trend ({htf_trend})"

    # --- Check 3: Sideways detection (ATR + BB) ---
    if is_sideways(symbol):
        return False, "Sideways detected (low ATR + BB squeeze)"

    # --- Check 4: LTF Confirmations ---
    confirms = get_ltf_confirmations(symbol, direction)
    if confirms < config.MIN_CONFIRMATIONS:
        return False, (
            f"Insufficient confirmations: {confirms}/{config.MIN_CONFIRMATIONS}"
        )

    # --- Check 5: Risk-Reward ---
    if rr_ratio < config.MIN_RISK_REWARD_RATIO:
        return False, (
            f"RR too low: {rr_ratio:.2f} (min {config.MIN_RISK_REWARD_RATIO})"
        )

    # --- Check 6: Correlation filter ---
    if risk_manager is not None:
        corr_ok, corr_reason = risk_manager.check_correlation_filter(symbol)
        if not corr_ok:
            return False, corr_reason

    logger.info(
        f"[VALIDATE] {symbol} {direction}: ALL CHECKS PASSED "
        f"(trend={htf_trend}, confirms={confirms}, RR={rr_ratio:.2f})"
    )
    return True, "OK"
