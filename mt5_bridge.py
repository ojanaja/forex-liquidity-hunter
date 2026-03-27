"""
Forex Liquidity Hunter - MetaTrader 5 Bridge
Handles all communication with the MT5 terminal.
"""
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional MT5 import (allows syntax checks on Mac)
# ---------------------------------------------------------------------------
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 module not found. Running in MOCK mode.")


# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------
TIMEFRAME_MAP = {}
if MT5_AVAILABLE:
    TIMEFRAME_MAP = {
        1:  mt5.TIMEFRAME_M1,
        5:  mt5.TIMEFRAME_M5,
        15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
    }


@dataclass
class AccountInfo:
    balance: float
    equity: float
    profit: float
    margin_free: float


@dataclass
class SymbolInfo:
    point: float       # e.g. 0.00001 for EURUSD
    digits: int        # e.g. 5
    trade_tick_value: float
    spread: float      # in points
    volume_min: float
    volume_max: float
    volume_step: float


@dataclass
class Position:
    ticket: int
    symbol: str
    type: int          # 0 = BUY, 1 = SELL
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    time: datetime


# ===========================================================================
# Connection
# ===========================================================================

def connect() -> bool:
    """Initialize MetaTrader 5 connection."""
    if not MT5_AVAILABLE:
        logger.info("[MOCK] MT5 connect — simulated OK")
        return True

    kwargs = {
        "login": config.MT5_LOGIN,
        "password": config.MT5_PASSWORD,
        "server": config.MT5_SERVER,
    }
    if config.MT5_PATH:
        kwargs["path"] = config.MT5_PATH

    if not mt5.initialize(**kwargs):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False

    info = mt5.account_info()
    if info is None:
        logger.error("Failed to get account info after connect.")
        return False

    logger.info(
        f"Connected to MT5 — Account: {info.login}, "
        f"Balance: ${info.balance:.2f}, Server: {info.server}"
    )
    return True


def disconnect():
    """Shutdown MT5 connection."""
    if MT5_AVAILABLE:
        mt5.shutdown()
    logger.info("MT5 disconnected.")


# ===========================================================================
# Account Data
# ===========================================================================

def get_account_info() -> AccountInfo:
    """Return current account metrics."""
    if not MT5_AVAILABLE:
        return AccountInfo(
            balance=config.ACCOUNT_BALANCE,
            equity=config.ACCOUNT_BALANCE,
            profit=0.0,
            margin_free=config.ACCOUNT_BALANCE,
        )
    info = mt5.account_info()
    return AccountInfo(
        balance=info.balance,
        equity=info.equity,
        profit=info.profit,
        margin_free=info.margin_free,
    )


# ===========================================================================
# Market Data
# ===========================================================================

