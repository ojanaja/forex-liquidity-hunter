"""
Forex Liquidity Hunter - Backtester V18 (Realistic Simulation)
===============================================================
Mirrors the LIVE bot logic as closely as possible:
  - All 3 strategy engines (SMC Sweep, Breakout, RSI Scalp)
  - HTF Trend Filter (EMA 50/200 + Market Structure)
  - Sideways Detection (ATR + Bollinger Band Squeeze)
  - LTF Confirmations (RSI, Engulfing, Volume)
  - Minimum Risk-Reward Ratio validation
  - Correlation Filter (max 1 per group)
  - Hybrid TP Checkpoint (partial close + trailing SL)
  - Net Profit = Gross - (Commission + Spread estimate)
  - Concurrent trade limit (MAX_OPEN_TRADES)

Usage:
    python backtest.py
"""
import logging
from collections import deque
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import calendar

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── Settings ─────────────────────────────────────────────────────────────────
BROKER_TO_WIB = 4
ACCOUNT_BALANCE = config.ACCOUNT_BALANCE
RISK_PER_TRADE = config.ACCOUNT_BALANCE * config.MAX_RISK_PER_TRADE_PCT / 100.0
COMMISSION_PER_LOT = getattr(config, "ESTIMATED_COMMISSION_PER_LOT", 7.0)
SPREAD_COST_PIPS = getattr(config, "ESTIMATED_SPREAD_COST_PIPS", 1.5)


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    """Represents an open trade in the backtest."""
    ticket: int
    symbol: str
    trade_type: str          # "BUY" or "SELL"
    entry: float
    sl: float
    original_sl: float
    tp: float
    risk_distance: float     # 1R in price
    pip_size: float
    strategy: str            # "SMC", "BREAKOUT", "RSI"
    entry_time: object       # timestamp
    original_volume: float = 1.0
    remaining_volume_pct: float = 1.0  # Track partial close %
    checkpoints_hit: list = field(default_factory=lambda: [False, False, False])
    trailing_active: bool = False
    trailing_extreme: float = 0.0  # High watermark (BUY) or low watermark (SELL)


@dataclass
class ClosedTrade:
    """Result of a closed trade."""
    time: object
    symbol: str
    trade_type: str
    strategy: str
    gross_pnl: float
    net_pnl: float
    partial_exits: list = field(default_factory=list)


# ─── MT5 Init ─────────────────────────────────────────────────────────────────

def initialize_mt5():
    if not MT5_AVAILABLE:
        return False
    kwargs = {
        "login": config.MT5_LOGIN,
        "password": config.MT5_PASSWORD,
        "server": config.MT5_SERVER,
    }
    if config.MT5_PATH:
        kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs):
        return False
    return True


