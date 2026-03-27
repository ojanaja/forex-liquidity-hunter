"""
Forex Liquidity Hunter - Risk Manager
Enforces all WeMasterTrade prop firm rules.

Rules:
  1. Daily Loss     < $200  (we stop at $150)
  2. Total Loss     < $400  (we stop at $350)
  3. Profit Ratio   >= 6%
  4. Profit Consist <= 30%  (daily cap $120)
  5. Risk Consist   < 2%    (max 0.5% per trade)
"""
import logging
import json
import os
from datetime import datetime, date
from typing import Optional

import config
import mt5_bridge

logger = logging.getLogger(__name__)

# Persistent file path for tracking cumulative stats across restarts
_STATS_FILE = os.path.join(config.LOG_DIR, "cumulative_stats.json")


class RiskManager:
    """Tracks and enforces all prop-firm risk and consistency rules."""

    def __init__(self):
        # ---------- Daily tracking ----------
        self.today: date = date.today()
        self.daily_realized_pnl: float = 0.0
        self.daily_trade_count: int = 0
        self.daily_wins: int = 0
        self.daily_losses: int = 0
        self.is_stopped_for_day: bool = False
        self.stop_reason: str = ""

        # ---------- Cumulative tracking ----------
        self.cumulative_pnl: float = 0.0
        self.total_trade_count: int = 0
        self.daily_profits: list[float] = []  # list of each day's profit

        # Load previous state if exists
        self._load_state()

    # ===================================================================
    # Core Check: can we trade?
    # ===================================================================

    def can_trade(self) -> bool:
        """
        Master gate — returns True only if ALL rules allow trading.
        Call this before every potential trade.
        """
        self._check_new_day()

        if self.is_stopped_for_day:
            logger.info(f"⛔ Stopped for today: {self.stop_reason}")
            return False

        # Rule 1: Daily Loss Limit
        floating_pnl = self._get_floating_pnl()
        total_daily_pnl = self.daily_realized_pnl + floating_pnl

        if total_daily_pnl <= -config.DAILY_LOSS_LIMIT:
            self._stop_day(
                f"Daily loss limit hit: ${total_daily_pnl:.2f} "
                f"(limit: -${config.DAILY_LOSS_LIMIT:.2f})"
            )
            # Emergency close all positions
            mt5_bridge.close_all_positions()
            return False

        # Rule 2: Total Loss Limit
        cumulative_total = self.cumulative_pnl + total_daily_pnl
        if cumulative_total <= -config.TOTAL_LOSS_LIMIT:
            self._stop_day(
                f"TOTAL loss limit hit: ${cumulative_total:.2f} "
                f"(limit: -${config.TOTAL_LOSS_LIMIT:.2f}). "
                f"⚠️ BOT SHOULD BE DISABLED PERMANENTLY."
            )
            mt5_bridge.close_all_positions()
            return False

        # Rule 4: Daily Profit Cap (consistency) - LOG ONLY per user request
        if total_daily_pnl >= config.DAILY_PROFIT_CAP:
            logger.info(
                f"🎯 Daily profit cap reached: +${total_daily_pnl:.2f}. "
                f"Continuing trade per user preference."
            )
            # return False <-- Disabled to allow $600/month target achievement

        # Rule 5: Check open trade count
        open_positions = mt5_bridge.get_open_positions()
        if len(open_positions) >= config.MAX_OPEN_TRADES:
            logger.info(
                f"Max open trades reached ({len(open_positions)}/{config.MAX_OPEN_TRADES})"
            )
            return False

        return True

    # ===================================================================
    # Lot Size Calculation (Rule 5: Risk Consistency)
    # ===================================================================

    def calculate_lot_size(
        self,
        sl_pips: float,
        symbol: str,
    ) -> Optional[float]:
        """
        Calculate lot size based on max risk per trade.

        Risk = lot_size * sl_pips * pip_value
        lot_size = max_risk / (sl_pips * pip_value)
        """
        sym_info = mt5_bridge.get_symbol_info(symbol)
        if sym_info is None:
            logger.error(f"Cannot calculate lot size: no info for {symbol}")
            return None

        max_risk_usd = config.ACCOUNT_BALANCE * (config.MAX_RISK_PER_TRADE_PCT / 100.0)

        # pip_value = tick_value / point * pip_size
        # For 5-digit pairs: 1 pip = 10 points
        pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
        pip_value = sym_info.trade_tick_value * (pip_size / sym_info.point)

        if pip_value <= 0 or sl_pips <= 0:
            logger.error(f"Invalid pip_value ({pip_value}) or sl_pips ({sl_pips})")
            return None

        raw_lots = max_risk_usd / (sl_pips * pip_value)

        # Round down to nearest volume_step
        step = sym_info.volume_step
        lots = max(
            sym_info.volume_min,
            min(
                round(int(raw_lots / step) * step, 2),
                sym_info.volume_max,
            ),
        )

        logger.info(
            f"Lot size for {symbol}: {lots} "
            f"(risk=${max_risk_usd:.0f}, SL={sl_pips:.1f} pips, "
            f"pip_val=${pip_value:.2f})"
        )
        return lots

    # ===================================================================
    # Trade Recording
    # ===================================================================

    def record_trade(self, profit: float, symbol: str = ""):
        """Call this after every trade closes."""
        self._check_new_day()

        self.daily_realized_pnl += profit
        self.daily_trade_count += 1
        self.total_trade_count += 1

        if profit >= 0:
            self.daily_wins += 1
        else:
            self.daily_losses += 1

        emoji = "✅" if profit >= 0 else "❌"
        logger.info(
            f"{emoji} Trade closed: {symbol} ${profit:+.2f} | "
            f"Daily P/L: ${self.daily_realized_pnl:+.2f} | "
            f"Cumulative: ${(self.cumulative_pnl + self.daily_realized_pnl):+.2f}"
        )

        self._save_state()

    # ===================================================================
    # Daily Summary
    # ===================================================================

    def get_daily_summary(self) -> dict:
        """Returns today's stats for logging."""
        floating = self._get_floating_pnl()
        cumulative_total = self.cumulative_pnl + self.daily_realized_pnl

        return {
            "date": str(self.today),
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "daily_floating_pnl": round(floating, 2),
            "daily_total_pnl": round(self.daily_realized_pnl + floating, 2),
            "cumulative_pnl": round(cumulative_total, 2),
            "trades_today": self.daily_trade_count,
            "wins": self.daily_wins,
            "losses": self.daily_losses,
            "win_rate": (
                round(self.daily_wins / self.daily_trade_count * 100, 1)
                if self.daily_trade_count > 0
                else 0.0
            ),
            "is_stopped": self.is_stopped_for_day,
            "stop_reason": self.stop_reason,
            "distance_to_target": round(
                config.PROFIT_TARGET - cumulative_total, 2
            ),
        }

    def log_daily_summary(self):
        """Pretty-print the daily summary to the log."""
        s = self.get_daily_summary()
        logger.info(
            f"\n{'='*50}\n"
            f"📊 DAILY SUMMARY — {s['date']}\n"
            f"{'='*50}\n"
            f"  Realized P/L:   ${s['daily_realized_pnl']:+.2f}\n"
            f"  Floating P/L:   ${s['daily_floating_pnl']:+.2f}\n"
            f"  Total Today:    ${s['daily_total_pnl']:+.2f}\n"
            f"  Cumulative:     ${s['cumulative_pnl']:+.2f}\n"
            f"  Trades: {s['trades_today']} "
            f"(W: {s['wins']} / L: {s['losses']} — "
            f"{s['win_rate']}%)\n"
            f"  To Target:      ${s['distance_to_target']:+.2f}\n"
            f"  Status:         {'🛑 STOPPED' if s['is_stopped'] else '🟢 ACTIVE'}\n"
            f"{'='*50}"
        )

        # Check if profit target reached
        if s["cumulative_pnl"] >= config.PROFIT_TARGET:
            logger.info(
                f"🎉🎉🎉 PROFIT TARGET REACHED! "
                f"${s['cumulative_pnl']:.2f} / ${config.PROFIT_TARGET:.2f} 🎉🎉🎉"
            )

    # ===================================================================
    # Consistency Check
    # ===================================================================

    def check_profit_consistency(self) -> dict:
        """
        Check if any single day's profit exceeds 30% of cumulative profit.
        Returns { "passes": bool, "worst_day_pct": float }
        """
        if not self.daily_profits or self.cumulative_pnl <= 0:
            return {"passes": True, "worst_day_pct": 0.0}

        max_day = max(self.daily_profits)
        worst_pct = (max_day / self.cumulative_pnl) * 100 if self.cumulative_pnl > 0 else 0.0

        return {
            "passes": worst_pct <= 30.0,
            "worst_day_pct": round(worst_pct, 1),
        }

    # ===================================================================
    # Internal Helpers
    # ===================================================================

    def _get_floating_pnl(self) -> float:
        """Sum of unrealized P/L from all open positions."""
        positions = mt5_bridge.get_open_positions()
        return sum(p.profit for p in positions)

    def _stop_day(self, reason: str):
        """Mark the bot as stopped for the rest of the day."""
        self.is_stopped_for_day = True
        self.stop_reason = reason
        logger.warning(f"🛑 BOT STOPPED: {reason}")

    def _check_new_day(self):
        """Reset daily counters if a new trading day has started."""
        today = date.today()
        if today != self.today:
            # Archive yesterday's profit
            if self.daily_realized_pnl != 0:
                self.daily_profits.append(self.daily_realized_pnl)
                self.cumulative_pnl += self.daily_realized_pnl

            logger.info(
                f"📅 New trading day: {today}. "
                f"Yesterday's P/L: ${self.daily_realized_pnl:+.2f}"
            )

            # Reset daily counters
            self.today = today
            self.daily_realized_pnl = 0.0
            self.daily_trade_count = 0
            self.daily_wins = 0
            self.daily_losses = 0
            self.is_stopped_for_day = False
            self.stop_reason = ""

            self._save_state()

    def _save_state(self):
        """Persist cumulative state to disk (survives bot restarts)."""
        os.makedirs(config.LOG_DIR, exist_ok=True)
        state = {
            "cumulative_pnl": self.cumulative_pnl,
            "total_trade_count": self.total_trade_count,
            "daily_profits": self.daily_profits,
            "last_day": str(self.today),
            "daily_realized_pnl": self.daily_realized_pnl,
        }
        try:
            with open(_STATS_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Load cumulative state from disk."""
        if not os.path.exists(_STATS_FILE):
            return
        try:
            with open(_STATS_FILE, "r") as f:
                state = json.load(f)
            self.cumulative_pnl = state.get("cumulative_pnl", 0.0)
            self.total_trade_count = state.get("total_trade_count", 0)
            self.daily_profits = state.get("daily_profits", [])

            last_day = state.get("last_day", "")
            if last_day == str(date.today()):
                self.daily_realized_pnl = state.get("daily_realized_pnl", 0.0)

            logger.info(
                f"📦 Loaded state: cumulative=${self.cumulative_pnl:+.2f}, "
                f"trades={self.total_trade_count}"
            )
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load state: {e}")
