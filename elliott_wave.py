"""
Elliott Wave Strategy Module (V18)
===================================
Algorithmic implementation of Elliott Wave Theory.
Focuses on detecting Wave 1 -> Wave 2 completion -> Entry at Wave 3 start.

Wave 3 is statistically the strongest and longest impulse wave.
Uses Zigzag swing detection + Fibonacci retracement validation.
"""
import pandas as pd
import numpy as np
from typing import Optional

import config


# ══════════════════════════════════════════════════════════════════════════════
# ZIGZAG SWING DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def zigzag(df: pd.DataFrame, depth: int = 12, deviation: float = 5.0) -> list[dict]:
    """
    Identify significant swing highs and lows using zigzag algorithm.

    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        depth: Minimum bars between swings
        deviation: Minimum % price change to form new swing

    Returns:
        List of swing points: [{"type": "HIGH"/"LOW", "price": float, "index": int, "time": datetime}]
    """
    if len(df) < depth * 2:
        return []

    highs = df["high"].values
    lows = df["low"].values
    times = df.index

    swings = []
    last_type = None
    last_price = 0.0
    last_idx = 0

    # Seed with first swing
    init_high_idx = np.argmax(highs[:depth * 2])
    init_low_idx = np.argmin(lows[:depth * 2])

    if init_low_idx < init_high_idx:
        swings.append({"type": "LOW", "price": float(lows[init_low_idx]),
                        "index": int(init_low_idx), "time": times[init_low_idx]})
        swings.append({"type": "HIGH", "price": float(highs[init_high_idx]),
                        "index": int(init_high_idx), "time": times[init_high_idx]})
    else:
        swings.append({"type": "HIGH", "price": float(highs[init_high_idx]),
                        "index": int(init_high_idx), "time": times[init_high_idx]})
        swings.append({"type": "LOW", "price": float(lows[init_low_idx]),
                        "index": int(init_low_idx), "time": times[init_low_idx]})

    last_type = swings[-1]["type"]
    last_price = swings[-1]["price"]
    last_idx = swings[-1]["index"]

    start = max(init_high_idx, init_low_idx) + 1

    for i in range(start, len(df)):
        # Check if this bar creates a new swing high
        window_start = max(0, i - depth)
        window_end = min(len(df), i + depth + 1)

        is_swing_high = highs[i] == np.max(highs[window_start:window_end])
        is_swing_low = lows[i] == np.min(lows[window_start:window_end])

        if is_swing_high and last_type == "LOW":
            # New swing high after a low
            pct_change = abs(highs[i] - last_price) / last_price * 100
            if pct_change >= deviation / 100 and (i - last_idx) >= depth:
                swings.append({"type": "HIGH", "price": float(highs[i]),
                                "index": i, "time": times[i]})
                last_type = "HIGH"
                last_price = float(highs[i])
                last_idx = i

        elif is_swing_low and last_type == "HIGH":
            # New swing low after a high
            pct_change = abs(lows[i] - last_price) / last_price * 100
            if pct_change >= deviation / 100 and (i - last_idx) >= depth:
                swings.append({"type": "LOW", "price": float(lows[i]),
                                "index": i, "time": times[i]})
                last_type = "LOW"
                last_price = float(lows[i])
                last_idx = i

        elif is_swing_high and last_type == "HIGH":
            # Extension of existing high
            if highs[i] > last_price:
                swings[-1] = {"type": "HIGH", "price": float(highs[i]),
                              "index": i, "time": times[i]}
                last_price = float(highs[i])
                last_idx = i

        elif is_swing_low and last_type == "LOW":
            # Extension of existing low
            if lows[i] < last_price:
                swings[-1] = {"type": "LOW", "price": float(lows[i]),
                              "index": i, "time": times[i]}
                last_price = float(lows[i])
                last_idx = i

    return swings


