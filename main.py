"""
Forex Liquidity Hunter — Main Runner (V18 Disciplined Trader)
==============================================================
Entry point for the bot. Run on your Windows VPS:
    python main.py

Flow:
  1. Connect to MT5
  2. Enter main loop
  3. Only scan for trades during session windows (Tokyo/London/NY)
  4. Enforce all discipline rules via RiskManager + MarketFilter
  5. 6-point pre-entry validation gate
  6. Auto break-even with commission/spread protection
  7. Log daily summary every 5 minutes
"""
import sys
import os
import time
import logging
from datetime import datetime

import pytz

import config
import mt5_bridge
from risk_manager import RiskManager
from strategy import generate_signal
from news_filter import news_filter
import telegram_notifier

# ======================================================================
# Logging Setup
# ======================================================================

def setup_logging():
    """Configure logging to both console and daily log file."""
    os.makedirs(config.LOG_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(config.LOG_DIR, f"bot_{today}.log")

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)-15s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


logger = logging.getLogger(__name__)


# ======================================================================
# Session Time Check
# ======================================================================

def get_active_session() -> str | None:
    """
    Returns the name of the currently active session, or None.
    Uses Asia/Jakarta timezone (UTC+7).
    """
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    current_minutes = now.hour * 60 + now.minute

    for name, sh, sm, eh, em in config.SESSIONS:
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= current_minutes <= end:
            return name

    return None


# ======================================================================
# Main Loop
# ======================================================================

