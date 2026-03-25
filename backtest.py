"""
Forex Liquidity Hunter - Historical Backtester
Simulates the SMC Strategy (Session sweeps, FVGs, Auto Break-Even) on historical MT5 data.
"""
import os
import time
import logging
from datetime import datetime, timedelta

import pandas as pd
import pytz

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# --- Backtest Parameters ---
SYMBOL = "XAUUSDx" if "XAUUSDx" in config.SYMBOLS else config.SYMBOLS[-1]
DAYS_TO_BACKTEST = 180  # 6 months
INITIAL_BALANCE = 10000.0


def initialize_mt5():
    if not MT5_AVAILABLE:
        logger.error("MT5 package not installed. Cannot download historical data.")
        return False
    
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
        
    if not mt5.initialize(**kwargs):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    return True


def run_backtest():
    if not initialize_mt5():
        return

    logger.info(f"📥 Downloading {DAYS_TO_BACKTEST} days of M5 data for {SYMBOL}...")
    
    # Fetch data
    now = datetime.now()
    start_date = now - timedelta(days=DAYS_TO_BACKTEST)
    
    # mt5.copy_rates_range takes datetime in UTC/Broker time. We'll use naive datetimes for simplicity
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, start_date, now)
    
    if rates is None or len(rates) == 0:
        logger.error("❌ Failed to download historical data. Check symbol name or MT5 connection.")
        mt5.shutdown()
        return
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    
    logger.info(f"✅ Downloaded {len(df)} M5 candles.")
    mt5.shutdown()
    
    # --- Simulation Variables ---
    balance = INITIAL_BALANCE
    watermark = INITIAL_BALANCE
    max_drawdown = 0.0
    
    trades = []
    wins = 0
    losses = 0
    
    open_trade = None  # { "type": "BUY"/"SELL", "entry": float, "sl": float, "tp": float, "lots": float }
    
    # Prop Firm Daily Loss tracking
    current_day = None
    daily_pnl = 0.0
    is_stopped_today = False
    
    # SMC logic variables
    session_high = None
    session_low = None
    sweep_direction = None  # "UP" or "DOWN"
    sweep_extreme = None
    recent_candles = []
    range_candles = []
    
    wib_tz = pytz.timezone("Asia/Jakarta")
    
    logger.info("⏳ Running simulation loop... This might take a few seconds.")
    
    sym_info_point = 0.001 if "JPY" in SYMBOL else 0.01  # Gold is 0.01 or 0.001 typically. Let's assume Gold point is 0.01
    pip_size = sym_info_point * 10
    
    # We will use iterrows for simplicity, though vectorization is faster
    for dt, row in df.iterrows():
        # Convert MT5 broker time to WIB to match session windows
        # Note: MT5 time is usually UTC+2/3. We'll approximate by treating broker time as UTC+2, so +5 hours for WIB
        # For a completely accurate test, you'd use exactly the broker's timezone.
        local_time = dt + timedelta(hours=5)
        day_str = local_time.strftime("%Y-%m-%d")
        
        # Keep track of recent candles for FVG check (last 10)
        recent_candles.append({"high": row["high"], "low": row["low"], "open": row["open"], "close": row["close"]})
        if len(recent_candles) > 10:
            recent_candles.pop(0)
            
        # Keep track of last 8 hours (96 M5 candles) for Session Range
        range_candles.append({"high": row["high"], "low": row["low"]})
        if len(range_candles) > 96:
            range_candles.pop(0)
            
        # Reset daily limits
        if day_str != current_day:
            current_day = day_str
            daily_pnl = 0.0
            is_stopped_today = False
            session_high = None
            session_low = None
            sweep_direction = None
            
        if is_stopped_today:
            # If we hit daily loss, skip rest of the day
            # But we must still handle open trades! Actually, Prop firms close all your trades.
            if open_trade is not None:
                continue
                
        # --- Trade Management (Stop Loss, Take Profit, Break Even) ---
        if open_trade is not None:
            close_price = None
            pnl = 0.0
            
            if open_trade["type"] == "BUY":
                if row["low"] <= open_trade["sl"]:
                    close_price = open_trade["sl"]
                elif row["high"] >= open_trade["tp"]:
                    close_price = open_trade["tp"]
                else:
                    # Auto Break Even check
                    if config.AUTO_BREAK_EVEN and open_trade["sl"] < open_trade["entry"]:
                        risk_dist = open_trade["entry"] - open_trade["original_sl"]
                        if row["high"] - open_trade["entry"] >= risk_dist * config.BE_ACTIVATION_RATIO:
                            open_trade["sl"] = open_trade["entry"]  # Move to BE
            else:
                if row["high"] >= open_trade["sl"]:
                    close_price = open_trade["sl"]
                elif row["low"] <= open_trade["tp"]:
                    close_price = open_trade["tp"]
                else:
                    # Auto Break Even check
                    if config.AUTO_BREAK_EVEN and open_trade["sl"] > open_trade["entry"]:
                        risk_dist = open_trade["original_sl"] - open_trade["entry"]
                        if open_trade["entry"] - row["low"] >= risk_dist * config.BE_ACTIVATION_RATIO:
                            open_trade["sl"] = open_trade["entry"]  # Move to BE
                            
            if close_price is not None:
                # Trade Closed
                if open_trade["type"] == "BUY":
                    # Simplify pip value math. Let's assume 1 pip = $10 per standard lot
                    # Gold: 100 pips = $1000 per lot (or 10 pips = $100)
                    profit_pips = (close_price - open_trade["entry"]) / pip_size
                else:
                    profit_pips = (open_trade["entry"] - close_price) / pip_size
                    
                # Standard risk model: $50 risk for original SL distance
                # Actual PnL = (profit_pips / risk_pips) * $50
                risk_pips = abs(open_trade["entry"] - open_trade["original_sl"]) / pip_size
                pnl = (profit_pips / risk_pips) * 50.0
                
                balance += pnl
                daily_pnl += pnl
                
                if pnl > 0: wins += 1
                else: losses += 1
                
                trades.append(pnl)
                
                if balance > watermark:
                    watermark = balance
                dd = watermark - balance
                if dd > max_drawdown:
                    max_drawdown = dd
                    
                if daily_pnl <= -config.DAILY_LOSS_LIMIT:
                    is_stopped_today = True
                    
                open_trade = None
                
            continue  # Only 1 trade at a time allowed
            
            
        # --- Signal Generation ---
        t_str = local_time.strftime("%H:%M")
        
        # 2. Sweep detection during active windows
        in_window = ("15:00" <= t_str <= "17:00") or ("20:00" <= t_str <= "22:00")
        if in_window and len(range_candles) >= 96:
            
            # The range is the last 95 candles (excluding the current one)
            session_high = max(c["high"] for c in range_candles[:-1])
            session_low = min(c["low"] for c in range_candles[:-1])
            
            # Detect sweep against the current row
            if sweep_direction is None:
                if row["high"] > session_high + (config.SWEEP_THRESHOLD_PIPS * pip_size):
                    sweep_direction = "UP"
                    sweep_extreme = row["high"]
                elif row["low"] < session_low - (config.SWEEP_THRESHOLD_PIPS * pip_size):
                    sweep_direction = "DOWN"
                    sweep_extreme = row["low"]
                    
            # 3. FVG Formation after sweep
            if sweep_direction is not None and len(recent_candles) >= 3:
                # Loop backward from 2nd to last candle to find an FVG
                for i in range(len(recent_candles) - 3, -1, -1):
                    c0 = recent_candles[i+2]
                    c1 = recent_candles[i+1]
                    c2 = recent_candles[i]
                    
                    if sweep_direction == "UP":
                        gap = c0["low"] - c2["high"]
                        if gap >= config.FVG_MIN_SIZE_PIPS * pip_size:
                            fvg_top = c0["low"]
                            fvg_bottom = c2["high"]
                            target_en = fvg_bottom
                            if getattr(config, "USE_FVG_50_ENTRY", False):
                                target_en = (fvg_top + fvg_bottom) / 2.0
                                
                            if target_en <= row["high"] <= fvg_top + (2 * pip_size):
                                sl = sweep_extreme + (config.SL_BUFFER_PIPS * pip_size)
                                sl_pips = (sl - target_en) / pip_size
                                if 3.0 <= sl_pips <= 30.0:
                                    open_trade = {
                                        "type": "SELL", "entry": target_en, "sl": sl, "original_sl": sl,
                                        "tp": target_en - ((sl - target_en) * config.TP_RATIO)
                                    }
                                    sweep_direction = None
                                    sweep_extreme = None
                                    break # FVG found and entered
                                    
                    elif sweep_direction == "DOWN":
                        gap = c2["low"] - c0["high"]
                        if gap >= config.FVG_MIN_SIZE_PIPS * pip_size:
                            fvg_top = c2["low"]
                            fvg_bottom = c0["high"]
                            target_en = fvg_top
                            if getattr(config, "USE_FVG_50_ENTRY", False):
                                target_en = (fvg_top + fvg_bottom) / 2.0
                                
                            if fvg_bottom - (2 * pip_size) <= row["low"] <= target_en:
                                sl = sweep_extreme - (config.SL_BUFFER_PIPS * pip_size)
                                sl_pips = (target_en - sl) / pip_size
                                if 3.0 <= sl_pips <= 30.0:
                                    open_trade = {
                                        "type": "BUY", "entry": target_en, "sl": sl, "original_sl": sl,
                                        "tp": target_en + ((target_en - sl) * config.TP_RATIO)
                                    }
                                    sweep_direction = None
                                    sweep_extreme = None
                                    break
                
    # --- Final Report ---
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0
    
    print("\n" + "="*50)
    print("📊 BACKTEST RESULTS (6 MONTHS)")
    print("="*50)
    print(f"Symbol:          {SYMBOL}")
    print(f"Initial Balance: ${INITIAL_BALANCE:,.2f}")
    print(f"Final Balance:   ${balance:,.2f}")
    print(f"Net Profit:      ${(balance - INITIAL_BALANCE):+,.2f}")
    print(f"Total Trades:    {total_trades}")
    print(f"Win Rate:        {win_rate:.1f}% ({wins} W / {losses} L)")
    print(f"Profit Factor:   {profit_factor:.2f}")
    print(f"Max Drawdown:    ${max_drawdown:,.2f} " + ("❌ (PROPFIRM FAILED)" if max_drawdown > 400 else "✅ (SAFE)"))
    print("="*50)
    print("Note: This simple vector engine uses heuristics for FVGs due to structural time-series complexity.")
    print("For full tick-by-tick exact replication, Strategy Tester in MT5 is advised.")


if __name__ == "__main__":
    run_backtest()
