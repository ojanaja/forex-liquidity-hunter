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
        f"Max {config.MAX_TRADES_PER_DAY} trades/day, "
        f"Max {config.MAX_OPEN_TRADES} open, "
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
            
            # --- Auto Break-Even Manager (with commission/spread) ---
            _manage_auto_break_even(open_positions)
            
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

            # --- Check for closed trades and update P/L ---
            _sync_closed_trades(risk)

            # --- Periodic Summary ---
            now = time.time()
            if now - last_summary_time >= config.SUMMARY_LOG_INTERVAL_SECONDS:
                risk.log_daily_summary()

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
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
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


# ======================================================================
# Auto Break-Even Manager (with Commission + Spread Protection)
# ======================================================================

def _manage_auto_break_even(open_positions):
    """
    Checks all open positions. If profit exceeds the risk threshold (e.g., 1.1R),
    moves the Stop Loss to Entry Price + Commission + Spread.
    This ensures the trade cannot result in a net loss even if SL is hit.
    """
    if not getattr(config, "AUTO_BREAK_EVEN", False):
        return

    ratio_threshold = getattr(config, "BE_ACTIVATION_RATIO", 1.0)
    
    for p in open_positions:
        # If SL is already at or past entry, we don't need to break even
        if p.type == 0 and p.sl >= p.price_open:  # BUY
            continue
        if p.type == 1 and p.sl > 0 and p.sl <= p.price_open:  # SELL
            continue

        price = mt5_bridge.get_current_price(p.symbol)
        if price is None:
            continue

        sym_info = mt5_bridge.get_symbol_info(p.symbol)
        if sym_info is None:
            continue

        pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point

        # Calculate 1R distance (Entry to original SL)
        risk_distance = abs(p.price_open - p.sl)
        
        # Avoid division by zero
        if risk_distance < 0.00001:
            continue

        # Calculate commission + spread buffer to add to BE level
        # Commission per lot (divided by 2 for single direction approximate)
        commission_per_lot = getattr(config, "ESTIMATED_COMMISSION_PER_LOT", 7.0)
        spread_pips = getattr(config, "ESTIMATED_SPREAD_COST_PIPS", 1.5)

        # Convert commission to price distance
        # commission_distance = (commission_per_lot * volume) / (pip_value * volume) ≈ commission / pip_value
        pip_value = sym_info.trade_tick_value * (pip_size / sym_info.point)
        if pip_value > 0:
            commission_distance = (commission_per_lot * p.volume) / (pip_value * p.volume) * pip_size
        else:
            commission_distance = 0

        spread_distance = spread_pips * pip_size
        be_buffer = commission_distance + spread_distance

        if p.type == 0:  # BUY
            current_profit_dist = price['bid'] - p.price_open
            rr_achieved = current_profit_dist / risk_distance
            
            if rr_achieved >= ratio_threshold:
                # BE level = entry + buffer (so even if hit, covers costs)
                be_level = p.price_open + be_buffer
                mt5_bridge.modify_position_sl(p.ticket, be_level)
                logger.info(
                    f"[BE] BUY {p.symbol} ticket {p.ticket}: "
                    f"SL moved to {be_level:.5f} "
                    f"(entry={p.price_open:.5f} + buffer={be_buffer:.5f})"
                )
                
        else:  # SELL
            current_profit_dist = p.price_open - price['ask']
            rr_achieved = current_profit_dist / risk_distance
            
            if rr_achieved >= ratio_threshold:
                # BE level = entry - buffer
                be_level = p.price_open - be_buffer
                mt5_bridge.modify_position_sl(p.ticket, be_level)
                logger.info(
                    f"[BE] SELL {p.symbol} ticket {p.ticket}: "
                    f"SL moved to {be_level:.5f} "
                    f"(entry={p.price_open:.5f} - buffer={be_buffer:.5f})"
                )


# ======================================================================
# Entry Point
# ======================================================================

if __name__ == "__main__":
    main()