# ══════════════════════════════════════════════════════════════════════════════
# WAVE PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def detect_wave12(swings: list[dict], direction: str) -> Optional[dict]:
    """
    Detect completed Wave 1 + Wave 2 pattern from recent swings.

    For BULLISH (buy at wave 3):
        Wave 1: swing LOW → swing HIGH (impulse up)
        Wave 2: swing HIGH → swing LOW (correction down, 38.2%-78.6% retrace)

    For BEARISH (sell at wave 3):
        Wave 1: swing HIGH → swing LOW (impulse down)
        Wave 2: swing LOW → swing HIGH (correction up, 38.2%-78.6% retrace)

    Returns:
        Dict with wave1_start, wave1_end, wave2_end, wave3_target, or None
    """
    if len(swings) < 3:
        return None

    retrace_min = getattr(config, "EW_WAVE2_RETRACE_MIN", 0.382)
    retrace_max = getattr(config, "EW_WAVE2_RETRACE_MAX", 0.786)
    min_wave1_pips = getattr(config, "EW_MIN_WAVE1_PIPS", 10.0)

    # Look at the last 3 swings
    s1, s2, s3 = swings[-3], swings[-2], swings[-1]

    if direction == "BULLISH":
        # Wave 1: LOW → HIGH, Wave 2: HIGH → LOW
        if s1["type"] != "LOW" or s2["type"] != "HIGH" or s3["type"] != "LOW":
            return None

        wave1_start = s1["price"]
        wave1_end = s2["price"]
        wave2_end = s3["price"]

        wave1_length = wave1_end - wave1_start  # positive (up)
        if wave1_length <= 0:
            return None

        # Wave 2 retracement
        wave2_retrace = (wave1_end - wave2_end) / wave1_length
        if wave2_retrace < retrace_min or wave2_retrace > retrace_max:
            return None

        # Wave 2 must NOT go below Wave 1 start (EW Rule)
        if wave2_end <= wave1_start:
            return None

        # Wave 3 target: 1.618 × Wave 1 from Wave 2 end
        wave3_target = wave2_end + (wave1_length * 1.618)

        return {
            "direction": "BUY",
            "wave1_start": wave1_start,
            "wave1_end": wave1_end,
            "wave2_end": wave2_end,
            "wave1_length": wave1_length,
            "wave2_retrace": wave2_retrace,
            "wave3_target": wave3_target,
            "wave2_time": s3["time"],
            "wave2_index": s3["index"],
        }

    elif direction == "BEARISH":
        # Wave 1: HIGH → LOW, Wave 2: LOW → HIGH
        if s1["type"] != "HIGH" or s2["type"] != "LOW" or s3["type"] != "HIGH":
            return None

        wave1_start = s1["price"]
        wave1_end = s2["price"]
        wave2_end = s3["price"]

        wave1_length = wave1_start - wave1_end  # positive (down move)
        if wave1_length <= 0:
            return None

        # Wave 2 retracement (upward correction)
        wave2_retrace = (wave2_end - wave1_end) / wave1_length
        if wave2_retrace < retrace_min or wave2_retrace > retrace_max:
            return None

        # Wave 2 must NOT go above Wave 1 start (EW Rule)
        if wave2_end >= wave1_start:
            return None

        # Wave 3 target: 1.618 × Wave 1 from Wave 2 end
        wave3_target = wave2_end - (wave1_length * 1.618)

        return {
            "direction": "SELL",
            "wave1_start": wave1_start,
            "wave1_end": wave1_end,
            "wave2_end": wave2_end,
            "wave1_length": wave1_length,
            "wave2_retrace": wave2_retrace,
            "wave3_target": wave3_target,
            "wave2_time": s3["time"],
            "wave2_index": s3["index"],
        }

    return None


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION (for live bot)
# ══════════════════════════════════════════════════════════════════════════════

