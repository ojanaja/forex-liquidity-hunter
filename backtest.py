"""
Forex Liquidity Hunter - Backtester v9 (OPTIMIZED)
Testing: TP 2.0, Sweep 2.0, FVG 0.5, and BE at 1.5R.
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
START_DATE      = datetime(2026, 1, 1)
END_DATE        = datetime(2026, 2, 28, 23, 59)
INITIAL_BALANCE = 10_000.0
RISK_PER_TRADE  = 50.0

# --- OPTIMIZED PARAMETERS ---
OPT_TP_RATIO       = 2.0  # Reduced from 3.0
OPT_SWEEP_PIPS      = 2.0  # Reduced from 3.0 (increase frequency)
OPT_FVG_PIPS        = 0.5  # Reduced from 1.0 (increase frequency)
OPT_BE_TRIGGER      = 1.5  # Increase from 1.0R (more breathing room)
# ----------------------------

BROKER_TO_WIB   = 4

def initialize_mt5():
    if not MT5_AVAILABLE: return False
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs): return False
    return True

def get_symbol_data(symbol, start, end):
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

def calculate_lots(risk, sl_dist, sym_info):
    if sl_dist <= 0: return 0.01
    pip_val = sym_info.trade_tick_value / sym_info.point * (sym_info.point * 10 if sym_info.digits in (3,5) else sym_info.point)
    raw_lots = risk / ( (sl_dist / (sym_info.point * 10 if sym_info.digits in (3,5) else sym_info.point)) * pip_val )
    return max(sym_info.volume_min, min(round(raw_lots, 2), sym_info.volume_max))

def run_backtest():
    if not initialize_mt5(): return
    
    all_trades = []
    symbols_to_test = config.SYMBOLS
    print(f"🚀 Starting Optimized Backtest (Jan-Feb 2026)...")

    for symbol in symbols_to_test:
        print(f"📊 Testing {symbol}...")
        df_m5, df_h1 = get_symbol_data(symbol, START_DATE, END_DATE)
        if df_m5 is None: continue
        
        info = mt5.symbol_info(symbol)
        if not info: continue
        
        pip_size = info.point * 10 if info.digits in (3, 5) else info.point
        thresh = OPT_SWEEP_PIPS * pip_size
        fvg_min = OPT_FVG_PIPS * pip_size
        sl_buff = config.SL_BUFFER_PIPS * pip_size
        max_sl_p = 1000.0 if "XAU" in symbol else 50.0

        range_buf = deque(maxlen=288)
        fvg_buf   = deque(maxlen=24)
        active_s_h, active_s_l = None, None
        last_sweep_type = None; sweep_expiry = datetime.min
        open_trade = None

        for ts, row in df_m5.iterrows():
            h, l, o, c = float(row["high"]), float(row["low"]), float(row["open"]), float(row["close"])
            candle = {"high": h, "low": l, "open": o, "close": c}
            range_buf.append(candle)
            fvg_buf.append(candle)
            if ts < START_DATE: continue

            wib = ts + timedelta(hours=BROKER_TO_WIB)
            t_str = wib.strftime("%H:%M")

            if open_trade:
                t = open_trade
                exit_p = None
                r_dist = t["entry"] - t["original_sl"] if t["type"] == "BUY" else t["original_sl"] - t["entry"]
                
                if t["type"] == "BUY":
                    if l <= t["sl"]: exit_p = t["sl"]
                    elif h >= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (h - t["entry"]) >= (r_dist * OPT_BE_TRIGGER):
                        t["sl"] = max(t["sl"], t["entry"])
                else:
                    if h >= t["sl"]: exit_p = t["sl"]
                    elif l <= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (t["entry"] - l) >= (r_dist * OPT_BE_TRIGGER):
                        t["sl"] = min(t["sl"], t["entry"])

                if exit_p is not None:
                    p_pips = (exit_p - t["entry"]) / pip_size if t["type"] == "BUY" else (t["entry"] - exit_p) / pip_size
                    pnl = (p_pips / (r_dist / pip_size)) * RISK_PER_TRADE
                    all_trades.append({
                        "time": ts.strftime("%m-%d %H:%M"), "symbol": symbol, "type": t["type"],
                        "entry": t["entry"], "sl": t["original_sl"], "tp": t["tp"], "lots": t["lots"], "pnl": pnl
                    })
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
                bias = "BULLISH" if c > df_h1.loc[h1_ts, 'ema'] else "BEARISH"

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
                            lots = calculate_lots(RISK_PER_TRADE, sl-te, info)
                            open_trade = {"type": "SELL", "entry": te, "sl": sl, "original_sl": sl, "tp": te - (sl - te) * OPT_TP_RATIO, "lots": lots}
                            break
                elif last_sweep_type == "LOW" and bias == "BULLISH":
                    if (newer["low"] - older["high"]) >= fvg_min:
                        te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                        sl = min(can["low"] for can in cl[-12:]) - sl_buff
                        sl_p = (te - sl) / pip_size
                        if 3.0 <= sl_p <= max_sl_p:
                            lots = calculate_lots(RISK_PER_TRADE, te-sl, info)
                            open_trade = {"type": "BUY", "entry": te, "sl": sl, "original_sl": sl, "tp": te + (te - sl) * OPT_TP_RATIO, "lots": lots}
                            break
    mt5.shutdown()

    # Final History
    print("\n" + "="*80)
    print(f"{'TIME':<12} | {'SYM':<8} | {'TYPE':<4} | {'LOTS':<4} | {'ENTRY':<8} | {'PNL':<6}")
    print("-" * 80)
    for t in all_trades:
        res = f"${t['pnl']:+6.2f}"
        print(f"{t['time']:<12} | {t['symbol']:<8} | {t['type']:<4} | {t['lots']:<4.2f} | {t['entry']:<8.4f} | {res}")
    print("-" * 80)
    
    total_pnl = sum(t["pnl"] for t in all_trades)
    wr = (len([t for t in all_trades if t["pnl"] > 0]) / len(all_trades) * 100) if all_trades else 0
    print(f"🏆 OPTIMIZED PROFIT: ${total_pnl:,.2f} | TRADES: {len(all_trades)} | WR: {wr:.1f}%")
    print("="*80)

if __name__ == "__main__":
    run_backtest()
