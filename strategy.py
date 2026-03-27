"""
Forex Liquidity Hunter - Strategy Module (V18 Multi-Engine + Intelligence)
Implements 3 parallel strategies with Market Regime Awareness.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np

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
    sl_pips: float
    reason: str


# ======================================================================
# Step 1: Identify the Session Range
# ======================================================================

def identify_session_range(symbol: str, range_hours: int = 8) -> Optional[dict]:
    candles_needed = (range_hours * 60) // config.RANGE_TIMEFRAME_MINUTES
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=config.RANGE_TIMEFRAME_MINUTES, count=candles_needed + 5)
    if df is None or df.empty: return None
    range_df = df.iloc[-(candles_needed + 1):-1]
    if range_df.empty: return None
    high, low = range_df["high"].max(), range_df["low"].min()
    return {"high": high, "low": low, "mid": (high + low) / 2.0}


# ======================================================================
# Step 2: Detect the Sweep (Liquidity Grab)
# ======================================================================

def detect_sweep(symbol: str, session_high: float, session_low: float) -> Optional[dict]:
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None: return None
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    threshold = config.SWEEP_THRESHOLD_PIPS * pip_size
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=1, count=30)
    if df is None or df.empty: return None
    recent_high, recent_low = df["high"].max(), df["low"].min()
    if recent_high >= session_high + threshold: return {"type": "HIGH_SWEPT", "extreme": recent_high}
    if recent_low <= session_low - threshold: return {"type": "LOW_SWEPT", "extreme": recent_low}
    return None


# ======================================================================
# Step 3: Confirmation (SMC FVG)
# ======================================================================

def detect_fvg_entry(symbol: str, sweep_data: dict) -> Optional[dict]:
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
                target = (fvg_top + fvg_bottom) / 2.0 if getattr(config, "USE_FVG_50_ENTRY", False) else fvg_bottom
                if target <= current_price["ask"] <= fvg_top + (2 * pip_size): return {"wick_tip": sweep_data["extreme"], "fvg_entry": current_price["ask"]}
        elif sweep_data["type"] == "LOW_SWEPT":
            gap = c2["low"] - c0["high"]
            if gap >= min_fvg_size:
                fvg_top, fvg_bottom = c2["low"], c0["high"]
                target = (fvg_top + fvg_bottom) / 2.0 if getattr(config, "USE_FVG_50_ENTRY", False) else fvg_top
                if fvg_bottom - (2 * pip_size) <= current_price["bid"] <= target: return {"wick_tip": sweep_data["extreme"], "fvg_entry": current_price["bid"]}
    return None


# ======================================================================
# Step 4: Breakout Strategy
# ======================================================================

def detect_breakout(symbol: str, session_high: float, session_low: float) -> Optional[dict]:
    if not getattr(config, "ENABLE_BREAKOUT", False): return None
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=5, count=4)
    if df is None or len(df) < 4: return None
    last, prev = df.iloc[-1], df.iloc[-2]
    if last["close"] > session_high and prev["close"] > session_high:
        return {"type": "BREAKOUT_BUY", "entry": last["close"], "sl": session_low, "tp": last["close"] + (last["close"] - session_low) * config.TP_RATIO}
    if last["close"] < session_low and prev["close"] < session_low:
        return {"type": "BREAKOUT_SELL", "entry": last["close"], "sl": session_high, "tp": last["close"] - (session_high - last["close"]) * config.TP_RATIO}
    return None


# ======================================================================
# Step 5: RSI Scalper
# ======================================================================

def detect_rsi_scalp(symbol: str) -> Optional[dict]:
    if not getattr(config, "ENABLE_RSI_SCALP", False): return None
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=5, count=30)
    if df is None or len(df) < 20: return None
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    rs = gain / loss.replace(0, 0.1)
    df["rsi"] = 100 - (100 / (1+rs))
    last_rsi, last_c = df["rsi"].iloc[-1], df.iloc[-1]
    sym_info = mt5_bridge.get_symbol_info(symbol)
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    if last_rsi < config.RSI_OS:
        sl = last_c["low"] - (config.SL_BUFFER_PIPS * pip_size)
        return {"type": "RSI_OS_BUY", "entry": last_c["close"], "sl": sl, "tp": last_c["close"] + (last_c["close"] - sl) * config.TP_RATIO}
    if last_rsi > config.RSI_OB:
        sl = last_c["high"] + (config.SL_BUFFER_PIPS * pip_size)
        return {"type": "RSI_OB_SELL", "entry": last_c["close"], "sl": sl, "tp": last_c["close"] - (sl - last_c["close"]) * config.TP_RATIO}
    return None


# ======================================================================
# Step 6: Market Intelligence (Regime)
# ======================================================================

def detect_market_regime(symbol: str) -> str:
    """Label market as TREND_UP, TREND_DOWN, or SIDEWAYS."""
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=15, count=100)
    if df is None or len(df) < 50: return "SIDEWAYS"
    up_m, down_m = df["high"].diff(), -df["low"].diff()
    plus_dm = up_m.where((up_m > down_m) & (up_m > 0), 0.0)
    minus_dm = down_m.where((down_m > up_m) & (down_m > 0), 0.0)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=config.ADX_PERIOD).mean().replace(0, 0.1)
    plus_di = 100 * (plus_dm.rolling(window=config.ADX_PERIOD).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=config.ADX_PERIOD).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 0.1)
    adx = dx.rolling(window=config.ADX_PERIOD).mean().iloc[-1]
    df_h1 = mt5_bridge.get_ohlc(symbol, timeframe_minutes=60, count=60)
    if df_h1 is None or len(df_h1) < 50: return "SIDEWAYS"
    e20 = df_h1["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = df_h1["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    regime = "SIDEWAYS"
    if adx > config.ADX_TRENDING_THRESHOLD:
        regime = "TREND_UP" if e20 > e50 else "TREND_DOWN"
    logger.info(f"🔍 {symbol} Intelligence: {regime} (ADX: {adx:.1f})")
    return regime


# ======================================================================
# Step 7: HTF Bias Filter
# ======================================================================

def check_htf_bias(symbol: str) -> Optional[str]:
    if not getattr(config, "USE_HTF_FILTER", False): return "NEUTRAL"
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=config.HTF_TIMEFRAME_MINUTES, count=config.HTF_EMA_PERIOD + 5)
    if df is None or len(df) < config.HTF_EMA_PERIOD: return None
    df['ema'] = df['close'].ewm(span=config.HTF_EMA_PERIOD, adjust=False).mean()
    p = mt5_bridge.get_current_price(symbol)
    if p is None: return None
    if p['ask'] > df['ema'].iloc[-1]: return "BULLISH"
    elif p['bid'] < df['ema'].iloc[-1]: return "BEARISH"
    return "NEUTRAL"


# ======================================================================
# Step 8: Main Signal Generator
# ======================================================================

def generate_signal(symbol: str) -> Optional[Signal]:
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None: return None
    pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
    if (sym_info.spread * sym_info.point) / pip_size > config.MAX_SPREAD_PIPS: return None
    
    regime = detect_market_regime(symbol)
    htf_bias = check_htf_bias(symbol)
    if htf_bias is None: return None
    rg = identify_session_range(symbol)
    if rg is None: return None

    # --- Strategy Filter ---
    # SMC: Best in SIDEWAYS or matching TREND
    if getattr(config, "ENABLE_SMC_SWEEP", True):
        sw = detect_sweep(symbol, rg["high"], rg["low"])
        if sw:
            dir = "BUY" if sw["type"] == "LOW_SWEPT" else "SELL"
            if (regime == "TREND_UP" and dir == "SELL") or (regime == "TREND_DOWN" and dir == "BUY"): pass
            else:
                ent = detect_fvg_entry(symbol, sw)
                if ent:
                    sl_p = abs(ent["fvg_entry"] - ent["wick_tip"]) / pip_size
                    return Signal(symbol, dir, ent["fvg_entry"], ent["wick_tip"], ent["fvg_entry"] + (ent["fvg_entry"] - ent["wick_tip"]) * config.TP_RATIO, sl_p, f"SMC ({regime})")

    # Breakout: Only when trend matches or starting
    if getattr(config, "ENABLE_BREAKOUT", False):
        br = detect_breakout(symbol, rg["high"], rg["low"])
        if br:
            dir = "BUY" if "BUY" in br["type"] else "SELL"
            if regime != "SIDEWAYS" and regime not in br["type"]: pass
            else:
                sl_p = abs(br["entry"] - br["sl"]) / pip_size
                return Signal(symbol, dir, br["entry"], br["sl"], br["tp"], sl_p, f"Breakout ({regime})")

    # RSI: Mean Reversion -> Only in SIDEWAYS
    if getattr(config, "ENABLE_RSI_SCALP", False):
        rs = detect_rsi_scalp(symbol)
        if rs:
            dir = "BUY" if "BUY" in rs["type"] else "SELL"
            if regime == "SIDEWAYS":
                sl_p = abs(rs["entry"] - rs["sl"]) / pip_size
                return Signal(symbol, dir, rs["entry"], rs["sl"], rs["tp"], sl_p, f"Scalp ({regime})")

    return None
