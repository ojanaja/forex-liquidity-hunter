"""
Forex Liquidity Hunter - Main Execution Loop (V18 Intelligence)
Parallel scan, multi-strategy, and smart risk management.
"""
import time
import logging
import os
from datetime import datetime
import pytz

import config
import mt5_bridge
from risk_manager import RiskManager
import strategy

# Setup logging
if not os.path.exists(config.LOG_DIR):
    os.makedirs(config.LOG_DIR)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"{config.LOG_DIR}/bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("main")

_symbol_cooldowns: dict[str, float] = {}


def manage_existing_trades():
    """Monitor open positions and move SL to Break-Even + Buffer."""
    if not config.AUTO_BREAK_EVEN:
        return

    positions = mt5_bridge.get_open_positions()
    for p in positions:
        sym_info = mt5_bridge.get_symbol_info(p.symbol)
        if sym_info is None: continue
        pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
        
        # Calculate current profit in pips
        if p.type == 0: # BUY
            profit_pips = (p.price_current - p.price_open) / pip_size
            sl_dist_pips = (p.price_open - p.sl) / pip_size if p.sl > 0 else 20.0
            
            if profit_pips >= config.BE_ACTIVATION_RATIO * sl_dist_pips:
                new_sl = p.price_open + (config.BE_BUFFER_PIPS * pip_size)
                if p.sl < new_sl - (0.1 * pip_size):
                    mt5_bridge.modify_position_sl(p.ticket, new_sl)
                    
        elif p.type == 1: # SELL
            profit_pips = (p.price_open - p.price_current) / pip_size
            sl_dist_pips = (p.sl - p.price_open) / pip_size if p.sl > 0 else 20.0
            
            if profit_pips >= config.BE_ACTIVATION_RATIO * sl_dist_pips:
                new_sl = p.price_open - (config.BE_BUFFER_PIPS * pip_size)
                if p.sl == 0 or p.sl > new_sl + (0.1 * pip_size):
                    mt5_bridge.modify_position_sl(p.ticket, new_sl)


def main():
    mode_str = "DRY RUN" if config.DRY_RUN else "LIVE"
    logger.info(
        f"\n"
        f"---------------------------------------------------\n"
        f"  FOREX LIQUIDITY HUNTER v1.8 (Intelligence)\n"
        f"  Regime Awareness + Smart Break-Even Plus\n"
        f"  Account:  WeMasterTrade 10k\n"
        f"  Mode:     {mode_str}\n"
        f"---------------------------------------------------\n"
    )

    risk = RiskManager()
    if not mt5_bridge.connect():
        logger.critical("Failed to connect to MT5. Exiting.")
        return

    last_summary_time = 0
    signals_today = 0
    tz = pytz.timezone(config.TIMEZONE)

    try:
        while True:
            now_dt = datetime.now(tz)
            
            # --- 1. Manage Existing Trades (BE Logic) ---
            manage_existing_trades()

            # --- 2. Check risk limits ---
            if not risk.can_trade():
                logger.warning("Daily risk limit reached or bot stopped. Standing down.")
                time.sleep(60)
                continue

            if risk.daily_trade_count >= getattr(config, "DAILY_TRADE_LIMIT", 3):
                if time.time() - last_summary_time >= config.SUMMARY_LOG_INTERVAL_SECONDS:
                    logger.info(f"Daily trade limit ({config.DAILY_TRADE_LIMIT}) reached. No more trades today.")
                time.sleep(60)
                continue

            # --- 3. Identify active session ---
            session = None
            for s_name, s_h, s_m, e_h, e_m in config.SESSIONS:
                start = now_dt.replace(hour=s_h, minute=s_m, second=0)
                end = now_dt.replace(hour=e_h, minute=e_m, second=0)
                if start <= now_dt <= end:
                    session = s_name
                    break

            # --- 4. Scan symbols ---
            open_count = len(mt5_bridge.get_open_positions())
            
            for symbol in config.SYMBOLS:
                if open_count >= config.MAX_OPEN_TRADES:
                    break

                # Cooldown check
                if symbol in _symbol_cooldowns:
                    if time.time() - _symbol_cooldowns[symbol] < config.TRADE_COOLDOWN_MINUTES * 60:
                        continue

                signal = strategy.generate_signal(symbol)
                if signal:
                    # Risk Check - fixed argument order
                    lot_size = risk.calculate_lot_size(signal.sl_pips, symbol)
                    if not lot_size:
                        continue

                    # Execute
                    ticket = mt5_bridge.place_order(symbol, signal.direction, lot_size, signal.stop_loss, signal.take_profit, f"LH18_{signal.direction}")
                    if ticket:
                        signals_today += 1
                        _symbol_cooldowns[symbol] = time.time()
                        open_count += 1

            # --- Sync and Summary ---
            risk.sync_closed_trades(mt5_bridge.get_daily_deals())
            if time.time() - last_summary_time >= config.SUMMARY_LOG_INTERVAL_SECONDS:
                risk.log_daily_summary()
                last_summary_time = time.time()

            time.sleep(config.SCAN_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
    finally:
        mt5_bridge.disconnect()
        logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    main()
