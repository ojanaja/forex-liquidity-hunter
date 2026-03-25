"""
Forex Liquidity Hunter - Multi-Symbol Backtester v6
Analyzes all symbols in config.py over the last 6 months.
"""
import logging
from collections import deque
from datetime import datetime, timedelta

import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── Settings ──────────────────────────────────────────────────────────────────
DAYS_TO_BACKTEST  = 180
INITIAL_BALANCE   = 10_000.0
RISK_PER_TRADE    = 50.0  # 0.5%
BROKER_TO_WIB     = 4

def initialize_mt5():
    if not MT5_AVAILABLE: return False
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs): return False
    return True

def get_symbol_data(symbol, days):
    now = datetime.now()
    start = now - timedelta(days=days)
    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, now)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, start - timedelta(days=2), now)
    
    if rates_m5 is None or rates_h1 is None: return None, None
    
    df_m5 = pd.DataFrame(rates_m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
    df_m5.set_index("time", inplace=True)
    
    df_h1 = pd.DataFrame(rates_h1)
    df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")
    df_h1.set_index("time", inplace=True)
    df_h1['ema'] = df_h1['close'].ewm(span=config.HTF_EMA_PERIOD, adjust=False).mean()
    
    return df_m5, df_h1

def run_backtest():
    if not initialize_mt5(): return
    
    all_trades = []
    total_balance = INITIAL_BALANCE
    
    symbols_to_test = config.SYMBOLS
    print(f"🚀 Starting Backtest for {len(symbols_to_test)} symbols over {DAYS_TO_BACKTEST} days...")

    for symbol in symbols_to_test:
        print(f"📊 Testing {symbol}...")
        df_m5, df_h1 = get_symbol_data(symbol, DAYS_TO_BACKTEST)
        if df_m5 is None:
            print(f"⚠️ Skip {symbol}: No data")
            continue

        # Pip size
        pip_size = 0.01 if "XAU" in symbol or "JPY" in symbol else 0.0001
        thresh = config.SWEEP_THRESHOLD_PIPS * pip_size
        fvg_min = config.FVG_MIN_SIZE_PIPS * pip_size
        sl_buff = config.SL_BUFFER_PIPS * pip_size
        
        # SL Limits for Gold vs Forex
        max_sl_pips = 1000.0 if "XAU" in symbol else 50.0

        # State
        range_buf = deque(maxlen=288)
        fvg_buf   = deque(maxlen=24)
        active_s_h, active_s_l = None, None
        last_sweep_type = None
        sweep_expiry = datetime.min
        open_trade = None

        for ts, row in df_m5.iterrows():
            h, l, o, c = float(row["high"]), float(row["low"]), float(row["open"]), float(row["close"])
            candle = {"high": h, "low": l, "open": o, "close": c}
            range_buf.append(candle)
            fvg_buf.append(candle)

            wib = ts + timedelta(hours=BROKER_TO_WIB)
            t_str = wib.strftime("%H:%M")

            # 1. Manage Trade
            if open_trade:
                t = open_trade
                exit_p = None
                if t["type"] == "BUY":
                    if l <= t["sl"]: exit_p = t["sl"]
                    elif h >= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (h - t["entry"]) >= (t["entry"] - t["original_sl"]):
                        t["sl"] = max(t["sl"], t["entry"])
                else:
                    if h >= t["sl"]: exit_p = t["sl"]
                    elif l <= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (t["entry"] - l) >= (t["original_sl"] - t["entry"]):
                        t["sl"] = min(t["sl"], t["entry"])

                if exit_p is not None:
                    p_pips = (exit_p - t["entry"]) / pip_size if t["type"] == "BUY" else (t["entry"] - exit_p) / pip_size
                    r_pips = abs(t["entry"] - t["original_sl"]) / pip_size
                    pnl = (p_pips / r_pips) * RISK_PER_TRADE if r_pips > 0 else 0
                    all_trades.append({"symbol": symbol, "pnl": pnl, "time": ts, "type": t["type"]})
                    open_trade = None
                    last_sweep_type = None
                    sweep_expiry = datetime.min
                continue

            # 2. Window
            in_window = ("14:00" <= t_str <= "18:00") or ("19:00" <= t_str <= "22:59")
            if in_window and active_s_h is None and len(range_buf) >= 200:
                active_s_h = max(can["high"] for can in list(range_buf)[:-1])
                active_s_l = min(can["low"]  for can in list(range_buf)[:-1])
            
            if not in_window:
                active_s_h, active_s_l = None, None
                last_sweep_type = None
                sweep_expiry = datetime.min
                continue

            if active_s_h is None: continue

            # 3. Bias
            h1_ts = ts.replace(minute=0, second=0)
            bias = "NEUTRAL"
            if h1_ts in df_h1.index:
                ema_val = df_h1.loc[h1_ts, 'ema']
                bias = "BULLISH" if c > ema_val else "BEARISH"

            # 4. Sweep
            if h >= active_s_h + thresh:
                last_sweep_type = "HIGH"; sweep_expiry = ts + timedelta(minutes=60)
            elif l <= active_s_l - thresh:
                last_sweep_type = "LOW"; sweep_expiry = ts + timedelta(minutes=60)

            if ts > sweep_expiry: last_sweep_type = None
            if not last_sweep_type: continue

            # 5. FVG
            cl = list(fvg_buf)
            for i in range(len(cl)-1, 1, -1):
                newer, older = cl[i], cl[i-2]
                if last_sweep_type == "HIGH" and bias == "BEARISH":
                    if (older["low"] - newer["high"]) >= fvg_min:
                        te = (older["low"] + newer["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["high"]
                        sl = max(can["high"] for can in cl[-12:]) + sl_buff
                        sl_p = (sl - te) / pip_size
                        if 3.0 <= sl_p <= max_sl_pips:
                            open_trade = {"type": "SELL", "entry": te, "sl": sl, "original_sl": sl, "tp": te - (sl - te) * config.TP_RATIO}
                            break
                elif last_sweep_type == "LOW" and bias == "BULLISH":
                    if (newer["low"] - older["high"]) >= fvg_min:
                        te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                        sl = min(can["low"] for can in cl[-12:]) - sl_buff
                        sl_p = (te - sl) / pip_size
                        if 3.0 <= sl_p <= max_sl_pips:
                            open_trade = {"type": "BUY", "entry": te, "sl": sl, "original_sl": sl, "tp": te + (te - sl) * config.TP_RATIO}
                            break

    mt5.shutdown()
    
    # Final Report
    wins = [t for t in all_trades if t["pnl"] > 0]
    losses = [t for t in all_trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in all_trades)
    
    print("\n" + "="*60)
    print(f"🏆 FINAL MULTI-SYMBOL REPORT (180 DAYS)")
    print("="*60)
    print(f"Symbols tested : {len(symbols_to_test)}")
    print(f"Total Trades   : {len(all_trades)}")
    print(f"Win Rate       : {(len(wins)/len(all_trades)*100 if all_trades else 0):.1f}%")
    print(f"Net Profit     : ${total_pnl:,.2f} ({(total_pnl/INITIAL_BALANCE*100):.1f}%)")
    print(f"Final Balance  : ${INITIAL_BALANCE + total_pnl:,.2f}")
    print("="*60)

if __name__ == "__main__":
    run_backtest()
