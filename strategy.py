"""
Forex Liquidity Hunter - Strategy Module (V17 Multi-Engine)
Implements 3 parallel strategies:
  1. SMC Liquidity Sweep (High Precision)
  2. Session Breakout (Aggressive Momentum)
  3. RSI Scalper (Mean Reversion)
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
        return {"type": "BREAKOUT_BUY", "entry": last_candle["close"], "sl": session_low, "tp": last_candle["close"] + (last_candle["close"] - session_low) * config.TP_RATIO}
    if last_candle["close"] < session_low and prev_candle["close"] < session_low:
        return {"type": "BREAKOUT_SELL", "entry": last_candle["close"], "sl": session_high, "tp": last_candle["close"] - (session_high - last_candle["close"]) * config.TP_RATIO}
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
        return {"type": "RSI_OS_BUY", "entry": last_candle["close"], "sl": sl_price, "tp": last_candle["close"] + (last_candle["close"] - sl_price) * config.TP_RATIO}
    if last_rsi > config.RSI_OB:
        sl_price = last_candle["high"] + (config.SL_BUFFER_PIPS * pip_size)
        return {"type": "RSI_OB_SELL", "entry": last_candle["close"], "sl": sl_price, "tp": last_candle["close"] - (sl_price - last_candle["close"]) * config.TP_RATIO}
    return None


# ======================================================================
# Step 6: HTF Bias Filter
# ======================================================================

def check_htf_bias(symbol: str) -> Optional[str]:
    if not getattr(config, "USE_HTF_FILTER", False): return "NEUTRAL"
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=config.HTF_TIMEFRAME_MINUTES, count=config.HTF_EMA_PERIOD + 5)
    if df is None or len(df) < config.HTF_EMA_PERIOD: return None
    df['ema'] = df['close'].ewm(span=config.HTF_EMA_PERIOD, adjust=False).mean()
    price = mt5_bridge.get_current_price(symbol)
    if price is None: return None
    last_ema = df['ema'].iloc[-1]
    if price['ask'] > last_ema: return "BULLISH"
    elif price['bid'] < last_ema: return "BEARISH"
    return "NEUTRAL"


# ======================================================================
# Step 7: Main Signal Generator (Orchestrator)
# ======================================================================

def generate_signal(symbol: str) -> Optional[Signal]:
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None: return None
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    
    # Spread Check
    spread_pips = (sym_info.spread * sym_info.point) / pip_size
    if spread_pips > config.MAX_SPREAD_PIPS: return None

    # Bias and Range
    htf_bias = check_htf_bias(symbol)
    if htf_bias is None: return None
    range_data = identify_session_range(symbol)
    if range_data is None: return None

    # --- STRATEGY A: SMC Sweep ---
    if getattr(config, "ENABLE_SMC_SWEEP", True):
        sweep = detect_sweep(symbol, range_data["high"], range_data["low"])
        if sweep:
            if sweep["type"] == "HIGH_SWEPT" and htf_bias == "BULLISH": pass
            elif sweep["type"] == "LOW_SWEPT" and htf_bias == "BEARISH": pass
            else:
                entry_data = detect_fvg_entry(symbol, sweep)
                if entry_data:
                    sl_pips = abs(entry_data["fvg_entry"] - entry_data["wick_tip"]) / pip_size
                    return Signal(symbol, "BUY" if sweep["type"] == "LOW_SWEPT" else "SELL", 
                                  entry_data["fvg_entry"], entry_data["wick_tip"], 
                                  entry_data["fvg_entry"] + (entry_data["fvg_entry"] - entry_data["wick_tip"]) * config.TP_RATIO, 
                                  sl_pips, f"SMC: {sweep['type']} + FVG")

    # --- STRATEGY B: Breakout ---
    if getattr(config, "ENABLE_BREAKOUT", False):
        brut = detect_breakout(symbol, range_data["high"], range_data["low"])
        if brut:
            sl_pips = abs(brut["entry"] - brut["sl"]) / pip_size
            return Signal(symbol, "BUY" if "BUY" in brut["type"] else "SELL", 
                          brut["entry"], brut["sl"], brut["tp"], sl_pips, f"Momentum: Session {brut['type']}")

    # --- STRATEGY C: RSI Scalp ---
    if getattr(config, "ENABLE_RSI_SCALP", False):
        rsi_s = detect_rsi_scalp(symbol)
        if rsi_s:
            sl_pips = abs(rsi_s["entry"] - rsi_s["sl"]) / pip_size
            return Signal(symbol, "BUY" if "BUY" in rsi_s["type"] else "SELL", 
                          rsi_s["entry"], rsi_s["sl"], rsi_s["tp"], sl_pips, f"Scalp: {rsi_s['type']}")

    return None
