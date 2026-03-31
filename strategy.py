"""
Forex Liquidity Hunter - Strategy Module (V18 Disciplined Trader)
=================================================================
Implements 3 parallel strategies with full validation:
  1. SMC Liquidity Sweep (High Precision)
  2. Session Breakout (Aggressive Momentum)
  3. RSI Scalper (Mean Reversion)

All signals must pass the 6-point pre-entry validation gate
via market_filter.validate_entry() before being returned.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config
import mt5_bridge
import market_filter

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
    rr_ratio: float      # Risk-Reward ratio
    reason: str


# ======================================================================
# Step 1: Identify the Session Range
# ======================================================================

def identify_session_range(
    symbol: str,
    range_hours: int = 8,
) -> Optional[dict]:
    """Get the High / Low of the preceding session range."""
    candles_needed = (range_hours * 60) // config.RANGE_TIMEFRAME_MINUTES
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.RANGE_TIMEFRAME_MINUTES,
        count=candles_needed + 5,
    )

    if df is None or df.empty:
        return None

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
    """Check if price pushed beyond the session range."""
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None: return None

    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    threshold = config.SWEEP_THRESHOLD_PIPS * pip_size

    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=1, count=30)
    if df is None or df.empty: return None

    recent_high = df["high"].max()
    recent_low = df["low"].min()

    if recent_high >= session_high + threshold:
        return {"type": "HIGH_SWEPT", "extreme": recent_high}
    if recent_low <= session_low - threshold:
        return {"type": "LOW_SWEPT", "extreme": recent_low}
    return None


# ======================================================================
# Step 3: FVG and Rejection Detection (SMC Confirmation)
# ======================================================================

def detect_fvg_entry(symbol: str, sweep_data: dict) -> Optional[dict]:
    """Look for an FVG forming after the sweep."""
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=config.SCAN_TIMEFRAME_MINUTES, count=10)
    if df is None or len(df) < 5: return None
    
    sym_info = mt5_bridge.get_symbol_info(symbol)
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    min_fvg_size = config.FVG_MIN_SIZE_PIPS * pip_size
    
    current_price = mt5_bridge.get_current_price(symbol)
    if current_price is None: return None

    for i in range(len(df) - 3, 0, -1):
        c0, c1, c2 = df.iloc[i-1], df.iloc[i], df.iloc[i+1]
        
        if sweep_data["type"] == "HIGH_SWEPT":
            gap = c0["low"] - c2["high"]
            if gap >= min_fvg_size:
                fvg_top, fvg_bottom = c0["low"], c2["high"]
                target_entry = (fvg_top + fvg_bottom) / 2.0 if getattr(config, "USE_FVG_50_ENTRY", False) else fvg_bottom
                if target_entry <= current_price["ask"] <= fvg_top + (2 * pip_size):
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": current_price["ask"]}
                    
        elif sweep_data["type"] == "LOW_SWEPT":
            gap = c2["low"] - c0["high"]
            if gap >= min_fvg_size:
                fvg_top, fvg_bottom = c2["low"], c0["high"]
                target_entry = (fvg_top + fvg_bottom) / 2.0 if getattr(config, "USE_FVG_50_ENTRY", False) else fvg_top
                if fvg_bottom - (2 * pip_size) <= current_price["bid"] <= target_entry:
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": current_price["bid"]}
    return None


# ======================================================================
# Step 4: Breakout Strategy (Momentum)
# ======================================================================

def detect_breakout(symbol: str, session_high: float, session_low: float) -> Optional[dict]:
    if not getattr(config, "ENABLE_BREAKOUT", False):
        return None
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=5, count=4)
    if df is None or len(df) < 4: return None
    last_candle, prev_candle = df.iloc[-1], df.iloc[-2]
    
    if last_candle["close"] > session_high and prev_candle["close"] > session_high:
        sl = session_low
        tp = last_candle["close"] + (last_candle["close"] - session_low) * config.TP_RATIO
        return {"type": "BREAKOUT_BUY", "entry": last_candle["close"], "sl": sl, "tp": tp}
    if last_candle["close"] < session_low and prev_candle["close"] < session_low:
        sl = session_high
        tp = last_candle["close"] - (session_high - last_candle["close"]) * config.TP_RATIO
        return {"type": "BREAKOUT_SELL", "entry": last_candle["close"], "sl": sl, "tp": tp}
    return None


# ======================================================================
# Step 5: RSI Scalping Strategy (Mean Reversion)
# ======================================================================

def detect_rsi_scalp(symbol: str) -> Optional[dict]:
    if not getattr(config, "ENABLE_RSI_SCALP", False):
        return None
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=5, count=30)
    if df is None or len(df) < 20: return None
    
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1+rs))
    
    last_rsi, last_candle = df["rsi"].iloc[-1], df.iloc[-1]
    sym_info = mt5_bridge.get_symbol_info(symbol)
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    
    if last_rsi < config.RSI_OS:
        sl_price = last_candle["low"] - (config.SL_BUFFER_PIPS * pip_size)
        tp_price = last_candle["close"] + (last_candle["close"] - sl_price) * config.TP_RATIO
        return {"type": "RSI_OS_BUY", "entry": last_candle["close"], "sl": sl_price, "tp": tp_price}
    if last_rsi > config.RSI_OB:
        sl_price = last_candle["high"] + (config.SL_BUFFER_PIPS * pip_size)
        tp_price = last_candle["close"] - (sl_price - last_candle["close"]) * config.TP_RATIO
        return {"type": "RSI_OB_SELL", "entry": last_candle["close"], "sl": sl_price, "tp": tp_price}
    return None


# ======================================================================
# Helper: Calculate RR ratio
# ======================================================================

def _calc_rr_ratio(entry: float, sl: float, tp: float) -> float:
    """Calculate Risk-Reward ratio from entry, SL, TP."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return 0.0
    return reward / risk