def get_symbol_data(symbol, days_back=365):
    """Fetch M5, H1, and M15 data for comprehensive backtesting."""
    end = datetime.now()
    start = end - timedelta(days=days_back)

    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, start, end)
    rates_m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)

    if rates_m5 is None or rates_h1 is None:
        return None, None, None

    df_m5 = pd.DataFrame(rates_m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
    df_m5.set_index("time", inplace=True)

    df_h1 = pd.DataFrame(rates_h1)
    df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")
    df_h1.set_index("time", inplace=True)

    df_m15 = None
    if rates_m15 is not None:
        df_m15 = pd.DataFrame(rates_m15)
        df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s")
        df_m15.set_index("time", inplace=True)

    return df_m5, df_h1, df_m15


# ══════════════════════════════════════════════════════════════════════════════
# HTF TREND FILTER (EMA 50/200 + Market Structure)
# ══════════════════════════════════════════════════════════════════════════════

def compute_htf_trend(df_h1: pd.DataFrame, ts) -> str:
    """
    Mirrors market_filter.get_htf_trend() using historical H1 data.
    Returns: "UPTREND", "DOWNTREND", or "SIDEWAYS"
    """
    if not getattr(config, "USE_HTF_FILTER", False):
        return "UPTREND"

    # Get H1 data up to current time
    h1_slice = df_h1.loc[:ts].tail(max(config.HTF_EMA_SLOW, config.HTF_STRUCTURE_LOOKBACK) + 50)

    if len(h1_slice) < config.HTF_EMA_SLOW:
        return "SIDEWAYS"

    # Dual EMA
    ema_fast = h1_slice["close"].ewm(span=config.HTF_EMA_FAST, adjust=False).mean()
    ema_slow = h1_slice["close"].ewm(span=config.HTF_EMA_SLOW, adjust=False).mean()

    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        ema_trend = "UPTREND"
    elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
        ema_trend = "DOWNTREND"
    else:
        ema_trend = "SIDEWAYS"

    # Market Structure (swing highs/lows)
    lookback = min(config.HTF_STRUCTURE_LOOKBACK, len(h1_slice))
    highs = h1_slice["high"].iloc[-lookback:]
    lows = h1_slice["low"].iloc[-lookback:]

    swing_highs, swing_lows = [], []
    window = 5
    for i in range(window, len(highs) - window):
        if highs.iloc[i] == highs.iloc[i - window:i + window + 1].max():
            swing_highs.append(highs.iloc[i])
        if lows.iloc[i] == lows.iloc[i - window:i + window + 1].min():
            swing_lows.append(lows.iloc[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        structure = "SIDEWAYS"
    else:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]

        if hh and hl:
            structure = "UPTREND"
        elif lh and ll:
            structure = "DOWNTREND"
        else:
            structure = "SIDEWAYS"

    # Both must agree
    return ema_trend if ema_trend == structure else "SIDEWAYS"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEWAYS DETECTION (ATR + Bollinger Band Squeeze)
# ══════════════════════════════════════════════════════════════════════════════

def check_sideways(df_h1: pd.DataFrame, ts) -> bool:
    """Mirrors market_filter.is_sideways()"""
    bars_needed = max(config.ATR_PERIOD, config.BB_PERIOD) * 3
    h1_slice = df_h1.loc[:ts].tail(bars_needed)

    if len(h1_slice) < bars_needed:
        return False

    # ATR
    high = h1_slice["high"]
    low = h1_slice["low"]
    prev_close = h1_slice["close"].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=config.ATR_PERIOD).mean()

    current_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-config.ATR_PERIOD * 2:-config.ATR_PERIOD].mean()

    if pd.isna(current_atr) or pd.isna(avg_atr) or avg_atr <= 0:
        return False

    atr_low = current_atr < (avg_atr * config.ATR_LOW_VOLATILITY_FACTOR)

    # BB Squeeze
    sma = h1_slice["close"].rolling(window=config.BB_PERIOD).mean()
    std = h1_slice["close"].rolling(window=config.BB_PERIOD).std()
    band_width = (sma.iloc[-1] + config.BB_STD_DEV * std.iloc[-1]) - (sma.iloc[-1] - config.BB_STD_DEV * std.iloc[-1])
    last_price = h1_slice["close"].iloc[-1]

    if last_price <= 0:
        return False

    bb_squeeze = (band_width / last_price) < config.BB_SQUEEZE_THRESHOLD

    return atr_low and bb_squeeze


# ══════════════════════════════════════════════════════════════════════════════
# LTF CONFIRMATIONS (RSI, Engulfing, Volume)
# ══════════════════════════════════════════════════════════════════════════════

