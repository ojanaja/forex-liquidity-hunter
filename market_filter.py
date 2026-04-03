"""
Forex Liquidity Hunter – Market Filter Module (V19 Quant)
==========================================================
Central module for pre-entry market condition checks:
    1. HTF trend analysis (EMA 50/200 + market structure)
    2. Sideways detection (ATR + Bollinger squeeze)
    3. Quant confirmations (trend/volatility/momentum statistics)
    4. Master validation gate
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
    df["ema_fast"] = df["close"].ewm(
        span=config.HTF_EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(
        span=config.HTF_EMA_SLOW, adjust=False).mean()

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

    # Consensus: EMA is primary, structure only vetoes on active contradiction
    if ema_trend == structure:
        logger.debug(
            f"[HTF] {symbol}: {ema_trend} (EMA + Structure confirmed)")
        return ema_trend
    elif structure == "SIDEWAYS":
        # EMA has a direction, structure unclear → trust EMA
        logger.debug(
            f"[HTF] {symbol}: {ema_trend} (EMA primary, structure unclear)")
        return ema_trend
    elif ema_trend == "SIDEWAYS":
        # EMA flat, structure has direction → trust structure
        logger.debug(
            f"[HTF] {symbol}: {structure} (Structure primary, EMA flat)")
        return structure
    else:
        # Active contradiction (UP vs DOWN)
        logger.debug(
            f"[HTF] {symbol}: SIDEWAYS (EMA={ema_trend}, Structure={structure} — conflict)"
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

    Returns: tuple with number of confirmations (0-4) and list of reason strings
    """
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.LTF_TIMEFRAME_MINUTES,
        count=30,
    )

    if df is None or len(df) < 20:
        return 0, []

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
    return confirmations, reasons