# ======================================================================
# Step 6: Main Signal Generator (Orchestrator)
# ======================================================================

def generate_signal(symbol: str, risk_manager=None) -> Optional[Signal]:
    """
    Master signal generator. Runs all strategy engines and validates
    each signal through the 6-point pre-entry logic gate.
    """
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None: return None
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    
    # Spread Check
    spread_pips = (sym_info.spread * sym_info.point) / pip_size
    if spread_pips > config.MAX_SPREAD_PIPS: return None

    # Session Range (needed for SMC + Breakout)
    range_data = identify_session_range(symbol)
    if range_data is None: return None

    # --- Collect candidate signals from all strategies ---
    candidates = []

    # --- STRATEGY A: SMC Sweep ---
    if getattr(config, "ENABLE_SMC_SWEEP", True):
        sweep = detect_sweep(symbol, range_data["high"], range_data["low"])
        if sweep:
            # Only proceed if sweep is counter-trend (reversal setup)
            htf_trend = market_filter.get_htf_trend(symbol)
            should_proceed = True
            if htf_trend:
                if sweep["type"] == "HIGH_SWEPT" and htf_trend == "UPTREND":
                    should_proceed = False  # Don't sell in uptrend
                elif sweep["type"] == "LOW_SWEPT" and htf_trend == "DOWNTREND":
                    should_proceed = False  # Don't buy in downtrend

            if should_proceed:
                entry_data = detect_fvg_entry(symbol, sweep)
                if entry_data:
                    direction = "BUY" if sweep["type"] == "LOW_SWEPT" else "SELL"
                    entry = entry_data["fvg_entry"]
                    sl = entry_data["wick_tip"]
                    sl_dist = abs(entry - sl)
                    tp = entry + sl_dist * config.TP_RATIO if direction == "BUY" else entry - sl_dist * config.TP_RATIO
                    sl_pips = sl_dist / pip_size
                    rr = _calc_rr_ratio(entry, sl, tp)

                    candidates.append(Signal(
                        symbol, direction, entry, sl, tp,
                        sl_pips, rr, f"SMC: {sweep['type']} + FVG"
                    ))

    # --- STRATEGY B: Breakout ---
    if getattr(config, "ENABLE_BREAKOUT", False):
        brut = detect_breakout(symbol, range_data["high"], range_data["low"])
        if brut:
            direction = "BUY" if "BUY" in brut["type"] else "SELL"
            sl_pips = abs(brut["entry"] - brut["sl"]) / pip_size
            rr = _calc_rr_ratio(brut["entry"], brut["sl"], brut["tp"])
            candidates.append(Signal(
                symbol, direction, brut["entry"], brut["sl"], brut["tp"],
                sl_pips, rr, f"Momentum: Session {brut['type']}"
            ))

    # --- STRATEGY C: RSI Scalp ---
    if getattr(config, "ENABLE_RSI_SCALP", False):
        rsi_s = detect_rsi_scalp(symbol)
        if rsi_s:
            direction = "BUY" if "BUY" in rsi_s["type"] else "SELL"
            sl_pips = abs(rsi_s["entry"] - rsi_s["sl"]) / pip_size
            rr = _calc_rr_ratio(rsi_s["entry"], rsi_s["sl"], rsi_s["tp"])
            candidates.append(Signal(
                symbol, direction, rsi_s["entry"], rsi_s["sl"], rsi_s["tp"],
                sl_pips, rr, f"Scalp: {rsi_s['type']}"
            ))

    # --- Validate each candidate through the 6-point gate ---
    for signal in candidates:
        # --- Minimum SL distance check ---
        # Wider SL = smaller lot size = same dollar risk
        min_sl = getattr(config, "MIN_SL_PIPS_XAU", 50.0) if "XAU" in symbol else \
                 getattr(config, "MIN_SL_PIPS", 15.0)

        if signal.sl_pips < min_sl:
            # Widen SL to minimum and recalculate TP to maintain RR
            old_sl_pips = signal.sl_pips
            sl_dist_new = min_sl * pip_size

            if signal.direction == "BUY":
                signal.stop_loss = signal.entry_price - sl_dist_new
                signal.take_profit = signal.entry_price + sl_dist_new * config.TP_RATIO
            else:
                signal.stop_loss = signal.entry_price + sl_dist_new
                signal.take_profit = signal.entry_price - sl_dist_new * config.TP_RATIO

            signal.sl_pips = min_sl
            signal.rr_ratio = _calc_rr_ratio(signal.entry_price, signal.stop_loss, signal.take_profit)

            logger.info(
                f"[SL-WIDEN] {symbol}: SL widened {old_sl_pips:.1f} -> {min_sl:.1f} pips "
                f"(lot size will auto-adjust, risk stays same)"
            )

        # RR pre-check (quick reject)
        if signal.rr_ratio < config.MIN_RISK_REWARD_RATIO:
            logger.info(
                f"[REJECT] {symbol} {signal.reason}: "
                f"RR {signal.rr_ratio:.2f} < {config.MIN_RISK_REWARD_RATIO}"
            )
            continue

        # Full 6-point validation
        valid, reason = market_filter.validate_entry(
            symbol=signal.symbol,
            direction=signal.direction,
            rr_ratio=signal.rr_ratio,
            risk_manager=risk_manager,
        )

        if valid:
            # Append the detailed entry conditions to the signal reason for Telegram
            signal.reason = f"{signal.reason}\n   └ <i>{reason}</i>"

            logger.info(
                f"[SIGNAL] {symbol} {signal.direction} via {signal.reason} "
                f"(RR={signal.rr_ratio:.2f}, SL={signal.sl_pips:.1f} pips)"
            )
            return signal
        else:
            logger.info(
                f"[REJECT] {symbol} {signal.reason}: {reason}"
            )

    return None
