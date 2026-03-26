"""
Forex Liquidity Hunter - Backtester v11 (LONG-TERM AUDIT)
Month-by-month 12-month simulation for 2025.
"""
import logging
from collections import deque
from datetime import datetime, timedelta
import calendar

import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config

logging.basicConfig(level=logging.ERROR, format="%(message)s") # Reduce logs for long runs
logger = logging.getLogger(__name__)

# ─── Settings ──────────────────────────────────────────────────────────────────
YEAR_TO_TEST    = 2025
BROKER_TO_WIB   = 4
ACCOUNT_BALANCE = config.ACCOUNT_BALANCE
RISK_PER_TRADE  = config.ACCOUNT_BALANCE * config.MAX_RISK_PER_TRADE_PCT / 100.0

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

def run_monthly_backtest(symbol_data_cache, start_date, end_date):
    """Runs the simulation for all symbols in a specific date range."""
    monthly_trades = []
    
    for symbol, (df_m5_all, df_h1_all) in symbol_data_cache.items():
        info = mt5.symbol_info(symbol)
        if not info: continue
        
        # Filter data for this specific month
        df_m5 = df_m5_all.loc[start_date - timedelta(days=1):end_date]
        df_h1 = df_h1_all.loc[start_date - timedelta(days=1):end_date]
        
        pip_size = info.point * 10 if info.digits in (3, 5) else info.point
        thresh = config.SWEEP_THRESHOLD_PIPS * pip_size
        fvg_min = config.FVG_MIN_SIZE_PIPS * pip_size
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
            if ts < start_date: continue

            wib = ts + timedelta(hours=BROKER_TO_WIB)
            t_str = wib.strftime("%H:%M")

            if open_trade:
                t = open_trade
                exit_p = None
                r_dist = t["entry"] - t["original_sl"] if t["type"] == "BUY" else t["original_sl"] - t["entry"]
                
                if t["type"] == "BUY":
                    if l <= t["sl"]: exit_p = t["sl"]
                    elif h >= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (h - t["entry"]) >= (r_dist * config.BE_ACTIVATION_RATIO):
                        t["sl"] = max(t["sl"], t["entry"])
                else:
                    if h >= t["sl"]: exit_p = t["sl"]
                    elif l <= t["tp"]: exit_p = t["tp"]
                    elif config.AUTO_BREAK_EVEN and (t["entry"] - l) >= (r_dist * config.BE_ACTIVATION_RATIO):
                        t["sl"] = min(t["sl"], t["entry"])

                if exit_p is not None:
                    p_pips = (exit_p - t["entry"]) / pip_size if t["type"] == "BUY" else (t["entry"] - exit_p) / pip_size
                    pnl = (p_pips / (r_dist / pip_size)) * RISK_PER_TRADE
                    monthly_trades.append({
                        "time": ts, "symbol": symbol, "pnl": pnl
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
                            open_trade = {"type": "SELL", "entry": te, "sl": sl, "original_sl": sl, "tp": te - (sl - te) * config.TP_RATIO}
                            break
                elif last_sweep_type == "LOW" and bias == "BULLISH":
                    if (newer["low"] - older["high"]) >= fvg_min:
                        te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                        sl = min(can["low"] for can in cl[-12:]) - sl_buff
                        sl_p = (te - sl) / pip_size
                        if 3.0 <= sl_p <= max_sl_p:
                            open_trade = {"type": "BUY", "entry": te, "sl": l, "original_sl": sl, "tp": te + (te - sl) * config.TP_RATIO}
                            break
    return monthly_trades

def run_backtest():
    if not initialize_mt5(): return
    
    print(f"🚀 Starting 1-Year Audit ({YEAR_TO_TEST})...")
    
    # Pre-fetch all data to avoid repeating work
    symbol_data_cache = {}
    for symbol in config.SYMBOLS:
        print(f"📥 Loading {symbol} data...")
        df_m5, df_h1 = get_symbol_data(symbol, datetime(YEAR_TO_TEST, 1, 1), datetime(YEAR_TO_TEST, 12, 31, 23, 59))
        if df_m5 is not None:
            symbol_data_cache[symbol] = (df_m5, df_h1)

    monthly_reports = []

    for month in range(1, 13):
        start_date = datetime(YEAR_TO_TEST, month, 1)
        last_day = calendar.monthrange(YEAR_TO_TEST, month)[1]
        end_date = datetime(YEAR_TO_TEST, month, last_day, 23, 59)
        
        print(f"📅 Simulating {calendar.month_name[month]}...", end="\r")
        trades = run_monthly_backtest(symbol_data_cache, start_date, end_date)
        
        total_pnl = sum(t["pnl"] for t in trades)
        daily_profits = {}
        for t in trades:
            day = t["time"].strftime("%Y-%m-%d")
            daily_profits[day] = daily_profits.get(day, 0) + t["pnl"]
        
        best_day = max(daily_profits.values()) if daily_profits else 0
        consistency_pct = (best_day / total_pnl * 100) if total_pnl > 0 else 0
        wr = (len([t for t in trades if t["pnl"] > 0]) / len(trades) * 100) if trades else 0

        monthly_reports.append({
            "Month": calendar.month_name[month],
            "Trades": len(trades),
            "WinRate": f"{wr:.1f}%",
            "Profit": f"${total_pnl:,.2f}",
            "BestDay": f"{consistency_pct:.1f}%",
            "Pass": "✅" if consistency_pct <= 30.0 and total_pnl >= 0 else "❌"
        })

    mt5.shutdown()

    # Final Report
    print("\n\n" + "="*85)
    print(f"{'MONTH':<12} | {'TRADES':<6} | {'WR':<6} | {'PROFIT':<10} | {'BEST DAY':<8} | {'CONSISTENCY'}")
    print("-" * 85)
    for r in monthly_reports:
        print(f"{r['Month']:<12} | {r['Trades']:<6} | {r['WinRate']:<6} | {r['Profit']:<10} | {r['BestDay']:<8} | {r['Pass']}")
    print("-" * 85)
    
    overall_profit = sum(float(r["Profit"].replace("$", "").replace(",", "")) for r in monthly_reports)
    avg_trades = sum(r["Trades"] for r in monthly_reports) / 12
    print(f"🏆 1-YEAR TOTAL PROFIT: ${overall_profit:,.2f} | AVG TRADES/MO: {avg_trades:.1f}")
    print("="*85)

if __name__ == "__main__":
    run_backtest()