def get_quant_confirmations(symbol: str, direction: str) -> tuple[int, list[str]]:
    """
    Quant confirmation layer using statistical metrics.

    Checks:
    1. EMA spread normalized by ATR aligns with direction
    2. Momentum spread z-score aligns with direction
    3. Volatility regime is not overheated (vol_short/vol_long <= threshold)
    4. Volume z-score indicates sufficient participation
    """
    tf = getattr(config, "QUANT_TIMEFRAME_MINUTES",
                 config.LTF_TIMEFRAME_MINUTES)
    bars = max(220, getattr(config, "QUANT_LOOKBACK_BARS", 320) // 2)
    df = mt5_bridge.get_ohlc(symbol, timeframe_minutes=tf, count=bars)

    if df is None or len(df) < 150:
        return 0, ["Quant data unavailable"]

    close = df["close"]
    returns = close.pct_change()
    reasons: list[str] = []
    confirms = 0

    # 1) Trend factor
    ema_fast = close.ewm(span=getattr(
        config, "QUANT_EMA_FAST", 20), adjust=False).mean()
    ema_slow = close.ewm(span=getattr(
        config, "QUANT_EMA_SLOW", 80), adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=getattr(
        config, "QUANT_ATR_PERIOD", 14)).mean().iloc[-1]

    if not pd.isna(atr) and atr > 0:
        trend_norm = (ema_fast.iloc[-1] - ema_slow.iloc[-1]) / atr
        if (direction == "BUY" and trend_norm > 0) or (direction == "SELL" and trend_norm < 0):
            confirms += 1
            reasons.append(f"TrendNorm={trend_norm:+.2f}")

    # 2) Momentum spread z-score
    mom_short = close.pct_change(
        getattr(config, "QUANT_MOMENTUM_SHORT_BARS", 12))
    mom_long = close.pct_change(
        getattr(config, "QUANT_MOMENTUM_LONG_BARS", 48))
    spread = (mom_short - mom_long).dropna()
    z_window = getattr(config, "QUANT_ZSCORE_WINDOW", 80)
    mom_z = 0.0
    if len(spread) >= z_window:
        mean = spread.rolling(window=z_window).mean().iloc[-1]
        std = spread.rolling(window=z_window).std().iloc[-1]
        if not pd.isna(std) and std > 0:
            mom_z = float((spread.iloc[-1] - mean) / std)

    if (direction == "BUY" and mom_z > 0) or (direction == "SELL" and mom_z < 0):
        confirms += 1
        reasons.append(f"MomZ={mom_z:+.2f}")

    # 3) Volatility regime
    vol_short = returns.rolling(window=getattr(
        config, "QUANT_VOL_SHORT_WINDOW", 24)).std().iloc[-1]
    vol_long = returns.rolling(window=getattr(
        config, "QUANT_VOL_LONG_WINDOW", 96)).std().iloc[-1]
    if not pd.isna(vol_short) and not pd.isna(vol_long) and vol_long > 0:
        vol_ratio = float(vol_short / vol_long)
        if vol_ratio <= 1.35:
            confirms += 1
            reasons.append(f"VolRatio={vol_ratio:.2f}")

    # 4) Volume participation
    if "tick_volume" in df.columns:
        vol_series = df["tick_volume"].astype(float)
        vol_mean = vol_series.rolling(window=60).mean().iloc[-1]
        vol_std = vol_series.rolling(window=60).std().iloc[-1]
        if not pd.isna(vol_mean) and not pd.isna(vol_std) and vol_std > 0:
            vol_z = float((vol_series.iloc[-1] - vol_mean) / vol_std)
            if vol_z >= -0.5:
                confirms += 1
                reasons.append(f"VolumeZ={vol_z:+.2f}")

    return confirms, reasons


# ======================================================================
# 3b. Impulse Candle Detection (Anti-Momentum-Reversal)
# ======================================================================

def detect_impulse_against(symbol: str, direction: str) -> tuple[bool, str]:
    """
    Detect if recent candles show strong impulsive momentum AGAINST the
    proposed entry direction.

    An impulse candle is:
    - Body size > 2x average body (last 20 candles)
    - Body uses > 70% of total candle range (small wicks)

    Returns:
        (True, reason) if entry is BLOCKED (impulse opposes direction)
        (False, "")   if no opposing impulse detected
    """
    df = mt5_bridge.get_ohlc(
        symbol,
        timeframe_minutes=config.LTF_TIMEFRAME_MINUTES,
        count=25,
    )

    if df is None or len(df) < 20:
        return False, ""

    # Check the last 3 candles for impulse (catches impulse that just happened)
    bodies = (df["close"] - df["open"]).abs()
    avg_body = bodies.iloc[-20:-1].mean()

    if avg_body <= 0:
        return False, ""

    impulse_multiplier = getattr(config, "IMPULSE_BODY_MULTIPLIER", 2.0)

    for lookback in range(1, 4):  # Check last 3 candles
        candle = df.iloc[-lookback]
        body = abs(candle["close"] - candle["open"])
        total_range = candle["high"] - candle["low"]

        if total_range <= 0:
            continue

        body_ratio = body / total_range  # How much of the candle is body vs wick
        body_vs_avg = body / avg_body

        is_impulse = body_vs_avg >= impulse_multiplier and body_ratio >= 0.70

        if is_impulse:
            is_bullish = candle["close"] > candle["open"]

            # Block SELL into bullish impulse, block BUY into bearish impulse
            if direction == "SELL" and is_bullish:
                return True, (
                    f"Bullish impulse candle detected "
                    f"(body {body_vs_avg:.1f}x avg, {body_ratio:.0%} body ratio) — "
                    f"don't SELL into momentum"
                )
            elif direction == "BUY" and not is_bullish:
                return True, (
                    f"Bearish impulse candle detected "
                    f"(body {body_vs_avg:.1f}x avg, {body_ratio:.0%} body ratio) — "
                    f"don't BUY into momentum"
                )

    return False, ""


# ======================================================================
# 4. Master Validation Gate (7-Point Pre-Entry Check)
# ======================================================================

def validate_entry(
    symbol: str,
    direction: str,
    rr_ratio: float,
    risk_manager=None,
) -> tuple[bool, str]:
    """
    Master validation function — ALL checks must pass.

    7-Point Logic Gate:
    0. Is it safe from news? (News Blackout filter)
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
    # --- Check 0 (NEW): News Blackout ---
    if getattr(config, "ENABLE_NEWS_FILTER", False):
        from news_filter import news_filter as _news_filter
        is_blackout, news_reason = _news_filter.is_news_blackout(symbol)
        if is_blackout:
            return False, f"News blackout: {news_reason}"

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

    # Quant-first validation path
    if getattr(config, "ENABLE_ONLY_QUANT", False):
        confirms, confirm_reasons = get_quant_confirmations(symbol, direction)
        if confirms < config.MIN_CONFIRMATIONS:
            return False, (
                f"Insufficient quant confirmations: {confirms}/{config.MIN_CONFIRMATIONS}"
            )

        if rr_ratio < config.MIN_RISK_REWARD_RATIO:
            return False, (
                f"RR too low: {rr_ratio:.2f} (min {config.MIN_RISK_REWARD_RATIO})"
            )

        if risk_manager is not None:
            corr_ok, corr_reason = risk_manager.check_correlation_filter(
                symbol)
            if not corr_ok:
                return False, corr_reason

        # --- H1 Momentum Alignment ---
        h1_df = mt5_bridge.get_ohlc(
            symbol, timeframe_minutes=config.HTF_TIMEFRAME_MINUTES, count=3)
        if h1_df is not None and len(h1_df) >= 1:
            h1_candle = h1_df.iloc[-1]
            h1_bullish = h1_candle["close"] > h1_candle["open"]
            if (direction == "BUY" and not h1_bullish) or \
               (direction == "SELL" and h1_bullish):
                return False, (
                    f"H1 momentum misaligned: candle is "
                    f"{'bullish' if h1_bullish else 'bearish'} vs {direction}"
                )

        conditions_msg = (
            f"HTF Trend: {htf_trend} | "
            f"Quant confirms ({confirms}/4): {', '.join(confirm_reasons) if confirm_reasons else 'None'} | "
            f"RR: {rr_ratio:.2f} | H1 aligned"
        )
        logger.info(
            f"[VALIDATE-QUANT] {symbol} {direction}: ALL CHECKS PASSED "
            f"({conditions_msg})"
        )
        return True, conditions_msg

    # --- Check 3b: Impulse candle filter ---
    impulse_blocked, impulse_reason = detect_impulse_against(symbol, direction)
    if impulse_blocked:
        return False, impulse_reason

    # --- Check 4: LTF Confirmations ---
    confirms, confirm_reasons = get_ltf_confirmations(symbol, direction)
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

    # Build detailed condition string to pass upward
    conditions_msg = (
        f"HTF Trend: {htf_trend} | "
        f"Confirms ({confirms}/4): {', '.join(confirm_reasons) if confirm_reasons else 'None'} | "
        f"RR: {rr_ratio:.2f}"
    )

    logger.info(
        f"[VALIDATE] {symbol} {direction}: ALL CHECKS PASSED "
        f"({conditions_msg})"
    )
    return True, conditions_msg
