"""
Forex Liquidity Hunter - Backtester v12 (ROLLING AUDIT)
Automatically detects available history and tests month-by-month.
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

logging.basicConfig(level=logging.ERROR, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── Settings ──────────────────────────────────────────────────────────────────
BROKER_TO_WIB   = 4
ACCOUNT_BALANCE = config.ACCOUNT_BALANCE
RISK_PER_TRADE  = config.ACCOUNT_BALANCE * config.MAX_RISK_PER_TRADE_PCT / 100.0

def initialize_mt5():
    if not MT5_AVAILABLE: return False
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs): return False
    return True

def get_symbol_data(symbol, days_back=365):
    """Fetches as much historical data as the broker allows (up to days_back)."""
    end = datetime.now()
    start = end - timedelta(days=days_back)
    
    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, start, end)
    
    if rates_m5 is None or rates_h1 is None: return None, None
    
    df_m5 = pd.DataFrame(rates_m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
    df_m5.set_index("time", inplace=True)
    
    df_h1 = pd.DataFrame(rates_h1)
    df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")
    df_h1.set_index("time", inplace=True)
    df_h1['ema'] = df_h1['close'].ewm(span=config.HTF_EMA_PERIOD, adjust=False).mean()
    
    return df_m5, df_h1

def run_monthly_backtest(symbol_data_cache, start_date, end_date):
    monthly_trades = []
    
    for symbol, (df_m5_all, df_h1_all) in symbol_data_cache.items():
        info = mt5.symbol_info(symbol)
        if not info: continue
        
        # Slice data for this month
        try:
            df_m5 = df_m5_all.loc[start_date:end_date]
            df_h1 = df_h1_all.loc[start_date - timedelta(days=1):end_date]
        except KeyError:
            continue
            
        if df_m5.empty: continue
        
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
                    monthly_trades.append({"time": ts, "symbol": symbol, "pnl": pnl})
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
                            open_trade = {"type": "BUY", "entry": te, "sl": sl, "original_sl": sl, "tp": te + (te - sl) * config.TP_RATIO}
                            break
    return monthly_trades

def run_backtest():
    if not initialize_mt5(): return
    
    # Target 3 months as requested (approx 90 days)
    DAYS_BACK = 90
    print(f"🚀 Starting 3-Month Historical Audit (Last {DAYS_BACK} days)...")
    
    symbol_data_cache = {}
    total_min_date = datetime.now()
    
    for symbol in config.SYMBOLS:
        print(f"📥 Loading {symbol}...", end="\r")
        df_m5, df_h1 = get_symbol_data(symbol, days_back=DAYS_BACK)
        if df_m5 is not None and not df_m5.empty:
            print(f"   ✅ {symbol}: Loaded {len(df_m5)} candles.")
            symbol_data_cache[symbol] = (df_m5, df_h1)
            total_min_date = min(total_min_date, df_m5.index.min())
        else:
            print(f"   ⚠️ {symbol}: No history found for last {DAYS_BACK} days.")

    if not symbol_data_cache:
        print("❌ Error: No historical data available on this broker.")
        mt5.shutdown(); return

    print(f"✅ Data found starting from: {total_min_date.strftime('%Y-%m-%d')}")
    
    # Generate list of months to test from total_min_date to now
    test_months = []
    curr = total_min_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()
    while curr <= now:
        month_end = curr.replace(day=calendar.monthrange(curr.year, curr.month)[1], hour=23, minute=59)
        test_months.append((curr, month_end))
        next_m = curr.month + 1; next_y = curr.year
        if next_m > 12: next_m = 1; next_y += 1
        curr = curr.replace(year=next_y, month=next_m)

    monthly_reports = []
    print(f"{'MONTH':<12} | {'TRADES':<6} | {'WR':<6} | {'PROFIT':<10} | {'BEST DAY':<8} | {'CONSISTENCY'}")
    print("-" * 85)

    all_time_profit = 0
    total_trades_count = 0

    for m_start, m_end in test_months:
        trades = run_monthly_backtest(symbol_data_cache, m_start, m_end)
        
        total_pnl = sum(t["pnl"] for t in trades)
        daily_profits = {}
        for t in trades:
            day = t["time"].strftime("%Y-%m-%d")
            daily_profits[day] = daily_profits.get(day, 0) + t["pnl"]
        
        max_win_day = max(daily_profits.values()) if daily_profits else 0
        consistency_pct = (max_win_day / total_pnl * 100) if total_pnl > 0 else 0
        wr = (len([t for t in trades if t["pnl"] > 0]) / len(trades) * 100) if trades else 0
        
        status = "✅" if consistency_pct <= 30.0 and total_pnl >= 0 else ("❌" if total_pnl > 0 else "⚪")
        
        month_name = m_start.strftime("%B %Y")
        print(f"{month_name:<12} | {len(trades):<6} | {wr:>5.1f}% | ${total_pnl:>8.2f} | {consistency_pct:>7.1f}% | {status}")
        
        monthly_reports.append(total_pnl)
        all_time_profit += total_pnl
        total_trades_count += len(trades)

    mt5.shutdown()
    print("-" * 85)
    print(f"🏆 TOTAL PROFIT: ${all_time_profit:,.2f} | TRADES: {total_trades_count} | AVG/MO: {total_trades_count/len(test_months):.1f}")
    print("="*85)

if __name__ == "__main__":
    run_backtest()
