"""
Forex Liquidity Hunter - Historical Backtester v5
Final Logic: Frozen Session Ranges, 1-Hour Sweep Memory, and H1 Trend Follow.
"""
import logging
from collections import deque
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

# ─── Backtest Parameters ───────────────────────────────────────────────────────
SYMBOL            = "XAUUSDx" if "XAUUSDx" in config.SYMBOLS else config.SYMBOLS[-1]
DAYS_TO_BACKTEST  = 180
INITIAL_BALANCE   = 10_000.0
RISK_PER_TRADE    = 50.0

# Pip size for the tested symbol
PIP_SIZE = 0.01 if "XAU" in SYMBOL or "JPY" in SYMBOL else 0.0001
THRESHOLD = config.SWEEP_THRESHOLD_PIPS * PIP_SIZE
FVG_MIN   = config.FVG_MIN_SIZE_PIPS    * PIP_SIZE
SL_BUFF   = config.SL_BUFFER_PIPS       * PIP_SIZE

BROKER_TO_WIB = 4

# ─── MT5 ───────────────────────────────────────────────────────────────────────
def initialize_mt5():
    if not MT5_AVAILABLE: return False
    kwargs = {"login": config.MT5_LOGIN, "password": config.MT5_PASSWORD, "server": config.MT5_SERVER}
    if config.MT5_PATH: kwargs["path"] = config.MT5_PATH
    if not mt5.initialize(**kwargs): return False
    return True

def download_data(tf, days):
    now = datetime.now()
    start = now - timedelta(days=days)
    rates = mt5.copy_rates_range(SYMBOL, tf, start, now)
    if rates is None: return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df

