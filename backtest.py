"""
Forex Liquidity Hunter - Multi-Symbol Backtester v7
Supports custom date ranges (e.g. January 2026).
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
# Set your backtest range here
START_DATE      = datetime(2026, 1, 1)
END_DATE        = datetime(2026, 1, 31, 23, 59)
INITIAL_BALANCE = 10_000.0
RISK_PER_TRADE  = 50.0  # 0.5%
BROKER_TO_WIB   = 4

def initialize_mt5():
    if not MT5_AVAILABLE: return False
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs): return False
    return True

def get_symbol_data(symbol, start, end):
    # Fetch extra 2 days for buffers/EMA
    fetch_start = start - timedelta(days=2)
    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, fetch_start, end)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, fetch_start, end)
    
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
    
    symbols_to_test = config.SYMBOLS
    print(f"🚀 Starting Backtest from {START_DATE.date()} to {END_DATE.date()}...")

    for symbol in symbols_to_test:
        print(f"📊 Testing {symbol}...")
        df_m5, df_h1 = get_symbol_data(symbol, START_DATE, END_DATE)
        if df_m5 is None:
            print(f"⚠️ Skip {symbol}: No data")
            continue

        pip_size = 0.01 if "XAU" in symbol or "JPY" in symbol else 0.0001
        thresh = config.SWEEP_THRESHOLD_PIPS * pip_size
        fvg_min = config.FVG_MIN_SIZE_PIPS * pip_size
        sl_buff = config.SL_BUFFER_PIPS * pip_size
        max_sl_p = 1000.0 if "XAU" in symbol else 50.0

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

            if ts < START_DATE: continue # Skip preamble data used only for buffers

            wib = ts + timedelta(hours=BROKER_TO_WIB)
            t_str = wib.strftime("%H:%M")

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
                    open_trade = None; last_sweep_type = None; sweep_expiry = datetime.min
                continue

            in_window = ("14:00" <= t_str <= "18:00") or ("19:00" <= t_str <= "22:59")
            if in_window and active_s_h is None and len(range_buf) >= 200:
                active_s_h = max(can["high"] for can in list(range_buf)[:-1])
                active_s_l = min(can["low"]  for can in list(range_buf)[:-1])
            
            if not in_window:
                active_s_h, active_s_l = None, None; last_sweep_type = None; sweep_expiry = datetime.min
                continue

            if active_s_h is None: continue

            h1_ts = ts.replace(minute=0, second=0)
            bias = "NEUTRAL"
            if h1_ts in df_h1.index:
                ema_val = df_h1.loc[h1_ts, 'ema']
                bias = "BULLISH" if c > ema_val else "BEARISH"

            if h >= active_s_h + thresh:
                last_sweep_type = "HIGH"; sweep_expiry = ts + timedelta(minutes=60)
            elif l <= active_s_l - thresh:
                last_sweep_type = "LOW"; sweep_expiry = ts + timedelta(minutes=60)

            if ts > sweep_expiry: last_sweep_type = None
            if not last_sweep_type: continue

            cl = list(fvg_buf)
            for i in range(len(cl)-1, 1, -1):
                newer, older = cl[i], cl[i-2]
                if last_sweep_type == "HIGH" and bias == "BEARISH":
                    if (older["low"] - newer["high"]) >= fvg_min:
                        te = (older["low"] + newer["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["high"]
                        sl = max(can["high"] for can in cl[-12:]) + sl_buff
                        sl_p = (sl - te) / pip_size
                        if 3.0 <= sl_p <= max_sl_p:
                            open_trade = {"type": "SELL", "entry": te, "sl": sl, "original_sl": sl, "tp": te - (sl - te) * config.TP_RATIO}
                            break
                elif last_sweep_type == "LOW" and bias == "BULLISH":
                    if (newer["low"] - older["high"]) >= fvg_min:
                        te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                        sl = min(can["low"] for can in cl[-12:]) - sl_buff
                        sl_p = (te - sl) / pip_size
                        if 3.0 <= sl_p <= max_sl_p:
                            open_trade = {"type": "BUY", "entry": te, "sl": sl, "original_sl": sl, "tp": te + (te - sl) * config.TP_RATIO}
                            break

    mt5.shutdown()
    
    total_trades = len(all_trades)
    wins = [t for t in all_trades if t["pnl"] > 0]
    total_pnl = sum(t["pnl"] for t in all_trades)
    
    print("\n" + "="*60)
    print(f"🏆 REPORT: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print("="*60)
    print(f"Total Trades   : {total_trades}")
    print(f"Win Rate       : {(len(wins)/total_trades*100 if total_trades else 0):.1f}%")
    print(f"Net Profit     : ${total_pnl:,.2f} ({(total_pnl/INITIAL_BALANCE*100):.1f}%)")
    print(f"Final Balance  : ${INITIAL_BALANCE + total_pnl:,.2f}")
    print("="*60)

if __name__ == "__main__":
    run_backtest()
