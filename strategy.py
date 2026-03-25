"""
Forex Liquidity Hunter - Strategy Module
Implements the Session Liquidity Sweep strategy with FVG confirmation.

Logic:
  1. Identify the preceding session's High / Low (the "range")
  2. Wait for price to "sweep" beyond that range
  3. Detect a Market Structure Shift (MSS) / Displacement leaving a Fair Value Gap (FVG)
  4. Enter the trade when price retraces into the FVG.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config
import mt5_bridge

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A trade signal produced by the strategy."""
    symbol: str
    direction: str       # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    sl_pips: float       # SL distance in pips (needed for lot sizing)
    reason: str


# ======================================================================
# Step 1: Identify the Session Range
# ======================================================================

def identify_session_range(
    symbol: str,
    range_hours: int = 8,
) -> Optional[dict]:
    """
    Get the High / Low of the preceding session range.

    We look back `range_hours` hours on M15 candles to capture
    the Asia range (for London open) or London range (for NY open).

    Returns: {"high": float, "low": float, "mid": float} or None
    """
    candles_needed = (range_hours * 60) // config.RANGE_TIMEFRAME_MINUTES
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.RANGE_TIMEFRAME_MINUTES,
        count=candles_needed + 5,  # small buffer
    )

    if df is None or df.empty:
        logger.warning(f"No range data for {symbol}")
        return None

    # Use the last `candles_needed` candles (excluding the current one)
    range_df = df.iloc[-(candles_needed + 1):-1]

    if range_df.empty:
        return None

    high = range_df["high"].max()
    low = range_df["low"].min()
    mid = (high + low) / 2.0

    return {"high": high, "low": low, "mid": mid}


# ======================================================================
# Step 2: Detect the Sweep (Liquidity Grab)
# ======================================================================

def detect_sweep(
    symbol: str,
    session_high: float,
    session_low: float,
) -> Optional[dict]:
    """
    Check if the current price has pushed beyond the session range.
    Returns the sweep type and the extreme price reached.
    """
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None

    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    threshold = config.SWEEP_THRESHOLD_PIPS * pip_size

    # Get recent M1 candles to check for sweep (last 30 mins)
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=1, count=30)
    if df is None or df.empty:
        return None

    recent_high = df["high"].max()
    recent_low = df["low"].min()

    if recent_high >= session_high + threshold:
        return {"type": "HIGH_SWEPT", "extreme": recent_high}

    if recent_low <= session_low - threshold:
        return {"type": "LOW_SWEPT", "extreme": recent_low}

    return None


# ======================================================================
# Step 3: FVG and Rejection Detection
# ======================================================================

def detect_fvg_entry(
    symbol: str,
    sweep_data: dict,
) -> Optional[dict]:
    """
    Look for an FVG forming after the sweep, and check if price is in it.
    If USE_FVG_FILTER is False, fallback to simple rejection candle.
    """
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.SCAN_TIMEFRAME_MINUTES,
        count=10, # Look at last 10 candles for FVG
    )

    if df is None or len(df) < 5:
        return None

    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None
        
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    min_fvg_size = config.FVG_MIN_SIZE_PIPS * pip_size

    current_price = mt5_bridge.get_current_price(symbol)
    if current_price is None:
        return None

    # Fallback to simple rejection if FVG filter is off
    if not getattr(config, "USE_FVG_FILTER", False):
        for idx in [-2, -1]:
            candle = df.iloc[idx]
            o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
            full_range = h - l
            if full_range <= 0:
                continue

            if sweep_data["type"] == "HIGH_SWEPT":
                body_top = max(o, c)
                wick_ratio = (h - body_top) / full_range
                if wick_ratio >= config.REJECTION_WICK_RATIO:
                    logger.info(f"🕯️ Bearish rejection found on {symbol}")
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": c}
            else:
                body_bottom = min(o, c)
                wick_ratio = (body_bottom - l) / full_range
                if wick_ratio >= config.REJECTION_WICK_RATIO:
                    logger.info(f"🕯️ Bullish rejection found on {symbol}")
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": c}
        return None


    # --- FVG DETECTION ---
    # We loop backward from the 2nd to last candle to find an FVG
    # (Candle 0, Candle 1, Candle 2). Gap is between Candle 0 and Candle 2.
    
    for i in range(len(df) - 3, 0, -1):
        c0 = df.iloc[i-1]
        c1 = df.iloc[i]
        c2 = df.iloc[i+1]
        
        if sweep_data["type"] == "HIGH_SWEPT":
            # Looking for Bearish FVG: c0's low > c2's high
            gap = c0["low"] - c2["high"]
            if gap >= min_fvg_size:
                fvg_top = c0["low"]
                fvg_bottom = c2["high"]
                
                # Check if current price has retraced into the FVG
                ask = current_price["ask"]
                if fvg_bottom <= ask <= fvg_top + (2 * pip_size):
                    logger.info(f"🎯 Bearish FVG Entry found on {symbol}! Gap size: {gap/pip_size:.1f} pips.")
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": ask}
                    
        elif sweep_data["type"] == "LOW_SWEPT":
            # Looking for Bullish FVG: c0's high < c2's low
            gap = c2["low"] - c0["high"]
            if gap >= min_fvg_size:
                fvg_top = c2["low"]
                fvg_bottom = c0["high"]
                
                # Check if current price has retraced into the FVG
                bid = current_price["bid"]
                if fvg_bottom - (2 * pip_size) <= bid <= fvg_top:
                    logger.info(f"🎯 Bullish FVG Entry found on {symbol}! Gap size: {gap/pip_size:.1f} pips.")
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": bid}

    return None


