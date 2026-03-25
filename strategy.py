"""
Forex Liquidity Hunter - Strategy Module
Implements the Session Liquidity Sweep strategy.

Logic:
  1. Identify the preceding session's High / Low (the "range")
  2. Wait for price to "sweep" beyond that range
  3. Detect a rejection candle (long wick closing back inside the range)
  4. Generate a SELL signal (if high was swept) or BUY signal (if low was swept)
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

    logger.debug(
        f"Session range for {symbol}: "
        f"H={high:.5f} L={low:.5f} Mid={mid:.5f} "
        f"({len(range_df)} candles)"
    )

    return {"high": high, "low": low, "mid": mid}


# ======================================================================
# Step 2: Detect the Sweep (Liquidity Grab)
# ======================================================================

def detect_sweep(
    symbol: str,
    session_high: float,
    session_low: float,
) -> Optional[str]:
    """
    Check if the current price has pushed beyond the session range.

    Returns:
      "HIGH_SWEPT" — price went above session high (look for SELL)
      "LOW_SWEPT"  — price went below session low  (look for BUY)
      None         — no sweep detected
    """
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None

    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    threshold = config.SWEEP_THRESHOLD_PIPS * pip_size

    # Get recent M1 candles to check for sweep
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=1, count=30)
    if df is None or df.empty:
        return None

    recent = df.tail(15)  # last 15 minutes
    recent_high = recent["high"].max()
    recent_low = recent["low"].min()

    if recent_high >= session_high + threshold:
        logger.info(
            f"🔴 HIGH SWEPT on {symbol}: "
            f"recent high {recent_high:.5f} > session high {session_high:.5f} "
            f"+ {config.SWEEP_THRESHOLD_PIPS} pips"
        )
        return "HIGH_SWEPT"

    if recent_low <= session_low - threshold:
        logger.info(
            f"🟢 LOW SWEPT on {symbol}: "
            f"recent low {recent_low:.5f} < session low {session_low:.5f} "
            f"- {config.SWEEP_THRESHOLD_PIPS} pips"
        )
        return "LOW_SWEPT"

    return None


# ======================================================================
# Step 3: Detect Rejection Candle
# ======================================================================

def detect_rejection(
    symbol: str,
    sweep_direction: str,
) -> Optional[dict]:
    """
    After a sweep, look for a rejection candle on the scan timeframe.

    A rejection candle has:
      - A long wick on the sweep side (> REJECTION_WICK_RATIO of the candle range)
      - Body closed back inside the session range

    Returns: {"wick_tip": float, "close": float} or None
    """
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.SCAN_TIMEFRAME_MINUTES,
        count=5,
    )

    if df is None or len(df) < 2:
        return None

    # Check the last completed candle (index -2) and current forming (index -1)
    for idx in [-2, -1]:
        candle = df.iloc[idx]
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
        full_range = h - l

        if full_range <= 0:
            continue

        body_top = max(o, c)
        body_bottom = min(o, c)

        if sweep_direction == "HIGH_SWEPT":
            # Looking for bearish rejection — long upper wick
            upper_wick = h - body_top
            wick_ratio = upper_wick / full_range

            if wick_ratio >= config.REJECTION_WICK_RATIO:
                logger.info(
                    f"🕯️ Bearish rejection on {symbol}: "
                    f"wick_ratio={wick_ratio:.2f}, wick_tip={h:.5f}"
                )
                return {"wick_tip": h, "close": c}

        elif sweep_direction == "LOW_SWEPT":
            # Looking for bullish rejection — long lower wick
            lower_wick = body_bottom - l
            wick_ratio = lower_wick / full_range

            if wick_ratio >= config.REJECTION_WICK_RATIO:
                logger.info(
                    f"🕯️ Bullish rejection on {symbol}: "
                    f"wick_ratio={wick_ratio:.2f}, wick_tip={l:.5f}"
                )
                return {"wick_tip": l, "close": c}

    return None


# ======================================================================
# Step 4: Generate Signal
# ======================================================================

def generate_signal(symbol: str) -> Optional[Signal]:
    """
    Full pipeline: range → sweep → rejection → signal.
    Returns a Signal object or None.
    """
    # --- Pre-check: spread ---
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None

    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    spread_pips = (sym_info.spread * sym_info.point) / pip_size

    if spread_pips > config.MAX_SPREAD_PIPS:
        logger.debug(
            f"Spread too wide on {symbol}: {spread_pips:.1f} pips "
            f"(max: {config.MAX_SPREAD_PIPS})"
        )
        return None

    # Step 1: Get session range
    session = identify_session_range(symbol)
    if session is None:
        return None

    # Step 2: Check for sweep
    sweep = detect_sweep(symbol, session["high"], session["low"])
    if sweep is None:
        return None

    # Step 3: Check for rejection
    rejection = detect_rejection(symbol, sweep)
    if rejection is None:
        return None

    # Step 4: Build the signal
    price = mt5_bridge.get_current_price(symbol)
    if price is None:
        return None

    if sweep == "HIGH_SWEPT":
        # SELL signal
        direction = "SELL"
        entry = price["bid"]
        sl = rejection["wick_tip"] + (config.SL_BUFFER_PIPS * pip_size)
        sl_distance = sl - entry
        sl_pips = sl_distance / pip_size
        tp = entry - (sl_distance * config.TP_RATIO)

        reason = (
            f"Session HIGH swept ({session['high']:.5f}), "
            f"bearish rejection at {rejection['wick_tip']:.5f}"
        )

    else:  # LOW_SWEPT
        # BUY signal
        direction = "BUY"
        entry = price["ask"]
        sl = rejection["wick_tip"] - (config.SL_BUFFER_PIPS * pip_size)
        sl_distance = entry - sl
        sl_pips = sl_distance / pip_size
        tp = entry + (sl_distance * config.TP_RATIO)

        reason = (
            f"Session LOW swept ({session['low']:.5f}), "
            f"bullish rejection at {rejection['wick_tip']:.5f}"
        )

    # Validate SL distance is reasonable (at least 3 pips, max 20 pips)
    if sl_pips < 3.0 or sl_pips > 20.0:
        logger.info(
            f"SL distance out of range for {symbol}: {sl_pips:.1f} pips. Skipping."
        )
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