def get_elliott_signal(
    df_m15: pd.DataFrame,
    htf_trend: str,
    pip_size: float,
) -> Optional[dict]:
    """
    Generate Elliott Wave signal from M15 data.

    Args:
        df_m15: M15 OHLC DataFrame
        htf_trend: "UPTREND" or "DOWNTREND" from HTF filter
        pip_size: pip size for the symbol

    Returns:
        Signal dict with type, entry, sl, tp, strategy or None
    """
    if not getattr(config, "ENABLE_ELLIOTT_WAVE", False):
        return None

    depth = getattr(config, "EW_ZIGZAG_DEPTH", 8)
    min_wave1_pips = getattr(config, "EW_MIN_WAVE1_PIPS", 10.0)

    swings = zigzag(df_m15, depth=depth, deviation=0.01)

    if len(swings) < 3:
        return None

    # Only trade with the trend
    if htf_trend == "UPTREND":
        pattern = detect_wave12(swings, "BULLISH")
    elif htf_trend == "DOWNTREND":
        pattern = detect_wave12(swings, "BEARISH")
    else:
        return None

    if pattern is None:
        return None

    # Validate minimum wave 1 size
    if pattern["wave1_length"] / pip_size < min_wave1_pips:
        return None

    entry = pattern["wave2_end"]
    sl_buffer = getattr(config, "SL_BUFFER_PIPS", 3.0) * pip_size

    if pattern["direction"] == "BUY":
        sl = pattern["wave1_start"] - sl_buffer  # Below Wave 1 start
        tp = pattern["wave3_target"]
    else:
        sl = pattern["wave1_start"] + sl_buffer  # Above Wave 1 start
        tp = pattern["wave3_target"]

    # RR check
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return None
    rr = reward / risk
    if rr < getattr(config, "MIN_RISK_REWARD_RATIO", 2.0):
        return None

    return {
        "type": pattern["direction"],
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "strategy": "ELLIOTT",
        "wave1_length": pattern["wave1_length"],
        "wave2_retrace": pattern["wave2_retrace"],
        "rr": rr,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BACKTEST HELPER (stateless, works with DataFrames)
# ══════════════════════════════════════════════════════════════════════════════

def detect_elliott_bt(
    df_m15: pd.DataFrame,
    ts,
    htf_trend: str,
    pip_size: float,
) -> Optional[dict]:
    """
    Elliott Wave detection for backtesting.
    Uses M15 data up to timestamp ts.
    """
    if not getattr(config, "ENABLE_ELLIOTT_WAVE", True):
        return None

    depth = getattr(config, "EW_ZIGZAG_DEPTH", 8)
    min_wave1_pips = getattr(config, "EW_MIN_WAVE1_PIPS", 10.0)
    lookback = getattr(config, "EW_LOOKBACK_BARS", 120)

    # Get M15 data up to current time
    m15_slice = df_m15.loc[:ts].tail(lookback)
    if len(m15_slice) < depth * 3:
        return None

    swings = zigzag(m15_slice, depth=depth, deviation=0.01)

    if len(swings) < 3:
        return None

    # Only trade with the trend
    if htf_trend == "UPTREND":
        pattern = detect_wave12(swings, "BULLISH")
    elif htf_trend == "DOWNTREND":
        pattern = detect_wave12(swings, "BEARISH")
    else:
        return None

    if pattern is None:
        return None

    # Validate minimum wave 1 size
    if pattern["wave1_length"] / pip_size < min_wave1_pips:
        return None

    # Use current close as entry (price near wave 2 end)
    current_close = m15_slice.iloc[-1]["close"]

    # Entry must be near wave 2 end (within 30% of wave 1 length)
    proximity = abs(current_close - pattern["wave2_end"]) / pattern["wave1_length"]
    if proximity > 0.3:
        return None  # Price has moved too far from wave 2 completion

    entry = current_close
    sl_buffer = getattr(config, "SL_BUFFER_PIPS", 3.0) * pip_size

    if pattern["direction"] == "BUY":
        sl = pattern["wave1_start"] - sl_buffer
        tp = pattern["wave3_target"]
    else:
        sl = pattern["wave1_start"] + sl_buffer
        tp = pattern["wave3_target"]

    # RR check
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return None
    rr = reward / risk
    if rr < getattr(config, "MIN_RISK_REWARD_RATIO", 2.0):
        return None

    # SL pips check
    sl_pips = risk / pip_size
    max_sl = getattr(config, "EW_MAX_SL_PIPS", 50.0)
    if sl_pips < 3.0 or sl_pips > max_sl:
        return None

    return {
        "type": pattern["direction"],
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "strategy": "ELLIOTT",
    }