# ======================================================================
# Step 4: HTF Trend Filter
# ======================================================================

def check_htf_bias(symbol: str) -> Optional[str]:
    """
    Check the Higher Timeframe (HTF) trend direction using EMA.
    Returns: 'BULLISH', 'BEARISH', or None.
    """
    if not getattr(config, "USE_HTF_FILTER", False):
        return "NEUTRAL"
        
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=getattr(config, "HTF_TIMEFRAME_MINUTES", 60),
        count=getattr(config, "HTF_EMA_PERIOD", 20) + 5,
    )
    
    if df is None or df.empty:
        return None
        
    period = getattr(config, "HTF_EMA_PERIOD", 20)
    if len(df) < period:
        return None
        
    # Calculate EMA
    df['ema'] = df['close'].ewm(span=period, adjust=False).mean()
    
    current_price = mt5_bridge.get_current_price(symbol)
    if current_price is None:
        return None
        
    last_ema = df['ema'].iloc[-1]
    
    # Simple check: price relative to EMA
    if current_price['ask'] > last_ema:
        return "BULLISH"
    elif current_price['bid'] < last_ema:
        return "BEARISH"
        
    return "NEUTRAL"


# ======================================================================
# Step 5: Generate Signal
# ======================================================================

def generate_signal(symbol: str) -> Optional[Signal]:
    """
    Full pipeline: HTF Bias → range → sweep → FVG return → signal.
    Returns a Signal object or None.
    """
    # --- Pre-check: spread ---
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None

    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    spread_pips = (sym_info.spread * sym_info.point) / pip_size

    if spread_pips > config.MAX_SPREAD_PIPS:
        return None

    # Step 1: HTF Bias Filter
    htf_bias = check_htf_bias(symbol)
    if htf_bias is None:
        return None

    # Step 2: Get session range
    session = identify_session_range(symbol)
    if session is None:
        return None

    # Step 3: Check for sweep
    sweep_data = detect_sweep(symbol, session["high"], session["low"])
    if sweep_data is None:
        return None

    # --- Apply HTF Filter against Sweep ---
    if sweep_data["type"] == "HIGH_SWEPT" and htf_bias == "BULLISH":
        # High Swept means we want to SELL, but trend is BULLISH -> Skip
        return None
    if sweep_data["type"] == "LOW_SWEPT" and htf_bias == "BEARISH":
        # Low Swept means we want to BUY, but trend is BEARISH -> Skip
        return None

    # Step 4: Check for FVG / Rejection
    entry_data = detect_fvg_entry(symbol, sweep_data)
    if entry_data is None:
        return None

    # Step 4: Build the signal
    price = mt5_bridge.get_current_price(symbol)
    if price is None:
        return None

    if sweep_data["type"] == "HIGH_SWEPT":
        # SELL signal
        direction = "SELL"
        entry = price["bid"]
        sl = entry_data["wick_tip"] + (config.SL_BUFFER_PIPS * pip_size)
        sl_distance = sl - entry
        sl_pips = sl_distance / pip_size
        tp = entry - (sl_distance * config.TP_RATIO)

        reason = f"Session HIGH swept, FVG reentry at {entry:.5f}"

    else:  # LOW_SWEPT
        # BUY signal
        direction = "BUY"
        entry = price["ask"]
        sl = entry_data["wick_tip"] - (config.SL_BUFFER_PIPS * pip_size)
        sl_distance = entry - sl
        sl_pips = sl_distance / pip_size
        tp = entry + (sl_distance * config.TP_RATIO)

        reason = f"Session LOW swept, FVG reentry at {entry:.5f}"

    # Validate SL distance is reasonable (at least 3 pips, max 30 pips for FVG)
    if sl_pips < 3.0 or sl_pips > 30.0:
        return None

    signal = Signal(
        symbol=symbol,
        direction=direction,
        entry_price=round(entry, sym_info.digits),
        stop_loss=round(sl, sym_info.digits),
        take_profit=round(tp, sym_info.digits),
        sl_pips=round(sl_pips, 1),
        reason=reason,
    )

    logger.info(
        f"🚨 SIGNAL: {signal.direction} {signal.symbol} "
        f"@ {signal.entry_price:.5f} | "
        f"SL={signal.stop_loss:.5f} ({signal.sl_pips} pips) | "
        f"TP={signal.take_profit:.5f} | "
        f"Reason: {signal.reason}"
    )

    return signal