def get_ohlc(
    symbol: str,
    timeframe_minutes: int = 5,
    count: int = 100,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles as a DataFrame.
    Columns: time, open, high, low, close, tick_volume
    """
    if not MT5_AVAILABLE:
        logger.info(f"[MOCK] get_ohlc({symbol}, M{timeframe_minutes}, {count})")
        # Return empty DataFrame with correct schema
        return pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "tick_volume"]
        )

    tf = TIMEFRAME_MAP.get(timeframe_minutes)
    if tf is None:
        logger.error(f"Unsupported timeframe: M{timeframe_minutes}")
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        logger.warning(f"No data returned for {symbol} M{timeframe_minutes}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def get_current_price(symbol: str) -> Optional[dict]:
    """Return {'bid': ..., 'ask': ...} for the symbol."""
    if not MT5_AVAILABLE:
        logger.info(f"[MOCK] get_current_price({symbol})")
        return {"bid": 1.10000, "ask": 1.10010}

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.warning(f"No tick data for {symbol}")
        return None
    return {"bid": tick.bid, "ask": tick.ask}


def get_symbol_info(symbol: str) -> Optional[SymbolInfo]:
    """Return symbol specifications (point, spread, lot limits)."""
    if not MT5_AVAILABLE:
        return SymbolInfo(
            point=0.00001,
            digits=5,
            trade_tick_value=10.0,
            spread=10,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )

    info = mt5.symbol_info(symbol)
    if info is None:
        logger.warning(f"Symbol info not found: {symbol}")
        return None

    return SymbolInfo(
        point=info.point,
        digits=info.digits,
        trade_tick_value=info.trade_tick_value,
        spread=info.spread,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
    )


# ===========================================================================
# Order Execution
# ===========================================================================

def place_order(
    symbol: str,
    direction: str,  # "BUY" or "SELL"
    lot_size: float,
    sl: float,
    tp: float,
    comment: str = "",
) -> Optional[int]:
    """
    Place a market order.  Returns the order ticket or None on failure.
    In DRY_RUN mode, only logs the order.
    """
    if config.DRY_RUN:
        price = get_current_price(symbol)
        entry = price["ask"] if direction == "BUY" else price["bid"]
        logger.info(
            f"[DRY_RUN] {direction} {lot_size} {symbol} @ {entry:.5f} "
            f"SL={sl:.5f} TP={tp:.5f}"
        )
        return -1  # Fake ticket

    if not MT5_AVAILABLE:
        logger.error("Cannot place real order: MT5 not available.")
        return None

    price = get_current_price(symbol)
    if price is None:
        return None

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    entry = price["ask"] if direction == "BUY" else price["bid"]

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 0,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        error_msg = result.comment if result else "Unknown error"
        logger.error(
            f"Order FAILED: {direction} {lot_size} {symbol} — {error_msg}"
        )
        return None

    logger.info(
        f"Order FILLED: {direction} {lot_size} {symbol} @ {entry:.5f} "
        f"SL={sl:.5f} TP={tp:.5f} — Ticket: {result.order}"
    )
    return result.order


# ===========================================================================
# Position Management
# ===========================================================================

def modify_position_sl(ticket: int, new_sl: float) -> bool:
    """Modifies the Stop Loss of an existing open position."""
    if config.DRY_RUN:
        logger.info(f"[DRY_RUN] Modify ticket {ticket} SL to {new_sl:.5f}")
        return True

    if not MT5_AVAILABLE:
        return False

    pos = mt5.positions_get(ticket=ticket)
    if pos is None or len(pos) == 0:
        return False

    p = pos[0]
    
    # Don't modify if it's already exactly the same
    if abs(p.sl - new_sl) < 0.00001:
        return True

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": p.symbol,
        "sl": new_sl,
        "tp": p.tp,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"🛡️ Auto Break-Even: Moved ticket {ticket} SL to {new_sl:.5f}")
        return True

    err = result.comment if result else "Unknown error"
    logger.error(f"Failed to move SL for ticket {ticket}: {err}")
    return False


def get_open_positions() -> list[Position]:
    """Return all currently open positions."""
    if not MT5_AVAILABLE:
        return []

    positions = mt5.positions_get()
    if positions is None:
        return []

    return [
        Position(
            ticket=p.ticket,
            symbol=p.symbol,
            type=p.type,
            volume=p.volume,
            price_open=p.price_open,
            sl=p.sl,
            tp=p.tp,
            profit=p.profit,
            time=datetime.fromtimestamp(p.time),
        )
        for p in positions
    ]


def close_all_positions() -> int:
    """
    Emergency: close every open position.
    Returns the number of positions successfully closed.
    """
    positions = get_open_positions()
    if not positions:
        logger.info("No open positions to close.")
        return 0

    closed = 0
    for pos in positions:
        direction = "SELL" if pos.type == 0 else "BUY"  # reverse to close
        price = get_current_price(pos.symbol)
        if price is None:
            continue

        close_price = price["bid"] if pos.type == 0 else price["ask"]

        if config.DRY_RUN:
            logger.info(
                f"[DRY_RUN] Closing ticket {pos.ticket} "
                f"{pos.symbol} {pos.volume} @ {close_price:.5f}"
            )
            closed += 1
            continue

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 10,
            "magic": 0,
            "comment": "",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Closed ticket {pos.ticket}")
            closed += 1
        else:
            err = result.comment if result else "Unknown"
            logger.error(f"Failed to close ticket {pos.ticket}: {err}")

    return closed


# ===========================================================================
# Trade History
# ===========================================================================

def get_daily_deals() -> list[dict]:
    """
    Fetch today's closed deals for P/L tracking.
    Returns list of dicts with keys: ticket, symbol, type, volume, profit, time.
    """
    if not MT5_AVAILABLE:
        return []

    now = datetime.now()
    start = datetime(now.year, now.month, now.day)

    deals = mt5.history_deals_get(start, now)
    if deals is None:
        return []

    result = []
    for d in deals:
        # Only include actual trade deals (entry/exit), skip balance ops
        if d.entry == 0 and d.profit == 0:
            continue
        result.append({
            "ticket": d.ticket,
            "symbol": d.symbol,
            "type": d.type,
            "volume": d.volume,
            "profit": d.profit,
            "commission": d.commission,
            "swap": d.swap,
            "time": datetime.fromtimestamp(d.time),
        })

    return result
