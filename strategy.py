"""
Forex Liquidity Hunter - Strategy Module (V19 Quant Engine)
============================================================
Primary alpha engine is a quant multi-factor model:
    1. Trend factor (EMA spread normalized by ATR)
    2. Momentum spread z-score (short-horizon vs long-horizon returns)
    3. Mean-reversion factor (price distance from rolling fair value)
    4. Volatility regime penalty (short-vol vs long-vol)

All candidate signals must pass market_filter.validate_entry().
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
    if sym_info is None:
        return None

    pip_size = sym_info.point * \
        10 if sym_info.digits in (3, 5) else sym_info.point
    threshold = config.SWEEP_THRESHOLD_PIPS * pip_size

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
# Step 3: FVG and Rejection Detection (SMC Confirmation)
# ======================================================================

def detect_fvg_entry(symbol: str, sweep_data: dict) -> Optional[dict]:
    """Look for an FVG forming after the sweep."""
    df = mt5_bridge.get_ohlc(
        symbol, timeframe_minutes=config.SCAN_TIMEFRAME_MINUTES, count=10)
    if df is None or len(df) < 5:
        return None

    sym_info = mt5_bridge.get_symbol_info(symbol)
    pip_size = sym_info.point * \
        10 if sym_info.digits in (3, 5) else sym_info.point
    min_fvg_size = config.FVG_MIN_SIZE_PIPS * pip_size

    current_price = mt5_bridge.get_current_price(symbol)
    if current_price is None:
        return None

    for i in range(len(df) - 3, 0, -1):
        c0, c1, c2 = df.iloc[i-1], df.iloc[i], df.iloc[i+1]

        if sweep_data["type"] == "HIGH_SWEPT":
            gap = c0["low"] - c2["high"]
            if gap >= min_fvg_size:
                fvg_top, fvg_bottom = c0["low"], c2["high"]
                target_entry = (fvg_top + fvg_bottom) / 2.0 if getattr(config,
                                                                       "USE_FVG_50_ENTRY", False) else fvg_bottom
                if target_entry <= current_price["ask"] <= fvg_top + (2 * pip_size):
                    return {"wick_tip": sweep_data["extreme"], "fvg_entry": current_price["ask"]}

        elif sweep_data["type"] == "LOW_SWEPT":
            gap = c2["low"] - c0["high"]
            if gap >= min_fvg_size:
                fvg_top, fvg_bottom = c2["low"], c0["high"]
                target_entry = (fvg_top + fvg_bottom) / 2.0 if getattr(config,
                                                                       "USE_FVG_50_ENTRY", False) else fvg_top
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
    if df is None or len(df) < 4:
        return None
    last_candle, prev_candle = df.iloc[-1], df.iloc[-2]

    if last_candle["close"] > session_high and prev_candle["close"] > session_high:
        sl = session_low
        tp = last_candle["close"] + \
            (last_candle["close"] - session_low) * config.TP_RATIO
        return {"type": "BREAKOUT_BUY", "entry": last_candle["close"], "sl": sl, "tp": tp}
    if last_candle["close"] < session_low and prev_candle["close"] < session_low:
        sl = session_high
        tp = last_candle["close"] - \
            (session_high - last_candle["close"]) * config.TP_RATIO
        return {"type": "BREAKOUT_SELL", "entry": last_candle["close"], "sl": sl, "tp": tp}
    return None


# ======================================================================
# Step 5: RSI Scalping Strategy (Mean Reversion)
# ======================================================================

def detect_rsi_scalp(symbol: str) -> Optional[dict]:
    if not getattr(config, "ENABLE_RSI_SCALP", False):
        return None
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=5, count=30)
    if df is None or len(df) < 20:
        return None

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1+rs))

    last_rsi, last_candle = df["rsi"].iloc[-1], df.iloc[-1]
    sym_info = mt5_bridge.get_symbol_info(symbol)
    pip_size = sym_info.point * \
        10 if sym_info.digits in (3, 5) else sym_info.point

    if last_rsi < config.RSI_OS:
        sl_price = last_candle["low"] - (config.SL_BUFFER_PIPS * pip_size)
        tp_price = last_candle["close"] + \
            (last_candle["close"] - sl_price) * config.TP_RATIO
        return {"type": "RSI_OS_BUY", "entry": last_candle["close"], "sl": sl_price, "tp": tp_price}
    if last_rsi > config.RSI_OB:
        sl_price = last_candle["high"] + (config.SL_BUFFER_PIPS * pip_size)
        tp_price = last_candle["close"] - \
            (sl_price - last_candle["close"]) * config.TP_RATIO
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


def _latest_zscore(series: pd.Series, window: int) -> float:
    """Return z-score of latest value using rolling mean/std."""
    if series is None or series.empty or window < 5 or len(series) < window:
        return 0.0

    rolling_mean = series.rolling(window=window).mean().iloc[-1]
    rolling_std = series.rolling(window=window).std().iloc[-1]
    if pd.isna(rolling_mean) or pd.isna(rolling_std) or rolling_std <= 0:
        return 0.0
    return float((series.iloc[-1] - rolling_mean) / rolling_std)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _quant_param(symbol: str, key: str, default):
    """Read quant parameter with optional per-symbol override."""
    overrides = getattr(config, "QUANT_SYMBOL_OVERRIDES", {}) or {}
    if symbol in overrides and key in overrides[symbol]:
        return overrides[symbol][key]
    return getattr(config, key, default)


def _build_quant_signal(symbol: str, pip_size: float) -> Optional[Signal]:
    """Build a candidate signal from multi-factor quant score."""
    timeframe = int(_quant_param(symbol, "QUANT_TIMEFRAME_MINUTES", 5))
    lookback = int(_quant_param(symbol, "QUANT_LOOKBACK_BARS", 320))
    df = mt5_bridge.get_ohlc(
        symbol, timeframe_minutes=timeframe, count=lookback)
    if df is None or len(df) < max(120, lookback // 2):
        return None

    close = df["close"]
    returns = close.pct_change()

    ema_fast_period = int(_quant_param(symbol, "QUANT_EMA_FAST", 20))
    ema_slow_period = int(_quant_param(symbol, "QUANT_EMA_SLOW", 80))
    atr_period = int(_quant_param(symbol, "QUANT_ATR_PERIOD", 14))

    ema_fast = close.ewm(span=ema_fast_period, adjust=False).mean()
    ema_slow = close.ewm(span=ema_slow_period, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=atr_period).mean().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None

    trend_raw = (ema_fast.iloc[-1] - ema_slow.iloc[-1]) / atr
    trend_factor = _clamp(float(trend_raw), -3.0, 3.0) / 3.0

    # Reject if trend is too weak
    min_trend = float(_quant_param(symbol, "QUANT_MIN_TREND_STRENGTH", 0.15))
    if abs(trend_factor) < min_trend:
        return None

    mom_short_bars = int(_quant_param(symbol, "QUANT_MOMENTUM_SHORT_BARS", 12))
    mom_long_bars = int(_quant_param(symbol, "QUANT_MOMENTUM_LONG_BARS", 48))
    z_window = int(_quant_param(symbol, "QUANT_ZSCORE_WINDOW", 80))

    mom_short = close.pct_change(mom_short_bars)
    mom_long = close.pct_change(mom_long_bars)
    mom_spread = (mom_short - mom_long).dropna()
    momentum_factor = _clamp(
        _latest_zscore(mom_spread, z_window),
        -3.0,
        3.0,
    ) / 3.0

    # Reject if momentum is too weak
    min_mom = float(_quant_param(symbol, "QUANT_MIN_MOM_ZSCORE", 0.3))
    mom_z_raw = _latest_zscore(mom_spread, z_window)
    if abs(mom_z_raw) < min_mom:
        return None

    mean_window = int(_quant_param(symbol, "QUANT_MEAN_WINDOW", 60))
    rolling_mean = close.rolling(window=mean_window).mean()
    rolling_std = close.rolling(window=mean_window).std()
    if pd.isna(rolling_std.iloc[-1]) or rolling_std.iloc[-1] <= 0:
        return None
    mr_raw = (close.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1]
    mean_reversion_factor = _clamp(float(-mr_raw), -3.0, 3.0) / 3.0

    vol_short_window = int(_quant_param(symbol, "QUANT_VOL_SHORT_WINDOW", 24))
    vol_long_window = int(_quant_param(symbol, "QUANT_VOL_LONG_WINDOW", 96))
    vol_short = returns.rolling(window=vol_short_window).std().iloc[-1]
    vol_long = returns.rolling(window=vol_long_window).std().iloc[-1]
    if pd.isna(vol_short) or pd.isna(vol_long) or vol_long <= 0:
        return None

    vol_ratio = float(vol_short / vol_long)
    vol_penalty = max(0.0, vol_ratio - 1.0)

    max_vol_ratio = float(_quant_param(symbol, "QUANT_MAX_VOL_RATIO", 1.15))
    if vol_ratio > max_vol_ratio:
        return None

    w_trend = float(_quant_param(symbol, "QUANT_W_TREND", 0.45))
    w_mom = float(_quant_param(symbol, "QUANT_W_MOMENTUM", 0.35))
    w_mr = float(_quant_param(symbol, "QUANT_W_MEAN_REVERSION", 0.20))
    raw_score = (w_trend * trend_factor) + (w_mom * momentum_factor) + \
        (w_mr * mean_reversion_factor)

    penalty = float(_quant_param(symbol,
                                 "QUANT_W_VOL_PENALTY", 0.25)) * vol_penalty

    if raw_score > 0:
        score = max(0.0, raw_score - penalty)
    else:
        score = min(0.0, raw_score + penalty)

    if bool(_quant_param(symbol, "QUANT_REQUIRE_TREND_MOM_ALIGNMENT", True)):
        directional = 1.0 if score > 0 else -1.0
        if score == 0.0:
            return None
        if (trend_factor * directional) <= 0 or (momentum_factor * directional) <= 0:
            return None

    threshold = float(_quant_param(
        symbol, "QUANT_SCORE_ENTRY_THRESHOLD", 0.20))
    if abs(score) < threshold:
        return None

    prices = mt5_bridge.get_current_price(symbol)
    if prices is None:
        return None

    direction = "BUY" if score > 0 else "SELL"

    # ── Strategy Filter 1: M5 Trend Consistency ──
    # Require 50%+ of last 10 closes on correct side of fast EMA
    last_10_close = close.iloc[-10:]
    last_10_ema = ema_fast.iloc[-10:]
    if direction == "BUY":
        trend_support = float((last_10_close > last_10_ema).sum()) / 10.0
    else:
        trend_support = float((last_10_close < last_10_ema).sum()) / 10.0
    if trend_support < 0.50:
        return None

    # ── Strategy Filter 2: Volatility Spike Rejection ──
    candle_ranges = (df["high"] - df["low"]).iloc[-20:]
    current_range = float(candle_ranges.iloc[-1])
    avg_range = float(candle_ranges.iloc[:-1].mean())
    if avg_range > 0 and current_range > 2.5 * avg_range:
        return None

    # ── Strategy Filter 3: EMA Slope Validation ──
    ema_now = float(ema_fast.iloc[-1])
    ema_5ago = float(ema_fast.iloc[-6]) if len(ema_fast) >= 6 else ema_now
    ema_slope = ema_now - ema_5ago
    if direction == "BUY" and ema_slope <= 0:
        return None
    if direction == "SELL" and ema_slope >= 0:
        return None

    entry = prices["ask"] if direction == "BUY" else prices["bid"]

    sl_atr = float(_quant_param(
        symbol, "QUANT_ATR_SL_MULTIPLIER", 1.8)) * float(atr)
    min_sl = getattr(config, "MIN_SL_PIPS_XAU", 50.0) if "XAU" in symbol else getattr(
        config, "MIN_SL_PIPS", 15.0)
    sl_distance = max(sl_atr, min_sl * pip_size)

    rr_target = float(_quant_param(
        symbol, "QUANT_TP_R_MULTIPLIER", config.TP_RATIO))
    if direction == "BUY":
        stop_loss = entry - sl_distance
        take_profit = entry + (sl_distance * rr_target)
    else:
        stop_loss = entry + sl_distance
        take_profit = entry - (sl_distance * rr_target)

    sl_pips = sl_distance / pip_size
    rr = _calc_rr_ratio(entry, stop_loss, take_profit)

    reason = (
        f"QuantMF score={score:+.3f} "
        f"(trend={trend_factor:+.2f}, mom={momentum_factor:+.2f}, "
        f"mr={mean_reversion_factor:+.2f}, vol_ratio={vol_ratio:.2f})"
    )

    return Signal(
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        sl_pips=sl_pips,
        rr_ratio=rr,
        reason=reason,
    )


# ======================================================================
# Step 6: Main Signal Generator (Orchestrator)
# ======================================================================

def generate_signal(symbol: str, risk_manager=None) -> Optional[Signal]:
    """
    Master signal generator. Runs all strategy engines and validates
    each signal through the 6-point pre-entry logic gate.
    """
    sym_info = mt5_bridge.get_symbol_info(symbol)
    if sym_info is None:
        return None
    pip_size = sym_info.point * \
        10 if sym_info.digits in (3, 5) else sym_info.point

    # Spread Check
    spread_pips = (sym_info.spread * sym_info.point) / pip_size
    if spread_pips > config.MAX_SPREAD_PIPS:
        return None

    candidates = []
    quant_signal = _build_quant_signal(symbol, pip_size)
    if quant_signal is not None:
        candidates.append(quant_signal)

    # --- Validate each candidate through the master gate ---
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
            signal.rr_ratio = _calc_rr_ratio(
                signal.entry_price, signal.stop_loss, signal.take_profit)

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

        # Full validation
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
