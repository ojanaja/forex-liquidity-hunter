"""
Forex Liquidity Hunter — Dry Run Trade Tracker
=================================================
Tracks virtual trades when DRY_RUN=True.
Monitors price vs SL/TP and automatically closes trades
when either level is hit, sending Telegram notifications.

Persists state to disk so trades survive bot restarts.
"""
import json
import logging
import os
import time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

import pytz

import config
import mt5_bridge
import telegram_notifier

logger = logging.getLogger(__name__)

# Persistence file
_TRADES_FILE = os.path.join(config.LOG_DIR, "dry_run_trades.json")

# Virtual ticket counter (negative to distinguish from real tickets)
_next_ticket = -100


@dataclass
class VirtualTrade:
    """A simulated trade for dry run mode."""
    ticket: int
    symbol: str
    direction: str           # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    session: str
    reason: str
    rr_ratio: float
    open_time: str           # ISO format string
    # --- Filled when closed ---
    close_price: float = 0.0
    close_time: str = ""
    close_reason: str = ""   # "SL", "TP", "MANUAL"
    pnl: float = 0.0
    is_closed: bool = False
    pip_size: float = 0.0001  # default for 5-digit pairs
    pip_value: float = 10.0   # default


class DryRunTracker:
    """
    Manages virtual trades for dry run mode.
    Checks price vs SL/TP every scan cycle and auto-closes trades.
    """

    def __init__(self):
        self.open_trades: dict[int, VirtualTrade] = {}
        self.closed_trades: list[VirtualTrade] = []
        self._load_state()
        logger.info(
            f"[DRY_RUN TRACKER] Initialized: "
            f"{len(self.open_trades)} open, "
            f"{len(self.closed_trades)} closed trades loaded"
        )

    # ===================================================================
    # Open a new virtual trade
    # ===================================================================

    def open_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        lot_size: float,
        session: str,
        reason: str,
        rr_ratio: float,
    ) -> int:
        """Register a new virtual trade. Returns the virtual ticket number."""
        global _next_ticket
        ticket = _next_ticket
        _next_ticket -= 1

        # Get pip info for P/L calculation
        pip_size = 0.0001
        pip_value = 10.0
        try:
            sym_info = mt5_bridge.get_symbol_info(symbol)
            if sym_info:
                pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
                pip_value = sym_info.trade_tick_value * (pip_size / sym_info.point)
        except Exception:
            pass

        tz = pytz.timezone(config.TIMEZONE)
        trade = VirtualTrade(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=lot_size,
            session=session,
            reason=reason,
            rr_ratio=rr_ratio,
            open_time=datetime.now(tz).isoformat(),
            pip_size=pip_size,
            pip_value=pip_value,
        )

        self.open_trades[ticket] = trade
        self._save_state()

        logger.info(
            f"[DRY_RUN TRACKER] Opened: {direction} {lot_size} {symbol} "
            f"@ {entry_price:.5f} SL={stop_loss:.5f} TP={take_profit:.5f} "
            f"ticket={ticket}"
        )
        return ticket

    # ===================================================================
    # Check all open trades for SL/TP hit
    # ===================================================================

    def check_trades(self):
        """
        Check current price vs SL/TP for each open virtual trade.
        Auto-closes trades that hit either level.
        """
        if not self.open_trades:
            return

        tickets_to_close = []

        for ticket, trade in self.open_trades.items():
            price = mt5_bridge.get_current_price(trade.symbol)
            if price is None:
                continue

            close_reason = None
            close_price = 0.0

            if trade.direction == "BUY":
                current_bid = price["bid"]
                # Check SL hit (bid drops to or below SL)
                if current_bid <= trade.stop_loss:
                    close_reason = "SL"
                    close_price = trade.stop_loss
                # Check TP hit (bid rises to or above TP)
                elif current_bid >= trade.take_profit:
                    close_reason = "TP"
                    close_price = trade.take_profit

            else:  # SELL
                current_ask = price["ask"]
                # Check SL hit (ask rises to or above SL)
                if current_ask >= trade.stop_loss:
                    close_reason = "SL"
                    close_price = trade.stop_loss
                # Check TP hit (ask drops to or below TP)
                elif current_ask <= trade.take_profit:
                    close_reason = "TP"
                    close_price = trade.take_profit

            if close_reason:
                tickets_to_close.append((ticket, close_price, close_reason))

        # Close trades outside the iteration
        for ticket, close_price, close_reason in tickets_to_close:
            self._close_trade(ticket, close_price, close_reason)

    # ===================================================================
    # Close a virtual trade
    # ===================================================================

    def _close_trade(self, ticket: int, close_price: float, reason: str):
        """Close a virtual trade and calculate P/L."""
        trade = self.open_trades.get(ticket)
        if trade is None:
            return

        tz = pytz.timezone(config.TIMEZONE)

        # Calculate P/L in pips and dollars
        if trade.direction == "BUY":
            pip_distance = (close_price - trade.entry_price) / trade.pip_size
        else:
            pip_distance = (trade.entry_price - close_price) / trade.pip_size

        pnl = pip_distance * trade.pip_value * trade.lot_size

        # Update trade object
        trade.close_price = close_price
        trade.close_time = datetime.now(tz).isoformat()
        trade.close_reason = reason
        trade.pnl = round(pnl, 2)
        trade.is_closed = True

        # Move from open to closed
        del self.open_trades[ticket]
        self.closed_trades.append(trade)
        self._save_state()

        emoji = "🎯" if reason == "TP" else "🛑"
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"[DRY_RUN TRACKER] {emoji} Trade CLOSED by {reason}: "
            f"{trade.symbol} {trade.direction} @ {close_price:.5f} "
            f"P/L: ${pnl:+.2f} (ticket={ticket})"
        )

        # Send Telegram notification
        telegram_notifier.notify_trade_closed(
            ticket=ticket,
            symbol=trade.symbol,
            direction=trade.direction,
            gross_profit=pnl,
            commission=0.0,
            swap=0.0,
            net_profit=pnl,
            close_reason=5 if reason == "TP" else 4,  # 4=SL, 5=TP
            comment=f"DRY_RUN_{reason}",
        )

    # ===================================================================
    # Query methods
    # ===================================================================

    def get_open_trades(self) -> list:
        """Return open virtual trades as Position-compatible objects."""
        positions = []
        for trade in self.open_trades.values():
            positions.append(mt5_bridge.Position(
                ticket=trade.ticket,
                symbol=trade.symbol,
                type=0 if trade.direction == "BUY" else 1,
                volume=trade.lot_size,
                price_open=trade.entry_price,
                sl=trade.stop_loss,
                tp=trade.take_profit,
                profit=0.0,  # Not calculated for simplicity
                time=datetime.fromisoformat(trade.open_time),
            ))
        return positions

    def get_open_symbols(self) -> list[str]:
        """Return list of symbols with open virtual trades."""
        return [t.symbol for t in self.open_trades.values()]

    def get_closed_trades(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[VirtualTrade]:
        """
        Return closed trades filtered by date range.
        If no dates given, returns all closed trades.
        """
        if start_date is None and end_date is None:
            return self.closed_trades.copy()

        tz = pytz.timezone(config.TIMEZONE)
        result = []

        for trade in self.closed_trades:
            if not trade.close_time:
                continue
            try:
                close_dt = datetime.fromisoformat(trade.close_time)
                trade_date = close_dt.date()
            except (ValueError, TypeError):
                continue

            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            result.append(trade)

        return result

    def get_trades_today(self) -> list[VirtualTrade]:
        """Return all trades (open and closed) from today."""
        today = date.today()
        closed_today = self.get_closed_trades(start_date=today, end_date=today)
        # Include currently open trades opened today
        tz = pytz.timezone(config.TIMEZONE)
        open_today = []
        for trade in self.open_trades.values():
            try:
                open_dt = datetime.fromisoformat(trade.open_time)
                if open_dt.date() == today:
                    open_today.append(trade)
            except (ValueError, TypeError):
                pass
        return closed_today + open_today

    def get_stats(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """Calculate statistics for a date range."""
        trades = self.get_closed_trades(start_date, end_date)

        total = len(trades)
        wins = sum(1 for t in trades if t.pnl >= 0)
        losses = sum(1 for t in trades if t.pnl < 0)
        total_pnl = sum(t.pnl for t in trades)
        win_rate = (wins / total * 100) if total > 0 else 0.0

        # Per-pair stats
        pair_stats = {}
        for t in trades:
            if t.symbol not in pair_stats:
                pair_stats[t.symbol] = {
                    "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0
                }
            pair_stats[t.symbol]["trades"] += 1
            pair_stats[t.symbol]["pnl"] += t.pnl
            if t.pnl >= 0:
                pair_stats[t.symbol]["wins"] += 1
            else:
                pair_stats[t.symbol]["losses"] += 1

        # Best and worst trades
        best_trade = max(trades, key=lambda t: t.pnl) if trades else None
        worst_trade = min(trades, key=lambda t: t.pnl) if trades else None

        # SL/TP breakdown
        sl_count = sum(1 for t in trades if t.close_reason == "SL")
        tp_count = sum(1 for t in trades if t.close_reason == "TP")

        # Max drawdown calculation
        running_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for t in sorted(trades, key=lambda x: x.close_time or ""):
            running_pnl += t.pnl
            if running_pnl > peak:
                peak = running_pnl
            dd = peak - running_pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Profit factor
        gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
        gross_losses = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf')

        # Avg RR achieved (only for closed trades with known entry/exit)
        avg_rr = 0.0
        rr_count = 0
        for t in trades:
            if t.pip_size > 0 and t.stop_loss != 0:
                sl_dist = abs(t.entry_price - t.stop_loss) / t.pip_size
                if sl_dist > 0:
                    if t.direction == "BUY":
                        actual_dist = (t.close_price - t.entry_price) / t.pip_size
                    else:
                        actual_dist = (t.entry_price - t.close_price) / t.pip_size
                    avg_rr += actual_dist / sl_dist
                    rr_count += 1

        if rr_count > 0:
            avg_rr /= rr_count

        # Longest winning/losing streaks
        longest_win_streak = 0
        longest_loss_streak = 0
        current_win = 0
        current_loss = 0
        for t in sorted(trades, key=lambda x: x.close_time or ""):
            if t.pnl >= 0:
                current_win += 1
                current_loss = 0
            else:
                current_loss += 1
                current_win = 0
            longest_win_streak = max(longest_win_streak, current_win)
            longest_loss_streak = max(longest_loss_streak, current_loss)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / total, 2) if total > 0 else 0.0,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "pair_stats": pair_stats,
            "sl_count": sl_count,
            "tp_count": tp_count,
            "max_drawdown": round(max_drawdown, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "∞",
            "avg_rr_achieved": round(avg_rr, 2),
            "longest_win_streak": longest_win_streak,
            "longest_loss_streak": longest_loss_streak,
        }

    # ===================================================================
    # Persistence
    # ===================================================================

    def _save_state(self):
        """Save all trades to disk."""
        os.makedirs(config.LOG_DIR, exist_ok=True)
        state = {
            "open_trades": {
                str(k): self._trade_to_dict(v)
                for k, v in self.open_trades.items()
            },
            "closed_trades": [
                self._trade_to_dict(t) for t in self.closed_trades
            ],
            "next_ticket": _next_ticket,
        }
        try:
            with open(_TRADES_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except IOError as e:
            logger.error(f"[DRY_RUN TRACKER] Failed to save state: {e}")

    def _load_state(self):
        """Load trades from disk."""
        global _next_ticket

        if not os.path.exists(_TRADES_FILE):
            return

        try:
            with open(_TRADES_FILE, "r") as f:
                state = json.load(f)

            _next_ticket = state.get("next_ticket", -100)

            for key, data in state.get("open_trades", {}).items():
                trade = self._dict_to_trade(data)
                self.open_trades[trade.ticket] = trade

            for data in state.get("closed_trades", []):
                trade = self._dict_to_trade(data)
                self.closed_trades.append(trade)

            logger.info(
                f"[DRY_RUN TRACKER] Loaded state: "
                f"{len(self.open_trades)} open, "
                f"{len(self.closed_trades)} closed"
            )
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"[DRY_RUN TRACKER] Failed to load state: {e}")

    @staticmethod
    def _trade_to_dict(trade: VirtualTrade) -> dict:
        return {
            "ticket": trade.ticket,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "entry_price": trade.entry_price,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "lot_size": trade.lot_size,
            "session": trade.session,
            "reason": trade.reason,
            "rr_ratio": trade.rr_ratio,
            "open_time": trade.open_time,
            "close_price": trade.close_price,
            "close_time": trade.close_time,
            "close_reason": trade.close_reason,
            "pnl": trade.pnl,
            "is_closed": trade.is_closed,
            "pip_size": trade.pip_size,
            "pip_value": trade.pip_value,
        }

    @staticmethod
    def _dict_to_trade(data: dict) -> VirtualTrade:
        return VirtualTrade(
            ticket=data["ticket"],
            symbol=data["symbol"],
            direction=data["direction"],
            entry_price=data["entry_price"],
            stop_loss=data["stop_loss"],
            take_profit=data["take_profit"],
            lot_size=data["lot_size"],
            session=data.get("session", ""),
            reason=data.get("reason", ""),
            rr_ratio=data.get("rr_ratio", 0.0),
            open_time=data.get("open_time", ""),
            close_price=data.get("close_price", 0.0),
            close_time=data.get("close_time", ""),
            close_reason=data.get("close_reason", ""),
            pnl=data.get("pnl", 0.0),
            is_closed=data.get("is_closed", False),
            pip_size=data.get("pip_size", 0.0001),
            pip_value=data.get("pip_value", 10.0),
        )