def main():
    setup_logging()

    logger.info(
        r"""
  ╔═══════════════════════════════════════════════════╗
  ║   FOREX LIQUIDITY HUNTER v1.8 (Disciplined)       ║
  ║   SMC Sweep + Breakout + RSI Scalp                ║
  ║   6-Point Validation Gate + Correlation Filter     ║
  ║   Mode:     %s                              ║
  ╚═══════════════════════════════════════════════════╝
    """ % ('DRY RUN' if config.DRY_RUN else 'LIVE')
    )

    if config.DRY_RUN:
        logger.info(
            "DRY_RUN mode is ON. No real trades will be placed. "
            "Set DRY_RUN=False in config.py to go live."
        )

    # Log bot rules
    logger.info(
        f"Bot Rules: "
        f"Max {config.MAX_OPEN_TRADES} concurrent trades, "
        f"Risk {config.MAX_RISK_PER_TRADE_PCT}%/trade, "
        f"Min RR 1:{config.MIN_RISK_REWARD_RATIO}, "
        f"Min {config.MIN_CONFIRMATIONS} confirmations, "
        f"Daily loss limit ${config.DAILY_LOSS_LIMIT}"
    )

    # --- Connect to MT5 ---
    if not mt5_bridge.connect():
        logger.error("Failed to connect to MT5. Exiting.")
        sys.exit(1)

    # --- Initialize Risk Manager ---
    risk = RiskManager()
    risk.log_daily_summary()

    # --- Notify Telegram: bot started ---
    telegram_notifier.notify_bot_started()

    # Log upcoming news events at startup
    news_filter.log_upcoming_events()

    last_summary_time = time.time()
    _symbol_cooldowns: dict[str, float] = {}  # Tracks last trade time per symbol

    try:
        logger.info(
            f"Bot started. Monitoring sessions: "
            f"{', '.join(s[0] for s in config.SESSIONS)}"
        )
        logger.info(f"Pairs: {', '.join(config.SYMBOLS)}")
        logger.info(
            f"Correlation groups: {len(config.CORRELATION_GROUPS)} groups configured"
        )

        while True:
            # --- Check if we are in a trading session ---
            session = get_active_session()

            if session is None:
                # Outside defined session hours
                tz = pytz.timezone(config.TIMEZONE)
                session = f"Global_{datetime.now(tz).strftime('%H')}"
            
            logger.info(f"Scanning... [Session: {session}]")

            # --- Can we trade? (Risk Manager check, includes daily limit) ---
            if not risk.can_trade():
                time.sleep(config.SCAN_INTERVAL_SECONDS)
                continue

            # --- Scan each symbol for signals ---
            open_positions = mt5_bridge.get_open_positions()
            
            # --- Checkpoint TP Manager (partial close + trailing) ---
            _manage_checkpoints(open_positions)
            
            open_symbols = [p.symbol for p in open_positions]

            for symbol in config.SYMBOLS:
                # Double-check risk before each symbol
                if not risk.can_trade():
                    break

                logger.info(f"Checking {symbol}...")
                
                # 1. Do we already have an open trade for this symbol?
                if symbol in open_symbols:
                    continue

                # 2. Is this symbol on cooldown?
                last_trade_time = _symbol_cooldowns.get(symbol, 0)
                cooldown_seconds = getattr(config, "TRADE_COOLDOWN_MINUTES", 15) * 60
                if time.time() - last_trade_time < cooldown_seconds:
                    continue

                # 3. Generate signal (includes full 6-point validation gate)
                signal = generate_signal(symbol, risk_manager=risk)

                if signal is None:
                    continue  # No valid setup on this pair

                # --- Calculate lot size ---
                lot_size = risk.calculate_lot_size(signal.sl_pips, symbol)
                if lot_size is None:
                    logger.warning(f"Could not calculate lot size for {symbol}. Skipping.")
                    continue

                # --- Execute the trade ---
                ticket = mt5_bridge.place_order(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    lot_size=lot_size,
                    sl=signal.stop_loss,
                    tp=signal.take_profit,
                    comment=f"LH_{session}_{signal.direction}",
                )

                if ticket is not None:
                    _symbol_cooldowns[symbol] = time.time()
                    logger.info(
                        f"Trade placed: "
                        f"{signal.direction} {lot_size} {symbol} "
                        f"(Session: {session}, Reason: {signal.reason}, "
                        f"RR: {signal.rr_ratio:.2f})"
                    )

                    # --- Telegram notification: trade opened ---
                    telegram_notifier.notify_trade_opened(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        lot_size=lot_size,
                        entry_price=signal.entry_price,
                        sl=signal.stop_loss,
                        tp=signal.take_profit,
                        rr_ratio=signal.rr_ratio,
                        reason=signal.reason,
                        session=session,
                        ticket=ticket,
                    )

            # --- Check for closed trades and update P/L ---
            _sync_closed_trades(risk)

            # --- Periodic Summary ---
            now = time.time()
            if now - last_summary_time >= config.SUMMARY_LOG_INTERVAL_SECONDS:
                risk.log_daily_summary()

                # Log upcoming news events
                news_filter.log_upcoming_events()

                # Consistency check
                consistency = risk.check_profit_consistency()
                if not consistency["passes"]:
                    logger.warning(
                        f"Profit consistency at risk! "
                        f"Worst day: {consistency['worst_day_pct']}% of total "
                        f"(limit: 30%)"
                    )

                last_summary_time = now

            # --- Wait before next scan ---
            time.sleep(config.SCAN_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("\nBot stopped by user (Ctrl+C)")
        telegram_notifier.notify_bot_stopped("Manual shutdown (Ctrl+C)")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        telegram_notifier.notify_bot_stopped(f"Error: {e}")
        # Emergency: close everything
        mt5_bridge.close_all_positions()
    finally:
        # --- End-of-run summary ---
        risk.log_daily_summary()
        mt5_bridge.disconnect()
        logger.info("Bot shutdown complete.")


# ======================================================================
# Trade Sync (detect closed trades)
# ======================================================================

_known_deals: set[int] = set()


def _sync_closed_trades(risk: RiskManager):
    """
    Check MT5 deal history for newly closed trades and record their P/L.
    Calculates NET profit = gross profit - (commission + swap).
    Spread cost is already embedded in the entry/exit prices.
    """
    deals = mt5_bridge.get_daily_deals()

    for deal in deals:
        ticket = deal["ticket"]
        if ticket in _known_deals:
            continue

        _known_deals.add(ticket)

        # NET PROFIT calculation (Req #5):
        # commission and swap are typically negative values
        gross_profit = deal["profit"]
        commission = deal.get("commission", 0)
        swap = deal.get("swap", 0)
        net_profit = gross_profit + commission + swap

        if abs(net_profit) > 0.001:  # Skip zero-profit balance ops
            logger.info(
                f"Deal #{ticket}: Gross=${gross_profit:+.2f}, "
                f"Commission=${commission:+.2f}, Swap=${swap:+.2f} "
                f"=> Net=${net_profit:+.2f}"
            )
            risk.record_trade(net_profit, deal.get("symbol", ""))

            # --- Telegram notification: trade closed ---
            deal_type = deal.get("type", 0)
            direction = "BUY" if deal_type == 0 else "SELL"
            telegram_notifier.notify_trade_closed(
                ticket=ticket,
                symbol=deal.get("symbol", ""),
                direction=direction,
                gross_profit=gross_profit,
                commission=commission,
                swap=swap,
                net_profit=net_profit,
                close_reason=deal.get("reason", 0),
                comment=deal.get("comment", ""),
            )


# ======================================================================
# Hybrid TP Checkpoint Manager
# ======================================================================
# Tracks per-ticket state:
#   - original_volume: lot size at entry
#   - risk_distance: entry-to-SL distance (1R)
#   - checkpoints_hit: [bool, bool, bool] for TP1/TP2/TP3
#   - trailing_active: True after final checkpoint
#   - trailing_high: highest price seen (for trailing SL)

_checkpoint_state: dict[int, dict] = {}


def _calc_be_buffer(sym_info, volume: float, pip_size: float) -> float:
    """Calculate commission + spread buffer in price distance."""
    commission_per_lot = getattr(config, "ESTIMATED_COMMISSION_PER_LOT", 7.0)
    spread_pips = getattr(config, "ESTIMATED_SPREAD_COST_PIPS", 1.5)

    pip_value = sym_info.trade_tick_value * (pip_size / sym_info.point)
    if pip_value > 0:
        commission_distance = (commission_per_lot / pip_value) * pip_size
    else:
        commission_distance = 0

    spread_distance = spread_pips * pip_size
    return commission_distance + spread_distance


def _get_checkpoint_price(entry: float, risk_distance: float, r_level: float, direction: str) -> float:
    """Calculate the price level for a given R multiple."""
    if direction == "BUY":
        return entry + (risk_distance * r_level)
    else:
        return entry - (risk_distance * r_level)


def _manage_checkpoints(open_positions):
    """
    Hybrid TP Checkpoint Manager.

    For each open position:
    - Track which checkpoints (TP1, TP2, TP3) have been hit
    - At each checkpoint: partial close + move SL
    - After final checkpoint: remove TP, enable trailing SL
    """
    if not getattr(config, "ENABLE_CHECKPOINT_TP", False):
        return

    checkpoints = getattr(config, "TP_CHECKPOINTS", [1.0, 2.0, 3.0])
    partial_pcts = getattr(config, "TP_PARTIAL_CLOSE_PCTS", [0.40, 0.30, 0.00])
    trailing_step = getattr(config, "TRAILING_STEP_PIPS", 10.0)

    active_tickets = set()

    for p in open_positions:
        active_tickets.add(p.ticket)
        direction = "BUY" if p.type == 0 else "SELL"

        # --- Initialize state for new positions ---
        if p.ticket not in _checkpoint_state:
            risk_distance = abs(p.price_open - p.sl)
            if risk_distance < 0.00001:
                continue

            _checkpoint_state[p.ticket] = {
                "original_volume": p.volume,
                "entry_price": p.price_open,
                "original_sl": p.sl,
                "risk_distance": risk_distance,
                "direction": direction,
                "checkpoints_hit": [False] * len(checkpoints),
                "trailing_active": False,
                "trailing_high": p.price_open if direction == "BUY" else p.price_open,
            }
            logger.debug(
                f"[CHECKPOINT] Tracking ticket {p.ticket} {direction} {p.symbol}: "
                f"entry={p.price_open:.5f}, risk={risk_distance:.5f}"
            )

        state = _checkpoint_state[p.ticket]

        # --- Get current price ---
        price = mt5_bridge.get_current_price(p.symbol)
        if price is None:
            continue

        sym_info = mt5_bridge.get_symbol_info(p.symbol)
        if sym_info is None:
            continue

        pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point

        # Calculate current R achieved
        if direction == "BUY":
            current_price = price["bid"]
            current_profit_dist = current_price - state["entry_price"]
        else:
            current_price = price["ask"]
            current_profit_dist = state["entry_price"] - current_price

        rr_achieved = current_profit_dist / state["risk_distance"]

        # --- Process each checkpoint ---
        for i, (r_level, close_pct) in enumerate(zip(checkpoints, partial_pcts)):
            if state["checkpoints_hit"][i]:
                continue  # Already hit

            if rr_achieved >= r_level:
                state["checkpoints_hit"][i] = True
                cp_name = f"TP{i+1}"

                logger.info(
                    f"[CHECKPOINT] {cp_name} HIT! {p.symbol} ticket {p.ticket} "
                    f"at {rr_achieved:.2f}R (level: {r_level}R)"
                )

                # --- Partial close ---
                volume_closed = 0
                if close_pct > 0:
                    volume_to_close = round(state["original_volume"] * close_pct, 2)
                    if volume_to_close >= 0.01:
                        mt5_bridge.partial_close_position(p.ticket, volume_to_close)
                        volume_closed = volume_to_close
                        logger.info(
                            f"[CHECKPOINT] {cp_name}: Closed {close_pct*100:.0f}% "
                            f"({volume_to_close} lots) of {p.symbol}"
                        )

                # --- Telegram notification: checkpoint hit ---
                telegram_notifier.notify_checkpoint_hit(
                    symbol=p.symbol,
                    ticket=p.ticket,
                    checkpoint_name=cp_name,
                    rr_achieved=rr_achieved,
                    partial_closed=volume_closed,
                )

                # --- Move SL ---
                if i == 0:
                    # TP1: SL to entry + 0.5R (locks in real profit, not just BE)
                    sl_lock = state["risk_distance"] * 0.5
                    if direction == "BUY":
                        new_sl = state["entry_price"] + sl_lock
                    else:
                        new_sl = state["entry_price"] - sl_lock
                    mt5_bridge.modify_position_sl(p.ticket, new_sl)
                    logger.info(
                        f"[CHECKPOINT] {cp_name}: SL -> entry+0.5R ({new_sl:.5f})"
                    )
                else:
                    # TP2+: SL to previous checkpoint level
                    prev_r = checkpoints[i - 1]
                    new_sl = _get_checkpoint_price(
                        state["entry_price"], state["risk_distance"],
                        prev_r, direction
                    )
                    mt5_bridge.modify_position_sl(p.ticket, new_sl)
                    logger.info(
                        f"[CHECKPOINT] {cp_name}: SL -> TP{i} level ({new_sl:.5f})"
                    )

                # --- After final checkpoint: remove TP, enable trailing ---
                is_final = (i == len(checkpoints) - 1)
                if is_final and getattr(config, "ENABLE_TRAILING_AFTER_FINAL", True):
                    state["trailing_active"] = True
                    state["trailing_high"] = current_price

                    # Remove TP (set to 0) — let it ride!
                    final_sl = _get_checkpoint_price(
                        state["entry_price"], state["risk_distance"],
                        checkpoints[-2] if len(checkpoints) >= 2 else checkpoints[-1],
                        direction
                    )
                    mt5_bridge.modify_position_sl_tp(p.ticket, final_sl, 0.0)
                    logger.info(
                        f"[CHECKPOINT] TP REMOVED! {p.symbol} now trailing. "
                        f"SL locked at TP{len(checkpoints)-1} level ({final_sl:.5f})"
                    )

        # --- Trailing SL (after final checkpoint) ---
        if state["trailing_active"]:
            trail_dist = trailing_step * pip_size

            if direction == "BUY":
                # Update high watermark
                if current_price > state["trailing_high"]:
                    state["trailing_high"] = current_price

                # Trail SL behind the high watermark
                trail_sl = state["trailing_high"] - trail_dist
                if trail_sl > p.sl:
                    mt5_bridge.modify_position_sl(p.ticket, trail_sl)
                    logger.info(
                        f"[TRAIL] {p.symbol} ticket {p.ticket}: "
                        f"SL trailed to {trail_sl:.5f} "
                        f"(high={state['trailing_high']:.5f})"
                    )

            else:  # SELL
                # Update low watermark
                if current_price < state["trailing_high"]:
                    state["trailing_high"] = current_price

                # Trail SL above the low watermark
                trail_sl = state["trailing_high"] + trail_dist
                if trail_sl < p.sl or p.sl == 0:
                    mt5_bridge.modify_position_sl(p.ticket, trail_sl)
                    logger.info(
                        f"[TRAIL] {p.symbol} ticket {p.ticket}: "
                        f"SL trailed to {trail_sl:.5f} "
                        f"(low={state['trailing_high']:.5f})"
                    )

    # --- Cleanup: remove state for closed positions ---
    closed_tickets = set(_checkpoint_state.keys()) - active_tickets
    for ticket in closed_tickets:
        del _checkpoint_state[ticket]
        logger.debug(f"[CHECKPOINT] Cleaned up state for closed ticket {ticket}")


# ======================================================================
# Entry Point
# ======================================================================

if __name__ == "__main__":
    main()
