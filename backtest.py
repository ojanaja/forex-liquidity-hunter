"""
Forex Liquidity Hunter - Backtester V18 (Realistic Simulation)
===============================================================
Mirrors the LIVE bot logic as closely as possible:
  - All 3 strategy engines (SMC Sweep, Breakout, RSI Scalp)
  - HTF Trend Filter (EMA 50/200 + Market Structure)
  - Sideways Detection (ATR + Bollinger Band Squeeze)
  - LTF Confirmations (RSI, Engulfing, Volume)
  - News Filter (static schedule blackout simulation)
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
from elliott_wave import detect_elliott_bt
from news_filter import _generate_static_schedule, _extract_currencies_from_symbol
import pytz

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
    checkpoints_hit: list = field(default_factory=lambda: [False] * len(getattr(config, 'TP_CHECKPOINTS', [1.0])))
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
    entry_price: float = 0.0
    exit_price: float = 0.0
    exit_type: str = ""       # SL, TP, PARTIAL, TRAIL, EOD
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

    # Consensus logic:
    # - EMA is the PRIMARY trend indicator
    # - Structure only VETOES if it actively contradicts (UP vs DOWN)
    # - If structure is unclear (SIDEWAYS), trust the EMA
    if ema_trend == structure:
        return ema_trend  # Perfect agreement
    elif structure == "SIDEWAYS":
        return ema_trend  # EMA has a direction, structure unclear → trust EMA
    elif ema_trend == "SIDEWAYS":
        return structure  # EMA flat, but structure has direction → trust structure
    else:
        return "SIDEWAYS"  # EMA and structure actively contradict (UP vs DOWN)


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
# NEWS FILTER (Static Schedule Simulation for Backtest)
# ══════════════════════════════════════════════════════════════════════════════

_bt_static_schedule = None


def _get_bt_news_schedule():
    """Lazy-load static news schedule for backtest period."""
    global _bt_static_schedule
    if _bt_static_schedule is None:
        events = []
        for year in range(2024, 2028):
            events.extend(_generate_static_schedule(year))
        _bt_static_schedule = events
    return _bt_static_schedule


def _check_news_blackout_bt(ts, symbol: str) -> bool:
    """
    Check if timestamp falls within a news blackout window.
    Uses static schedule (same fallback as live bot).
    Returns True if entry should be BLOCKED.
    """
    if not getattr(config, "ENABLE_NEWS_FILTER", False):
        return False

    events = _get_bt_news_schedule()
    symbol_currencies = _extract_currencies_from_symbol(symbol)

    before_min = getattr(config, "NEWS_BLACKOUT_MINUTES_BEFORE", 15)
    after_min = getattr(config, "NEWS_BLACKOUT_MINUTES_AFTER", 10)

    # Make ts timezone-aware
    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
        ts_utc = ts
    else:
        ts_utc = pytz.UTC.localize(ts) if not isinstance(ts, pd.Timestamp) else ts.tz_localize(pytz.UTC)

    for event in events:
        event_currency = event.get("currency", "")
        if event_currency not in symbol_currencies:
            continue

        event_time = event["time"]
        if event_time.tzinfo is None:
            event_time = pytz.UTC.localize(event_time)

        diff_min = (event_time - ts_utc).total_seconds() / 60

        # In blackout window: -after_min <= diff <= before_min
        if -after_min <= diff_min <= before_min:
            return True

    return False


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
                # SL to Break-Even + commission/spread buffer
                # Mirror the live bot's _calc_be_buffer logic
                commission_distance = (COMMISSION_PER_LOT / (RISK_PER_TRADE / trade.risk_distance * pip_size)) * pip_size if RISK_PER_TRADE > 0 else 0
                spread_distance = SPREAD_COST_PIPS * pip_size
                be_buffer = commission_distance + spread_distance
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

def run_monthly_backtest(symbol_data_cache, start_date, end_date, diagnostics=None):
    """
    Full simulation matching live bot logic.
    Processes all symbols bar-by-bar with multi-strategy, validation, and checkpoints.
    """
    global _ticket_counter
    all_closed_trades = []
    open_trades: list[BacktestTrade] = []

    # Diagnostic counters
    if diagnostics is None:
        diagnostics = {}
    dx = diagnostics
    for key in ["candles", "in_session", "htf_sideways", "mkt_sideways",
                "corr_block", "no_signal", "rr_fail", "confirm_fail",
                "trades_opened", "concurrent_block", "news_blackout"]:
        dx.setdefault(key, 0)

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
                        entry_price=trade.entry, exit_price=0.0,
                        exit_type=f"PARTIAL-{pr['checkpoint']}",
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

                    exit_reason = "TP" if ((trade.trade_type == "BUY" and exit_price >= trade.tp and trade.tp > 0) or
                                          (trade.trade_type == "SELL" and exit_price <= trade.tp and trade.tp > 0)) else "SL"
                    if trade.trailing_active and exit_reason == "SL":
                        exit_reason = "TRAIL"

                    all_closed_trades.append(ClosedTrade(
                        time=ts, symbol=symbol, trade_type=trade.trade_type,
                        strategy=trade.strategy,
                        gross_pnl=gross_pnl, net_pnl=net_pnl,
                        entry_price=trade.entry, exit_price=exit_price,
                        exit_type=exit_reason,
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
            dx["candles"] += 1
            if len(open_trades) >= config.MAX_OPEN_TRADES:
                dx["concurrent_block"] += 1
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

            dx["in_session"] += 1

            # ══════════════════════════════════════════════════
            # VALIDATION GATE (mirrors market_filter.validate_entry)
            # ══════════════════════════════════════════════════

            # 0. News Blackout
            if _check_news_blackout_bt(ts, symbol):
                dx["news_blackout"] += 1
                continue

            # 1. HTF Trend
            htf_trend = compute_htf_trend(df_h1, ts)
            if htf_trend == "SIDEWAYS":
                dx["htf_sideways"] += 1
                continue

            # 2. Sideways detection
            if check_sideways(df_h1, ts):
                dx["mkt_sideways"] += 1
                continue

            # 3. Correlation filter
            if not check_correlation(symbol, open_trades):
                dx["corr_block"] += 1
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
                                rr = config.TP_RATIO  # TP = entry - risk * TP_RATIO, so RR = TP_RATIO
                                if 3.0 <= sl_p <= max_sl_p and rr >= config.MIN_RISK_REWARD_RATIO:
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
                                rr = config.TP_RATIO  # TP = entry + risk * TP_RATIO, so RR = TP_RATIO
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
            # STRATEGY D: Elliott Wave (Wave 3 Entry)
            # ══════════════════════════════════════════════════
            if signal is None and getattr(config, "ENABLE_ELLIOTT_WAVE", False):
                ew_result = detect_elliott_bt(df_m15_all, ts, htf_trend, pip_size)
                if ew_result:
                    signal = ew_result

            # ══════════════════════════════════════════════════
            # FINAL VALIDATION: LTF Confirmations
            # ══════════════════════════════════════════════════
            if signal is None:
                dx["no_signal"] += 1

            if signal is not None:
                confirms = count_ltf_confirmations(df_m5, ts, signal["type"])
                if confirms < config.MIN_CONFIRMATIONS:
                    dx["confirm_fail"] += 1
                    signal = None  # Not enough confluences

            # ══════════════════════════════════════════════════
            # OPEN TRADE
            # ══════════════════════════════════════════════════
            if signal is not None:
                dx["trades_opened"] += 1
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
                    entry_price=trade.entry, exit_price=exit_p,
                    exit_type="EOD",
                ))

    return all_closed_trades


# ══════════════════════════════════════════════════════════════════════════════
# PROGRESS BAR UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

import time as _time
import sys as _sys
import os as _os


def _progress_bar(current, total, prefix="", width=40, start_time=None):
    """Print an inline progress bar with ETA."""
    pct = current / total if total > 0 else 1
    filled = int(width * pct)
    bar = "#" * filled + "-" * (width - filled)

    eta_str = ""
    if start_time and current > 0:
        elapsed = _time.time() - start_time
        eta = (elapsed / current) * (total - current)
        if eta > 60:
            eta_str = f" ETA: {eta/60:.1f}m"
        else:
            eta_str = f" ETA: {eta:.0f}s"

    _sys.stdout.write(f"\r  {prefix} |{bar}| {pct*100:5.1f}% ({current}/{total}){eta_str}   ")
    _sys.stdout.flush()
    if current >= total:
        print()


# ══════════════════════════════════════════════════════════════════════════════
# ADVANCED EVALUATION REPORT
# ══════════════════════════════════════════════════════════════════════════════

def generate_advanced_report(all_trades, test_months, report_path):
    """Generate comprehensive evaluation report — console + file."""
    lines = []

    def out(text=""):
        lines.append(text)
        print(text)

    out("=" * 100)
    out("  FOREX LIQUIDITY HUNTER V18 - ADVANCED BACKTEST REPORT")
    out(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out("=" * 100)

    if not all_trades:
        out("\n  No trades generated. Bot filters may be too strict.\n")
        _save_report(lines, report_path)
        return

    # Basic Stats
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t.net_pnl > 0]
    losses = [t for t in all_trades if t.net_pnl <= 0]
    total_gross = sum(t.gross_pnl for t in all_trades)
    total_net = sum(t.net_pnl for t in all_trades)
    total_commission = total_gross - total_net
    win_rate = len(wins) / total_trades * 100 if total_trades else 0

    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0
    largest_win = max((t.net_pnl for t in all_trades), default=0)
    largest_loss = min((t.net_pnl for t in all_trades), default=0)

    wr_decimal = len(wins) / total_trades if total_trades else 0
    expectancy = (wr_decimal * avg_win) + ((1 - wr_decimal) * avg_loss)

    gross_wins = sum(t.net_pnl for t in wins) if wins else 0
    gross_losses = abs(sum(t.net_pnl for t in losses)) if losses else 1
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    out("\n  1. PERFORMANCE SUMMARY")
    out("  " + "-" * 50)
    out(f"  Total Trades:      {total_trades}")
    out(f"  Wins / Losses:     {len(wins)} / {len(losses)}")
    out(f"  Win Rate:          {win_rate:.1f}%")
    out(f"  Gross Profit:      ${total_gross:+,.2f}")
    out(f"  Net Profit:        ${total_net:+,.2f}")
    out(f"  Commission+Spread: ${total_commission:+,.2f}")
    out(f"  Avg Win:           ${avg_win:+,.2f}")
    out(f"  Avg Loss:          ${avg_loss:+,.2f}")
    out(f"  Largest Win:       ${largest_win:+,.2f}")
    out(f"  Largest Loss:      ${largest_loss:+,.2f}")
    out(f"  Profit Factor:     {profit_factor:.2f}")
    out(f"  Expectancy/Trade:  ${expectancy:+,.2f}")
    if test_months:
        out(f"  Avg/Month:         ${total_net / len(test_months):+,.2f}")

    # Drawdown
    equity_curve = [ACCOUNT_BALANCE]
    peak = ACCOUNT_BALANCE
    max_drawdown = 0
    max_drawdown_pct = 0

    for t in all_trades:
        equity_curve.append(equity_curve[-1] + t.net_pnl)
        if equity_curve[-1] > peak:
            peak = equity_curve[-1]
        dd = peak - equity_curve[-1]
        dd_pct = (dd / peak * 100) if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
            max_drawdown_pct = dd_pct

    final_equity = equity_curve[-1]
    total_return_pct = (final_equity - ACCOUNT_BALANCE) / ACCOUNT_BALANCE * 100

    out("\n  2. DRAWDOWN ANALYSIS")
    out("  " + "-" * 50)
    out(f"  Starting Balance:  ${ACCOUNT_BALANCE:,.2f}")
    out(f"  Final Equity:      ${final_equity:,.2f}")
    out(f"  Total Return:      {total_return_pct:+.2f}%")
    out(f"  Max Drawdown:      ${max_drawdown:,.2f} ({max_drawdown_pct:.1f}%)")
    if max_drawdown > 0:
        out(f"  Recovery Factor:   {total_net / max_drawdown:.2f}")

    # Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    for t in all_trades:
        if t.net_pnl > 0:
            current_streak = current_streak + 1 if current_streak > 0 else 1
            max_win_streak = max(max_win_streak, current_streak)
        else:
            current_streak = current_streak - 1 if current_streak < 0 else -1
            max_loss_streak = max(max_loss_streak, abs(current_streak))

    out("\n  3. STREAK ANALYSIS")
    out("  " + "-" * 50)
    out(f"  Max Win Streak:    {max_win_streak} trades")
    out(f"  Max Loss Streak:   {max_loss_streak} trades")

    # Per-Strategy
    strat_stats = {}
    for t in all_trades:
        s = t.strategy
        if s not in strat_stats:
            strat_stats[s] = {"trades": 0, "wins": 0, "net": 0, "gross": 0}
        strat_stats[s]["trades"] += 1
        strat_stats[s]["net"] += t.net_pnl
        strat_stats[s]["gross"] += t.gross_pnl
        if t.net_pnl > 0:
            strat_stats[s]["wins"] += 1

    out("\n  4. STRATEGY PERFORMANCE")
    out("  " + "-" * 70)
    out(f"  {'Strategy':<12} | {'Trades':>6} | {'WR':>6} | {'Gross':>10} | {'Net':>10} | {'Avg Net':>8}")
    out("  " + "-" * 70)
    for strat, stats in sorted(strat_stats.items()):
        swr = stats["wins"] / stats["trades"] * 100 if stats["trades"] else 0
        avg = stats["net"] / stats["trades"] if stats["trades"] else 0
        out(f"  {strat:<12} | {stats['trades']:>6} | {swr:>5.1f}% | ${stats['gross']:>+9.2f} | ${stats['net']:>+9.2f} | ${avg:>+7.2f}")

    # Per-Symbol
    sym_stats = {}
    for t in all_trades:
        s = t.symbol
        if s not in sym_stats:
            sym_stats[s] = {"trades": 0, "wins": 0, "net": 0}
        sym_stats[s]["trades"] += 1
        sym_stats[s]["net"] += t.net_pnl
        if t.net_pnl > 0:
            sym_stats[s]["wins"] += 1

    sorted_symbols = sorted(sym_stats.items(), key=lambda x: x[1]["net"], reverse=True)

    out("\n  5. SYMBOL PERFORMANCE")
    out("  " + "-" * 55)
    out(f"  {'Symbol':<12} | {'Trades':>6} | {'WR':>6} | {'Net P/L':>10} | {'Status'}")
    out("  " + "-" * 55)
    for sym, stats in sorted_symbols:
        swr = stats["wins"] / stats["trades"] * 100 if stats["trades"] else 0
        status = "PROFIT" if stats["net"] > 0 else "LOSS"
        out(f"  {sym:<12} | {stats['trades']:>6} | {swr:>5.1f}% | ${stats['net']:>+9.2f} | {status}")

    # Checkpoint TP
    cp_counts = {"TP1": 0, "TP2": 0, "TP3": 0}
    for t in all_trades:
        for cp in t.partial_exits:
            if cp in cp_counts:
                cp_counts[cp] += 1

    if any(v > 0 for v in cp_counts.values()):
        out("\n  6. CHECKPOINT TP EFFECTIVENESS")
        out("  " + "-" * 50)
        for cp, count in cp_counts.items():
            pct = count / total_trades * 100 if total_trades else 0
            out(f"  {cp} Hit:          {count} times ({pct:.1f}% of trades)")

    # Daily Analysis
    daily_pnl = {}
    for t in all_trades:
        day = str(t.time)[:10]
        daily_pnl[day] = daily_pnl.get(day, 0) + t.net_pnl

    if daily_pnl:
        daily_values = list(daily_pnl.values())
        green_days = len([d for d in daily_values if d > 0])
        red_days = len([d for d in daily_values if d <= 0])
        best_day_val = max(daily_values)
        worst_day_val = min(daily_values)
        avg_daily = sum(daily_values) / len(daily_values)

        out("\n  7. DAILY ANALYSIS")
        out("  " + "-" * 50)
        out(f"  Trading Days:      {len(daily_values)}")
        out(f"  Green Days:        {green_days} ({green_days/len(daily_values)*100:.0f}%)")
        out(f"  Red Days:          {red_days} ({red_days/len(daily_values)*100:.0f}%)")
        out(f"  Best Day:          ${best_day_val:+,.2f}")
        out(f"  Worst Day:         ${worst_day_val:+,.2f}")
        out(f"  Avg Daily P/L:     ${avg_daily:+,.2f}")

        if total_net > 0:
            consistency = best_day_val / total_net * 100
            out(f"  Consistency:       {consistency:.1f}% {'(PASS <= 30%)' if consistency <= 30 else '(FAIL > 30%)'}")

    # Equity Curve (sampled)
    out("\n  8. EQUITY CURVE")
    out("  " + "-" * 60)
    step = max(1, len(equity_curve) // 10)
    for i in range(0, len(equity_curve), step):
        val = equity_curve[i]
        diff = val - ACCOUNT_BALANCE
        bar_len = int(diff / max(abs(total_net), 1) * 20)
        if bar_len >= 0:
            bar = "#" * min(bar_len, 30)
        else:
            bar = "-" * min(abs(bar_len), 30)
        label = f"Trade {i}" if i > 0 else "Start"
        out(f"  {label:<12} ${val:>10,.2f}  {bar}")
    out(f"  {'End':<12} ${equity_curve[-1]:>10,.2f}")

    # Trade History
    out("\n  9. TRADE HISTORY")
    out("  " + "-" * 120)
    out(f"  {'#':>4} | {'Date':^19} | {'Symbol':^10} | {'Dir':^4} | {'Strategy':^8} | {'Entry':>10} | {'Exit':>10} | {'Type':^8} | {'Net P/L':>10} | {'Balance':>11}")
    out("  " + "-" * 120)
    running_balance = ACCOUNT_BALANCE
    for i, t in enumerate(all_trades):
        running_balance += t.net_pnl
        t_time = t.time
        if hasattr(t_time, 'strftime'):
            t_str = t_time.strftime('%Y-%m-%d %H:%M')
        else:
            t_str = str(t_time)[:19]
        entry_str = f"{t.entry_price:.5f}" if t.entry_price else "-"
        exit_str = f"{t.exit_price:.5f}" if t.exit_price else "-"
        pnl_sign = "+" if t.net_pnl >= 0 else ""
        out(f"  {i+1:>4} | {t_str:^19} | {t.symbol:^10} | {t.trade_type:^4} | {t.strategy:^8} | {entry_str:>10} | {exit_str:>10} | {t.exit_type:^8} | ${pnl_sign}{t.net_pnl:>8.2f} | ${running_balance:>10,.2f}")
    out("  " + "-" * 120)

    # Verdict
    out("\n  " + "=" * 50)
    out("  VERDICT")
    out("  " + "=" * 50)

    issues = []
    if win_rate < 40:
        issues.append(f"Low win rate ({win_rate:.0f}%) - entries may need better timing")
    if profit_factor < 1.0:
        issues.append(f"Profit factor < 1 ({profit_factor:.2f}) - losing system")
    elif profit_factor < 1.5:
        issues.append(f"Profit factor weak ({profit_factor:.2f}) - target > 1.5")
    if max_drawdown_pct > 10:
        issues.append(f"High drawdown ({max_drawdown_pct:.1f}%) - risk management review needed")
    if expectancy < 0:
        issues.append(f"Negative expectancy (${expectancy:+.2f}/trade) - NOT viable")
    if total_trades < 10:
        issues.append(f"Too few trades ({total_trades}) - insufficient data")

    strengths = []
    if win_rate >= 55:
        strengths.append(f"Good win rate ({win_rate:.0f}%)")
    if profit_factor >= 2.0:
        strengths.append(f"Strong profit factor ({profit_factor:.2f})")
    if max_drawdown_pct < 5:
        strengths.append(f"Low drawdown ({max_drawdown_pct:.1f}%)")
    if expectancy > 5:
        strengths.append(f"Solid expectancy (${expectancy:+.2f}/trade)")
    if total_return_pct >= 6:
        strengths.append(f"Target return achieved ({total_return_pct:+.1f}%)")

    if strengths:
        out("  [+] Strengths:")
        for s in strengths:
            out(f"      + {s}")
    if issues:
        out("  [!] Issues:")
        for issue in issues:
            out(f"      ! {issue}")

    if not issues and profit_factor > 1.5 and expectancy > 0:
        out("\n  >> SYSTEM VIABLE FOR LIVE TRADING <<")
    elif expectancy > 0 and profit_factor > 1.0:
        out("\n  >> SYSTEM MARGINAL - Optimize before live trading <<")
    else:
        out("\n  >> SYSTEM NOT READY - Significant improvements needed <<")

    out("\n" + "=" * 100)
    _save_report(lines, report_path)


def _save_report(lines, report_path):
    """Save report to file."""
    _os.makedirs(_os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  Report saved to: {report_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest():
    if not initialize_mt5():
        print("Failed to connect to MT5.")
        return

    DAYS_BACK = 90
    print()
    print("=" * 70)
    print("  FOREX LIQUIDITY HUNTER V18 - REALISTIC BACKTEST ENGINE")
    print("=" * 70)
    print(f"  Period:          Last {DAYS_BACK} days")
    print(f"  Risk/Trade:      {config.MAX_RISK_PER_TRADE_PCT}%")
    print(f"  Min RR:          1:{config.MIN_RISK_REWARD_RATIO}")
    print(f"  Confirmations:   >= {config.MIN_CONFIRMATIONS}")
    print(f"  News Filter:     {'ON' if getattr(config, 'ENABLE_NEWS_FILTER', False) else 'OFF'}")
    print(f"  Checkpoint TP:   {'ON' if getattr(config, 'ENABLE_CHECKPOINT_TP', False) else 'OFF'}")
    print(f"  Max Open Trades: {config.MAX_OPEN_TRADES}")
    print(f"  Symbols:         {len(config.SYMBOLS)}")
    print("=" * 70)

    # Phase 1: Load Data
    print("\n  PHASE 1: Loading Historical Data")
    print("  " + "-" * 40)

    symbol_data_cache = {}
    total_min_date = datetime.now()
    load_start = _time.time()

    for idx, symbol in enumerate(config.SYMBOLS):
        _progress_bar(idx, len(config.SYMBOLS), prefix="Data    ", start_time=load_start)
        df_m5, df_h1, df_m15 = get_symbol_data(symbol, days_back=DAYS_BACK)
        if df_m5 is not None and not df_m5.empty:
            symbol_data_cache[symbol] = (df_m5, df_h1, df_m15)
            total_min_date = min(total_min_date, df_m5.index.min())

    _progress_bar(len(config.SYMBOLS), len(config.SYMBOLS), prefix="Data    ", start_time=load_start)
    print(f"  Loaded {len(symbol_data_cache)}/{len(config.SYMBOLS)} symbols")

    if not symbol_data_cache:
        print("  ERROR: No historical data available.")
        mt5.shutdown()
        return

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

    # Phase 2: Run Backtest
    print(f"\n  PHASE 2: Running Backtest ({len(test_months)} months)")
    print("  " + "-" * 40)

    all_trades_combined = []
    monthly_results = []
    bt_start = _time.time()
    diagnostics = {}

    for month_idx, (m_start, m_end) in enumerate(test_months):
        _progress_bar(month_idx, len(test_months), prefix="Backtest", start_time=bt_start)

        trades = run_monthly_backtest(symbol_data_cache, m_start, m_end, diagnostics)
        all_trades_combined.extend(trades)

        gross = sum(t.gross_pnl for t in trades)
        net = sum(t.net_pnl for t in trades)
        wins = len([t for t in trades if t.net_pnl > 0])
        wr = (wins / len(trades) * 100) if trades else 0

        strat_breakdown = {}
        for t in trades:
            strat_breakdown[t.strategy] = strat_breakdown.get(t.strategy, 0) + 1

        monthly_results.append({
            "month": m_start.strftime("%b %Y"),
            "trades": len(trades),
            "wins": wins,
            "wr": wr,
            "gross": gross,
            "net": net,
            "strats": strat_breakdown,
        })

    _progress_bar(len(test_months), len(test_months), prefix="Backtest", start_time=bt_start)
    elapsed = _time.time() - bt_start
    print(f"  Completed in {elapsed:.1f}s ({len(all_trades_combined)} trades)")

    mt5.shutdown()

    # Diagnostic Summary
    print(f"\n  DIAGNOSTIC: Filter Rejection Breakdown")
    print("  " + "-" * 50)
    print(f"  Candles processed:   {diagnostics.get('candles', 0):>8}")
    print(f"  In session window:   {diagnostics.get('in_session', 0):>8}")
    print(f"  News blackout:       {diagnostics.get('news_blackout', 0):>8}  (blocked)")
    print(f"  HTF = SIDEWAYS:      {diagnostics.get('htf_sideways', 0):>8}  (blocked)")
    print(f"  Market sideways:     {diagnostics.get('mkt_sideways', 0):>8}  (blocked)")
    print(f"  Correlation block:   {diagnostics.get('corr_block', 0):>8}  (blocked)")
    print(f"  Concurrent block:    {diagnostics.get('concurrent_block', 0):>8}  (blocked)")
    print(f"  No signal generated: {diagnostics.get('no_signal', 0):>8}")
    print(f"  Confirm fail (<{config.MIN_CONFIRMATIONS}):  {diagnostics.get('confirm_fail', 0):>8}  (blocked)")
    print(f"  Trades opened:       {diagnostics.get('trades_opened', 0):>8}")

    # Phase 3: Monthly Table
    print(f"\n  PHASE 3: Monthly Breakdown")
    print("  " + "-" * 85)
    print(f"  {'Month':<10} | {'Trades':>6} | {'WR':>6} | {'Gross':>10} | {'Net':>10} | {'Strategies'}")
    print("  " + "-" * 85)

    for mr in monthly_results:
        strat_str = " ".join(f"{k}:{v}" for k, v in sorted(mr["strats"].items()))
        print(
            f"  {mr['month']:<10} | {mr['trades']:>6} | {mr['wr']:>5.1f}% | "
            f"${mr['gross']:>+9.2f} | ${mr['net']:>+9.2f} | {strat_str}"
        )

    print("  " + "-" * 85)

    # Phase 4: Advanced Report
    print(f"\n  PHASE 4: Generating Evaluation Report\n")
    report_path = _os.path.join(config.LOG_DIR, "backtest_report.txt")
    generate_advanced_report(all_trades_combined, test_months, report_path)


if __name__ == "__main__":
    run_backtest()