def count_ltf_confirmations(df_m5: pd.DataFrame, ts, direction: str) -> int:
    """Mirrors market_filter.get_ltf_confirmations()"""
    m5_slice = df_m5.loc[:ts].tail(30)
    if len(m5_slice) < 20:
        return 0

    confirmations = 0

    # 1. RSI
    delta = m5_slice["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()

    if loss.iloc[-1] != 0:
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
    else:
        rsi = 100.0

    if direction == "BUY" and rsi < config.RSI_OB:
        confirmations += 1
    elif direction == "SELL" and rsi > config.RSI_OS:
        confirmations += 1

    # 2. Engulfing
    if len(m5_slice) >= 2:
        prev, curr = m5_slice.iloc[-2], m5_slice.iloc[-1]
        if direction == "BUY":
            if prev["close"] < prev["open"] and curr["close"] > curr["open"]:
                if curr["close"] > prev["open"] and curr["open"] < prev["close"]:
                    confirmations += 1
        elif direction == "SELL":
            if prev["close"] > prev["open"] and curr["close"] < curr["open"]:
                if curr["close"] < prev["open"] and curr["open"] > prev["close"]:
                    confirmations += 1

    # 3. Rejection / Pin bar
    candle = m5_slice.iloc[-1]
    body = abs(candle["close"] - candle["open"])
    if direction == "BUY":
        lower_wick = min(candle["open"], candle["close"]) - candle["low"]
        if body > 0 and lower_wick > body * 2:
            confirmations += 1
    elif direction == "SELL":
        upper_wick = candle["high"] - max(candle["open"], candle["close"])
        if body > 0 and upper_wick > body * 2:
            confirmations += 1

    # 4. Volume spike
    if "tick_volume" in m5_slice.columns:
        avg_vol = m5_slice["tick_volume"].iloc[-20:-1].mean()
        if avg_vol > 0 and m5_slice["tick_volume"].iloc[-1] > avg_vol * 1.5:
            confirmations += 1

    return confirmations


# ══════════════════════════════════════════════════════════════════════════════
# CORRELATION FILTER
# ══════════════════════════════════════════════════════════════════════════════

def check_correlation(symbol: str, open_trades: list) -> bool:
    """Returns True if entry is allowed (no correlation conflict)."""
    groups = getattr(config, "CORRELATION_GROUPS", [])
    max_per_group = getattr(config, "MAX_POSITIONS_PER_CORRELATION_GROUP", 1)

    if not groups:
        return True

    open_symbols = set(t.symbol for t in open_trades)

    for group in groups:
        if symbol in group:
            group_count = sum(1 for s in group if s in open_symbols)
            if group_count >= max_per_group:
                return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# RSI SCALP STRATEGY (backtest version)
# ══════════════════════════════════════════════════════════════════════════════

def detect_rsi_scalp_bt(df_m5: pd.DataFrame, ts, pip_size: float):
    """Detects RSI oversold/overbought for mean reversion entry."""
    if not getattr(config, "ENABLE_RSI_SCALP", False):
        return None

    m5_slice = df_m5.loc[:ts].tail(30)
    if len(m5_slice) < 20:
        return None

    delta = m5_slice["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()

    if loss.iloc[-1] == 0:
        return None

    rs = gain.iloc[-1] / loss.iloc[-1]
    last_rsi = 100 - (100 / (1 + rs))
    last = m5_slice.iloc[-1]

    if last_rsi < config.RSI_OS:
        sl = last["low"] - config.SL_BUFFER_PIPS * pip_size
        tp = last["close"] + (last["close"] - sl) * config.TP_RATIO
        return {"type": "BUY", "entry": last["close"], "sl": sl, "tp": tp, "strategy": "RSI"}
    elif last_rsi > config.RSI_OB:
        sl = last["high"] + config.SL_BUFFER_PIPS * pip_size
        tp = last["close"] - (sl - last["close"]) * config.TP_RATIO
        return {"type": "SELL", "entry": last["close"], "sl": sl, "tp": tp, "strategy": "RSI"}

    return None


# ══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT TP MANAGEMENT (backtest version)
# ══════════════════════════════════════════════════════════════════════════════

def process_checkpoints(trade: BacktestTrade, high: float, low: float, pip_size: float) -> list:
    """
    Process checkpoint TP logic. Returns list of partial exit results.
    Each result: {"pct": fraction, "pnl": dollar_pnl}
    """
    if not getattr(config, "ENABLE_CHECKPOINT_TP", False):
        return []

    checkpoints = getattr(config, "TP_CHECKPOINTS", [1.0, 2.0, 3.0])
    partial_pcts = getattr(config, "TP_PARTIAL_CLOSE_PCTS", [0.40, 0.30, 0.00])
    trailing_step = getattr(config, "TRAILING_STEP_PIPS", 10.0) * pip_size

    partial_exits = []

    # Current price for RR calculation
    if trade.trade_type == "BUY":
        current_best = high
        current_profit_dist = current_best - trade.entry
    else:
        current_best = low
        current_profit_dist = trade.entry - current_best

    rr_achieved = current_profit_dist / trade.risk_distance if trade.risk_distance > 0 else 0

    # Process checkpoints
    for i, (r_level, close_pct) in enumerate(zip(checkpoints, partial_pcts)):
        if trade.checkpoints_hit[i]:
            continue

        if rr_achieved >= r_level:
            trade.checkpoints_hit[i] = True

            # Partial close
            if close_pct > 0 and trade.remaining_volume_pct > 0:
                exit_price = trade.entry + (trade.risk_distance * r_level) if trade.trade_type == "BUY" \
                    else trade.entry - (trade.risk_distance * r_level)
                p_pips = abs(exit_price - trade.entry) / pip_size
                pnl_portion = (p_pips / (trade.risk_distance / pip_size)) * RISK_PER_TRADE * close_pct
                # Deduct commission for this portion
                net_pnl = pnl_portion - (COMMISSION_PER_LOT * close_pct * 0.5) - (SPREAD_COST_PIPS * pip_size * close_pct)
                partial_exits.append({"pct": close_pct, "pnl": net_pnl, "checkpoint": f"TP{i+1}"})
                trade.remaining_volume_pct -= close_pct

            # Move SL
            if i == 0:
                # SL to BE + buffer
                be_buffer = SPREAD_COST_PIPS * pip_size
                if trade.trade_type == "BUY":
                    trade.sl = max(trade.sl, trade.entry + be_buffer)
                else:
                    trade.sl = min(trade.sl, trade.entry - be_buffer)
            else:
                # SL to previous checkpoint
                prev_r = checkpoints[i - 1]
                if trade.trade_type == "BUY":
                    new_sl = trade.entry + (trade.risk_distance * prev_r)
                    trade.sl = max(trade.sl, new_sl)
                else:
                    new_sl = trade.entry - (trade.risk_distance * prev_r)
                    trade.sl = min(trade.sl, new_sl)

            # After final checkpoint: enable trailing, remove TP
            if i == len(checkpoints) - 1:
                trade.trailing_active = True
                trade.trailing_extreme = current_best
                trade.tp = 0  # Remove TP — let it ride

    # Trailing SL
    if trade.trailing_active:
        if trade.trade_type == "BUY":
            if high > trade.trailing_extreme:
                trade.trailing_extreme = high
            trail_sl = trade.trailing_extreme - trailing_step
            if trail_sl > trade.sl:
                trade.sl = trail_sl
        else:
            if low < trade.trailing_extreme:
                trade.trailing_extreme = low
            trail_sl = trade.trailing_extreme + trailing_step
            if trail_sl < trade.sl:
                trade.sl = trail_sl

    return partial_exits


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

_ticket_counter = 0

def run_monthly_backtest(symbol_data_cache, start_date, end_date):
    """
    Full simulation matching live bot logic.
    Processes all symbols bar-by-bar with multi-strategy, validation, and checkpoints.
    """
    global _ticket_counter
    all_closed_trades = []
    open_trades: list[BacktestTrade] = []

    for symbol, (df_m5_all, df_h1_all, df_m15_all) in symbol_data_cache.items():
        info = mt5.symbol_info(symbol)
        if not info:
            continue

        # Slice data
        try:
            df_m5 = df_m5_all.loc[start_date:end_date]
            df_h1 = df_h1_all.loc[start_date - timedelta(days=7):end_date]
        except KeyError:
            continue

        if df_m5.empty:
            continue

        pip_size = info.point * 10 if info.digits in (3, 5) else info.point
        thresh = config.SWEEP_THRESHOLD_PIPS * pip_size
        fvg_min = config.FVG_MIN_SIZE_PIPS * pip_size
        sl_buff = config.SL_BUFFER_PIPS * pip_size
        max_sl_p = 1000.0 if "XAU" in symbol else 50.0

        range_buf = deque(maxlen=288)
        fvg_buf = deque(maxlen=24)
        active_s_h, active_s_l = None, None
        last_sweep_type = None
        sweep_expiry = datetime.min
        symbol_cooldown = datetime.min

        for ts, row in df_m5.iterrows():
            h = float(row["high"])
            l = float(row["low"])
            o = float(row["open"])
            c = float(row["close"])
            vol = float(row.get("tick_volume", 0))
            candle = {"high": h, "low": l, "open": o, "close": c}
            range_buf.append(candle)
            fvg_buf.append(candle)

            wib = ts + timedelta(hours=BROKER_TO_WIB)
            t_str = wib.strftime("%H:%M")

            # ── Manage existing trades for this symbol ──
            trades_to_close = []
            for trade in open_trades:
                if trade.symbol != symbol:
                    continue

                # Process checkpoints
                partial_results = process_checkpoints(trade, h, l, pip_size)
                for pr in partial_results:
                    all_closed_trades.append(ClosedTrade(
                        time=ts, symbol=symbol, trade_type=trade.trade_type,
                        strategy=trade.strategy,
                        gross_pnl=pr["pnl"], net_pnl=pr["pnl"],
                        partial_exits=[pr["checkpoint"]],
                    ))

                # Check SL/TP hit
                exit_price = None
                if trade.trade_type == "BUY":
                    if l <= trade.sl:
                        exit_price = trade.sl
                    elif trade.tp > 0 and h >= trade.tp:
                        exit_price = trade.tp
                else:
                    if h >= trade.sl:
                        exit_price = trade.sl
                    elif trade.tp > 0 and l <= trade.tp:
                        exit_price = trade.tp

                if exit_price is not None:
                    if trade.trade_type == "BUY":
                        p_pips = (exit_price - trade.entry) / pip_size
                    else:
                        p_pips = (trade.entry - exit_price) / pip_size

                    sl_pips = trade.risk_distance / pip_size
                    gross_pnl = (p_pips / sl_pips) * RISK_PER_TRADE * trade.remaining_volume_pct
                    # Deduct commission + spread for remaining portion
                    commission = COMMISSION_PER_LOT * trade.remaining_volume_pct * 0.5
                    spread_cost = SPREAD_COST_PIPS * pip_size * trade.remaining_volume_pct
                    net_pnl = gross_pnl - commission - spread_cost

                    all_closed_trades.append(ClosedTrade(
                        time=ts, symbol=symbol, trade_type=trade.trade_type,
                        strategy=trade.strategy,
                        gross_pnl=gross_pnl, net_pnl=net_pnl,
                    ))
                    trades_to_close.append(trade)
                    symbol_cooldown = ts + timedelta(minutes=config.TRADE_COOLDOWN_MINUTES)

            for t in trades_to_close:
                if t in open_trades:
                    open_trades.remove(t)

            # ── Skip if already have trade on this symbol ──
            if any(t.symbol == symbol for t in open_trades):
                continue

            # ── Cooldown check ──
            if ts < symbol_cooldown:
                continue

            # ── Concurrent trade limit ──
            if len(open_trades) >= config.MAX_OPEN_TRADES:
                continue

            # ── Session window check ──
            in_window = ("14:00" <= t_str <= "18:00") or ("19:00" <= t_str <= "22:59")
            if in_window and active_s_h is None and len(range_buf) >= 200:
                active_s_h = max(can["high"] for can in list(range_buf)[:-1])
                active_s_l = min(can["low"] for can in list(range_buf)[:-1])
            if not in_window:
                active_s_h, active_s_l = None, None
                last_sweep_type = None
                sweep_expiry = datetime.min
                continue
            if active_s_h is None:
                continue

            # ══════════════════════════════════════════════════
            # VALIDATION GATE (mirrors market_filter.validate_entry)
            # ══════════════════════════════════════════════════

            # 1. HTF Trend
            htf_trend = compute_htf_trend(df_h1, ts)
            if htf_trend == "SIDEWAYS":
                continue

            # 2. Sideways detection
            if check_sideways(df_h1, ts):
                continue

            # 3. Correlation filter
            if not check_correlation(symbol, open_trades):
                continue

            # ══════════════════════════════════════════════════
            # STRATEGY A: SMC Sweep
            # ══════════════════════════════════════════════════
            signal = None

            if getattr(config, "ENABLE_SMC_SWEEP", True):
                # Detect sweep
                if h >= active_s_h + thresh:
                    last_sweep_type = "HIGH"
                    sweep_expiry = ts + timedelta(minutes=60)
                elif l <= active_s_l - thresh:
                    last_sweep_type = "LOW"
                    sweep_expiry = ts + timedelta(minutes=60)

                if ts > sweep_expiry:
                    last_sweep_type = None

                if last_sweep_type:
                    # FVG detection
                    cl = list(fvg_buf)
                    for i in range(len(cl) - 1, 1, -1):
                        newer, older = cl[i], cl[i - 2]

                        if last_sweep_type == "HIGH" and htf_trend == "DOWNTREND":
                            if (older["low"] - newer["high"]) >= fvg_min:
                                te = (older["low"] + newer["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["high"]
                                sl = max(can["high"] for can in cl[-12:]) + sl_buff
                                sl_p = (sl - te) / pip_size
                                rr = ((te - sl) * config.TP_RATIO) / (sl - te) if (sl - te) != 0 else 0
                                if 3.0 <= sl_p <= max_sl_p and abs(rr) >= config.MIN_RISK_REWARD_RATIO:
                                    signal = {
                                        "type": "SELL", "entry": te, "sl": sl,
                                        "tp": te - (sl - te) * config.TP_RATIO,
                                        "strategy": "SMC",
                                    }
                                    break

                        elif last_sweep_type == "LOW" and htf_trend == "UPTREND":
                            if (newer["low"] - older["high"]) >= fvg_min:
                                te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                                sl = min(can["low"] for can in cl[-12:]) - sl_buff
                                sl_p = (te - sl) / pip_size
                                rr = ((te - sl) * config.TP_RATIO) / (te - sl) if (te - sl) != 0 else 0
                                if 3.0 <= sl_p <= max_sl_p and rr >= config.MIN_RISK_REWARD_RATIO:
                                    signal = {
                                        "type": "BUY", "entry": te, "sl": sl,
                                        "tp": te + (te - sl) * config.TP_RATIO,
                                        "strategy": "SMC",
                                    }
                                    break

            # ══════════════════════════════════════════════════
            # STRATEGY B: Breakout
            # ══════════════════════════════════════════════════
            if signal is None and getattr(config, "ENABLE_BREAKOUT", False):
                m5_recent = df_m5.loc[:ts].tail(4)
                if len(m5_recent) >= 4:
                    last_c = m5_recent.iloc[-1]
                    prev_c = m5_recent.iloc[-2]

                    if last_c["close"] > active_s_h and prev_c["close"] > active_s_h and htf_trend == "UPTREND":
                        sl = active_s_l
                        sl_p = (last_c["close"] - sl) / pip_size
                        rr = config.TP_RATIO
                        if 3.0 <= sl_p <= max_sl_p and rr >= config.MIN_RISK_REWARD_RATIO:
                            signal = {
                                "type": "BUY", "entry": last_c["close"], "sl": sl,
                                "tp": last_c["close"] + (last_c["close"] - sl) * config.TP_RATIO,
                                "strategy": "BREAKOUT",
                            }

                    elif last_c["close"] < active_s_l and prev_c["close"] < active_s_l and htf_trend == "DOWNTREND":
                        sl = active_s_h
                        sl_p = (sl - last_c["close"]) / pip_size
                        rr = config.TP_RATIO
                        if 3.0 <= sl_p <= max_sl_p and rr >= config.MIN_RISK_REWARD_RATIO:
                            signal = {
                                "type": "SELL", "entry": last_c["close"], "sl": sl,
                                "tp": last_c["close"] - (sl - last_c["close"]) * config.TP_RATIO,
                                "strategy": "BREAKOUT",
                            }

            # ══════════════════════════════════════════════════
            # STRATEGY C: RSI Scalp
            # ══════════════════════════════════════════════════
            if signal is None:
                rsi_result = detect_rsi_scalp_bt(df_m5, ts, pip_size)
                if rsi_result:
                    sl_p = abs(rsi_result["entry"] - rsi_result["sl"]) / pip_size
                    rr = abs(rsi_result["tp"] - rsi_result["entry"]) / abs(rsi_result["entry"] - rsi_result["sl"]) \
                        if abs(rsi_result["entry"] - rsi_result["sl"]) > 0 else 0

                    # Must align with HTF trend
                    if rsi_result["type"] == "BUY" and htf_trend == "UPTREND" and rr >= config.MIN_RISK_REWARD_RATIO:
                        signal = rsi_result
                    elif rsi_result["type"] == "SELL" and htf_trend == "DOWNTREND" and rr >= config.MIN_RISK_REWARD_RATIO:
                        signal = rsi_result

            # ══════════════════════════════════════════════════
            # FINAL VALIDATION: LTF Confirmations
            # ══════════════════════════════════════════════════
            if signal is not None:
                confirms = count_ltf_confirmations(df_m5, ts, signal["type"])
                if confirms < config.MIN_CONFIRMATIONS:
                    signal = None  # Not enough confluences

            # ══════════════════════════════════════════════════
            # OPEN TRADE
            # ══════════════════════════════════════════════════
            if signal is not None:
                _ticket_counter += 1
                risk_dist = abs(signal["entry"] - signal["sl"])

                new_trade = BacktestTrade(
                    ticket=_ticket_counter,
                    symbol=symbol,
                    trade_type=signal["type"],
                    entry=signal["entry"],
                    sl=signal["sl"],
                    original_sl=signal["sl"],
                    tp=signal["tp"],
                    risk_distance=risk_dist,
                    pip_size=pip_size,
                    strategy=signal.get("strategy", "SMC"),
                    entry_time=ts,
                    trailing_extreme=signal["entry"],
                )
                open_trades.append(new_trade)
                last_sweep_type = None
                sweep_expiry = datetime.min

    # Close any remaining open trades at last price (end of period)
    for trade in open_trades:
        if trade.symbol in symbol_data_cache:
            df_m5_all = symbol_data_cache[trade.symbol][0]
            if not df_m5_all.empty:
                last_row = df_m5_all.iloc[-1]
                exit_p = last_row["close"]
                if trade.trade_type == "BUY":
                    p_pips = (exit_p - trade.entry) / trade.pip_size
                else:
                    p_pips = (trade.entry - exit_p) / trade.pip_size

                sl_pips = trade.risk_distance / trade.pip_size
                gross = (p_pips / sl_pips) * RISK_PER_TRADE * trade.remaining_volume_pct if sl_pips > 0 else 0
                net = gross - (COMMISSION_PER_LOT * trade.remaining_volume_pct * 0.5)

                all_closed_trades.append(ClosedTrade(
                    time=df_m5_all.index[-1], symbol=trade.symbol,
                    trade_type=trade.trade_type, strategy=trade.strategy,
                    gross_pnl=gross, net_pnl=net,
                ))

    return all_closed_trades


# ══════════════════════════════════════════════════════════════════════════════
# REPORT & RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest():
    if not initialize_mt5():
        print("Failed to connect to MT5.")
        return

    DAYS_BACK = 90
    print(f"FOREX LIQUIDITY HUNTER V18 — REALISTIC BACKTEST")
    print(f"Loading {DAYS_BACK} days of history...")
    print(f"Settings: RR>={config.MIN_RISK_REWARD_RATIO}, "
          f"Risk={config.MAX_RISK_PER_TRADE_PCT}%, "
          f"Confirmations>={config.MIN_CONFIRMATIONS}, "
          f"Checkpoint TP={'ON' if getattr(config, 'ENABLE_CHECKPOINT_TP', False) else 'OFF'}")
    print()

    symbol_data_cache = {}
    total_min_date = datetime.now()

    for symbol in config.SYMBOLS:
        print(f"  Loading {symbol}...", end="\r")
        df_m5, df_h1, df_m15 = get_symbol_data(symbol, days_back=DAYS_BACK)
        if df_m5 is not None and not df_m5.empty:
            print(f"  OK {symbol}: {len(df_m5)} candles         ")
            symbol_data_cache[symbol] = (df_m5, df_h1, df_m15)
            total_min_date = min(total_min_date, df_m5.index.min())
        else:
            print(f"  -- {symbol}: No history               ")

    if not symbol_data_cache:
        print("No historical data available.")
        mt5.shutdown()
        return

    print(f"\nData from: {total_min_date.strftime('%Y-%m-%d')}")

    # Generate months
    test_months = []
    curr = total_min_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()
    while curr <= now:
        month_end = curr.replace(day=calendar.monthrange(curr.year, curr.month)[1], hour=23, minute=59)
        test_months.append((curr, month_end))
        next_m = curr.month + 1
        next_y = curr.year
        if next_m > 12:
            next_m = 1
            next_y += 1
        curr = curr.replace(year=next_y, month=next_m)

    # Header
    print()
    print(f"{'MONTH':<12} | {'TRADES':>6} | {'WR':>6} | {'GROSS':>10} | {'NET':>10} | {'BEST DAY':>8} | {'CONSIST':>7} | {'STRATS'}")
    print("-" * 100)

    all_time_gross = 0
    all_time_net = 0
    total_trades = 0
    total_wins = 0
    strategy_counts = {"SMC": 0, "BREAKOUT": 0, "RSI": 0}

    for m_start, m_end in test_months:
        trades = run_monthly_backtest(symbol_data_cache, m_start, m_end)

        gross_pnl = sum(t.gross_pnl for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)
        wins = len([t for t in trades if t.net_pnl > 0])
        wr = (wins / len(trades) * 100) if trades else 0

        # Daily breakdown
        daily_profits = {}
        for t in trades:
            day = str(t.time)[:10]
            daily_profits[day] = daily_profits.get(day, 0) + t.net_pnl
        max_win_day = max(daily_profits.values()) if daily_profits else 0
        consistency_pct = (max_win_day / net_pnl * 100) if net_pnl > 0 else 0

        # Strategy breakdown
        month_strats = {}
        for t in trades:
            month_strats[t.strategy] = month_strats.get(t.strategy, 0) + 1
            strategy_counts[t.strategy] = strategy_counts.get(t.strategy, 0) + 1

        strat_str = " ".join(f"{k}:{v}" for k, v in sorted(month_strats.items()))
        status = "OK" if consistency_pct <= 30.0 and net_pnl >= 0 else ("!!" if net_pnl > 0 else "--")

        month_name = m_start.strftime("%b %Y")
        print(
            f"{month_name:<12} | {len(trades):>6} | {wr:>5.1f}% | "
            f"${gross_pnl:>+9.2f} | ${net_pnl:>+9.2f} | {consistency_pct:>7.1f}% | "
            f"{status:>7} | {strat_str}"
        )

        all_time_gross += gross_pnl
        all_time_net += net_pnl
        total_trades += len(trades)
        total_wins += wins

    mt5.shutdown()

    # Summary
    print("-" * 100)
    overall_wr = (total_wins / total_trades * 100) if total_trades else 0
    avg_per_month = total_trades / len(test_months) if test_months else 0
    print(
        f"{'TOTAL':<12} | {total_trades:>6} | {overall_wr:>5.1f}% | "
        f"${all_time_gross:>+9.2f} | ${all_time_net:>+9.2f}"
    )
    print()
    print(f"  Gross Profit: ${all_time_gross:+,.2f}")
    print(f"  Net Profit:   ${all_time_net:+,.2f}  (after commission + spread)")
    print(f"  Total Trades: {total_trades}  (avg {avg_per_month:.1f}/month)")
    print(f"  Win Rate:     {overall_wr:.1f}%")
    print(f"  Strategies:   {', '.join(f'{k}: {v}' for k, v in sorted(strategy_counts.items()) if v > 0)}")
    print(f"  Settings:     RR>={config.MIN_RISK_REWARD_RATIO}, "
          f"Risk={config.MAX_RISK_PER_TRADE_PCT}%, "
          f"Checkpoints={'ON' if getattr(config, 'ENABLE_CHECKPOINT_TP', False) else 'OFF'}")
    print("=" * 100)


if __name__ == "__main__":
    run_backtest()