# ─── Simulation ────────────────────────────────────────────────────────────────
def run_backtest():
    if not initialize_mt5(): return

    logger.info(f"📥 Downloading data for {SYMBOL}...")
    df_m5  = download_data(mt5.TIMEFRAME_M5, DAYS_TO_BACKTEST)
    df_h1  = download_data(mt5.TIMEFRAME_H1, DAYS_TO_BACKTEST + 2)
    mt5.shutdown()

    if df_m5 is None or df_h1 is None:
        logger.error("❌ Data download failed.")
        return

    df_h1['ema'] = df_h1['close'].ewm(span=config.HTF_EMA_PERIOD, adjust=False).mean()

    # State
    balance = INITIAL_BALANCE
    watermark = INITIAL_BALANCE
    max_dd = 0.0
    wins, losses = 0, 0
    pnl_list = []
    open_trade = None

    # Persistent logic
    range_buf = deque(maxlen=288) # 24 hours
    fvg_buf   = deque(maxlen=24)  # 2 hours
    
    # Frozen session state
    active_s_h, active_s_l = None, None
    last_sweep_type = None
    sweep_expiry = datetime.min
    
    # Diagnostic
    c_session = 0
    c_sweep = 0
    c_fvg = 0

    logger.info("⏳ Running accurate simulation...")

    for ts, row in df_m5.iterrows():
        h, l = float(row["high"]), float(row["low"])
        o, c = float(row["open"]), float(row["close"])
        candle = {"high": h, "low": l, "open": o, "close": c, "time": ts}
        range_buf.append(candle)
        fvg_buf.append(candle)

        wib = ts + timedelta(hours=BROKER_TO_WIB)
        t_str = wib.strftime("%H:%M")

        # 1. Manage Open Trade
        if open_trade:
            t = open_trade
            exit_p = None
            if t["type"] == "BUY":
                if l <= t["sl"]: exit_p = t["sl"]
                elif h >= t["tp"]: exit_p = t["tp"]
                else:
                    if config.AUTO_BREAK_EVEN and t["sl"] < t["entry"]:
                        if (h - t["entry"]) >= (t["entry"] - t["original_sl"]): t["sl"] = t["entry"]
            else: # SELL
                if h >= t["sl"]: exit_p = t["sl"]
                elif l <= t["tp"]: exit_p = t["tp"]
                else:
                    if config.AUTO_BREAK_EVEN and t["sl"] > t["entry"]:
                        if (t["entry"] - l) >= (t["original_sl"] - t["entry"]): t["sl"] = t["entry"]

            if exit_p is not None:
                p_pips = (exit_p - t["entry"]) / PIP_SIZE if t["type"] == "BUY" else (t["entry"] - exit_p) / PIP_SIZE
                r_pips = abs(t["entry"] - t["original_sl"]) / PIP_SIZE
                pnl = (p_pips / r_pips) * RISK_PER_TRADE if r_pips > 0 else 0
                balance += pnl
                if pnl > 0: wins += 1
                else: losses += 1
                pnl_list.append(pnl)
                watermark = max(watermark, balance)
                max_dd = max(max_dd, watermark - balance)
                open_trade = None
                # Reset sweep after trade
                last_sweep_type = None
                sweep_expiry = datetime.min
            else:
                continue

        # 2. Session Window Logic
        in_window = ("14:00" <= t_str <= "18:00") or ("19:00" <= t_str <= "22:59")
        
        # Calculate range ONLY once at start of window
        if in_window and active_s_h is None:
            if len(range_buf) >= 200: # Enough for a good 8-16h range
                active_s_h = max(can["high"] for can in list(range_buf)[:-1])
                active_s_l = min(can["low"]  for can in list(range_buf)[:-1])
        
        # Reset range when outside window
        if not in_window:
            active_s_h, active_s_l = None, None
            last_sweep_type = None
            sweep_expiry = datetime.min
            continue

        c_session += 1
        
        # 3. HTF Bias
        h1_ts = ts.replace(minute=0, second=0)
        bias = "NEUTRAL"
        if h1_ts in df_h1.index:
            ema = df_h1.loc[h1_ts, 'ema']
            bias = "BULLISH" if c > ema else "BEARISH"

        # 4. Sweep Detection (Persistent)
        if h >= active_s_h + THRESHOLD:
            last_sweep_type = "HIGH"
            sweep_expiry = ts + timedelta(minutes=60)
            c_sweep += 1
        elif l <= active_s_l - THRESHOLD:
            last_sweep_type = "LOW"
            sweep_expiry = ts + timedelta(minutes=60)
            c_sweep += 1

        # Check if we have an active sweep memory
        if ts > sweep_expiry:
            last_sweep_type = None
        
        if not last_sweep_type: continue

        # 5. FVG Detection
        cl = list(fvg_buf)
        for i in range(len(cl)-1, 1, -1):
            newer, older = cl[i], cl[i-2]
            if last_sweep_type == "HIGH" and bias == "BEARISH":
                gap = older["low"] - newer["high"]
                if gap >= FVG_MIN:
                    c_fvg += 1
                    te = (older["low"] + newer["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["high"]
                    sl = max(can["high"] for can in cl[-12:]) + SL_BUFF # SL above recent high
                    sl_p = (sl - te) / PIP_SIZE
                    if 3.0 <= sl_p <= 1000.0:
                        open_trade = {"type": "SELL", "entry": te, "sl": sl, "original_sl": sl, "tp": te - (sl - te) * config.TP_RATIO}
                        break
            elif last_sweep_type == "LOW" and bias == "BULLISH":
                gap = newer["low"] - older["high"]
                if gap >= FVG_MIN:
                    c_fvg += 1
                    te = (newer["low"] + older["high"]) / 2 if config.USE_FVG_50_ENTRY else newer["low"]
                    sl = min(can["low"] for can in cl[-12:]) - SL_BUFF
                    sl_p = (te - sl) / PIP_SIZE
                    if 3.0 <= sl_p <= 1000.0:
                        open_trade = {"type": "BUY", "entry": te, "sl": sl, "original_sl": sl, "tp": te + (te - sl) * config.TP_RATIO}
                        break

    # Report
    total = wins + losses
    wr = (wins/total*100) if total > 0 else 0
    pf = (sum(p for p in pnl_list if p > 0) / abs(sum(p for p in pnl_list if p < 0))) if losses > 0 else 0
    print(f"\nDIAGNOSTIC: SessionC={c_session} | SweepC={c_sweep} | FVGC={c_fvg}")
    print(f"RESULTS: Balance: ${balance:,.2f} | Trades: {total} | WinRate: {wr:.1f}% | ProfitFactor: {pf:.2f}")

if __name__ == "__main__":
    run_backtest()
