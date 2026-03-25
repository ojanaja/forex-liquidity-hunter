"""
Forex Liquidity Hunter - Historical Backtester v3
Simulates the SMC Strategy (Session sweeps, FVGs, Auto Break-Even) on historical MT5 data.
Completely standalone - does NOT touch main.py or execute any live trades.

Run with: python backtest.py
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
DAYS_TO_BACKTEST  = 180          # 6 months
INITIAL_BALANCE   = 10_000.0
RISK_PER_TRADE    = 50.0         # $50 fixed risk per trade  (= 0.5% of $10k)

# Pip size for the tested symbol
PIP_SIZE = 0.01 if "XAU" in SYMBOL or "JPY" in SYMBOL else 0.0001

# Thresholds (in price units, pre-multiplied for speed)
SWEEP_THRESH  = config.SWEEP_THRESHOLD_PIPS * PIP_SIZE
FVG_MIN       = config.FVG_MIN_SIZE_PIPS    * PIP_SIZE
SL_BUFFER     = config.SL_BUFFER_PIPS       * PIP_SIZE
SLIPPAGE      = 2 * PIP_SIZE                # 2-pip tolerance for FVG entry check

# Session hours in WIB (UTC+7).  MT5 broker time ≈ UTC+3, so +4h ≈ WIB.
# We add 4 hours to every candle timestamp when checking sessions.
BROKER_TO_WIB = 4


# ─── MT5 ───────────────────────────────────────────────────────────────────────
def initialize_mt5():
    if not MT5_AVAILABLE:
        logger.error("MetaTrader5 package not installed. Cannot download historical data.")
        return False

    kwargs = {
        "login":    config.MT5_LOGIN,
        "password": config.MT5_PASSWORD,
        "server":   config.MT5_SERVER,
    }
    if config.MT5_PATH:
        kwargs["path"] = config.MT5_PATH

    if not mt5.initialize(**kwargs):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    return True


def download_data():
    now        = datetime.now()
    start_date = now - timedelta(days=DAYS_TO_BACKTEST)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, start_date, now)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        logger.error("❌ No data returned. Check symbol name and MT5 connection.")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    logger.info(f"✅ Downloaded {len(df):,} M5 candles.")
    return df


# ─── Simulation ────────────────────────────────────────────────────────────────
def is_in_session(t_str: str) -> bool:
    """Check if the WIB time string is in London or NY window."""
    return ("15:00" <= t_str <= "17:59") or ("20:00" <= t_str <= "21:59")


def run_backtest():
    if not initialize_mt5():
        return

    logger.info(f"📥 Downloading {DAYS_TO_BACKTEST} days of M5 data for {SYMBOL}...")
    df = download_data()
    if df is None:
        return

    # ── State variables ──────────────────────────────────────
    balance     = INITIAL_BALANCE
    watermark   = INITIAL_BALANCE
    max_dd      = 0.0
    wins        = 0
    losses      = 0
    pnl_list    = []

    open_trade  = None   # dict or None
    current_day = None
    daily_pnl   = 0.0
    daily_halted= False

    # Rolling windows
    range_buf   = deque(maxlen=96)   # last 8 hours for session range (96 × 5min)
    fvg_buf     = deque(maxlen=10)   # last 10 candles for FVG search

    logger.info("⏳ Running simulation loop...")

    for ts, row in df.iterrows():
        high = float(row["high"])
        low  = float(row["low"])
        o    = float(row["open"])
        c    = float(row["close"])

        # ── 0. Push candle into rolling buffers ──
        candle = {"high": high, "low": low, "open": o, "close": c}
        range_buf.append(candle)
        fvg_buf.append(candle)

        # ── 1. Daily reset ───────────────────────
        wib_dt  = ts + timedelta(hours=BROKER_TO_WIB)
        day_str = wib_dt.strftime("%Y-%m-%d")
        t_str   = wib_dt.strftime("%H:%M")

        if day_str != current_day:
            current_day  = day_str
            daily_pnl    = 0.0
            daily_halted = False

        # ── 2. Manage open trade ─────────────────
        if open_trade is not None:
            tp    = open_trade["tp"]
            sl    = open_trade["sl"]
            entry = open_trade["entry"]
            trade_type  = open_trade["type"]
            original_sl = open_trade["original_sl"]

            close_price = None

            if trade_type == "BUY":
                if low <= sl:
                    close_price = sl
                elif high >= tp:
                    close_price = tp
                else:
                    # Auto Break-Even
                    if config.AUTO_BREAK_EVEN and sl < entry:
                        risk = entry - original_sl
                        if risk > 0 and (high - entry) >= risk * config.BE_ACTIVATION_RATIO:
                            open_trade["sl"] = entry
            else:  # SELL
                if high >= sl:
                    close_price = sl
                elif low <= tp:
                    close_price = tp
                else:
                    # Auto Break-Even
                    if config.AUTO_BREAK_EVEN and sl > entry:
                        risk = original_sl - entry
                        if risk > 0 and (entry - low) >= risk * config.BE_ACTIVATION_RATIO:
                            open_trade["sl"] = entry

            if close_price is not None:
                if trade_type == "BUY":
                    profit_pips = (close_price - entry) / PIP_SIZE
                else:
                    profit_pips = (entry - close_price) / PIP_SIZE

                risk_pips = abs(entry - original_sl) / PIP_SIZE
                pnl = (profit_pips / risk_pips) * RISK_PER_TRADE if risk_pips > 0 else 0.0

                balance   += pnl
                daily_pnl += pnl

                if pnl > 0:
                    wins += 1
                else:
                    losses += 1

                pnl_list.append(pnl)
                watermark = max(watermark, balance)
                max_dd    = max(max_dd, watermark - balance)

                if daily_pnl <= -config.DAILY_LOSS_LIMIT:
                    daily_halted = True

                open_trade = None

            # Don't look for new signals while trade is open
            continue

        # ── 3. Skip if daily limit hit ───────────
        if daily_halted:
            continue

        # ── 4. Only generate signals in session windows ──
        if not is_in_session(t_str):
            continue

        # ── 5. Need enough data ──────────────────
        if len(range_buf) < 96 or len(fvg_buf) < 3:
            continue

        # ── 6. Session range (last 95 closed candles, excl. current) ──
        session_high = max(c["high"] for c in list(range_buf)[:-1])
        session_low  = min(c["low"]  for c in list(range_buf)[:-1])

        # ── 7. Sweep detection ───────────────────
        # Use last 6 candles (30 min) to detect the sweep
        last6 = list(fvg_buf)[-6:]
        recent_high = max(c["high"] for c in last6)
        recent_low  = min(c["low"]  for c in last6)

        if recent_high >= session_high + SWEEP_THRESH:
            sweep_type    = "HIGH_SWEPT"
            sweep_extreme = recent_high
        elif recent_low <= session_low - SWEEP_THRESH:
            sweep_type    = "LOW_SWEPT"
            sweep_extreme = recent_low
        else:
            continue  # No sweep this candle

        # ── 8. FVG detection ─────────────────────
        candles_list = list(fvg_buf)  # oldest → newest
        entry_found  = False

        for i in range(len(candles_list) - 1, 1, -1):
            newer  = candles_list[i]
            _mid   = candles_list[i - 1]
            older  = candles_list[i - 2]

            if sweep_type == "HIGH_SWEPT":
                # Bearish FVG: older.low > newer.high  (gap between them)
                gap = older["low"] - newer["high"]
                if gap >= FVG_MIN:
                    fvg_top    = older["low"]
                    fvg_bottom = newer["high"]
                    target_en  = (fvg_top + fvg_bottom) / 2.0 if config.USE_FVG_50_ENTRY else fvg_bottom
                    # Price must have *entered* the FVG zone this candle (SELL: price touched from below)
                    if fvg_bottom - SLIPPAGE <= high <= fvg_top + SLIPPAGE:
                        sl       = sweep_extreme + SL_BUFFER
                        sl_pips  = (sl - target_en) / PIP_SIZE
                        if 3.0 <= sl_pips <= 50.0:
                            open_trade = {
                                "type": "SELL", "entry": target_en,
                                "sl": sl, "original_sl": sl,
                                "tp": target_en - (sl - target_en) * config.TP_RATIO,
                            }
                            entry_found = True
                            break

            elif sweep_type == "LOW_SWEPT":
                # Bullish FVG: newer.low > older.high
                gap = newer["low"] - older["high"]
                if gap >= FVG_MIN:
                    fvg_top    = newer["low"]
                    fvg_bottom = older["high"]
                    target_en  = (fvg_top + fvg_bottom) / 2.0 if config.USE_FVG_50_ENTRY else fvg_top
                    # Price must have *entered* the FVG zone this candle (BUY: price touched from above)
                    if fvg_bottom - SLIPPAGE <= low <= fvg_top + SLIPPAGE:
                        sl       = sweep_extreme - SL_BUFFER
                        sl_pips  = (target_en - sl) / PIP_SIZE
                        if 3.0 <= sl_pips <= 50.0:
                            open_trade = {
                                "type": "BUY", "entry": target_en,
                                "sl": sl, "original_sl": sl,
                                "tp": target_en + (target_en - sl) * config.TP_RATIO,
                            }
                            entry_found = True
                            break

    # ─── Final Report ───────────────────────────────────────────────────────────
    total   = wins + losses
    wr      = (wins / total * 100) if total > 0 else 0
    gross_p = sum(p for p in pnl_list if p > 0)
    gross_l = abs(sum(p for p in pnl_list if p < 0))
    pf      = (gross_p / gross_l) if gross_l > 0 else float("inf")

    print("\n" + "=" * 55)
    print("📊  BACKTEST RESULTS  —  6 MONTHS")
    print("=" * 55)
    print(f"  Symbol           : {SYMBOL}")
    print(f"  Initial Balance  : ${INITIAL_BALANCE:>10,.2f}")
    print(f"  Final Balance    : ${balance:>10,.2f}")
    print(f"  Net Profit       : ${balance - INITIAL_BALANCE:>+10,.2f}")
    print(f"  Total Trades     : {total}")
    print(f"  Win Rate         : {wr:.1f}%  ({wins}W / {losses}L)")
    print(f"  Profit Factor    : {pf:.2f}")
    dd_flag = "❌ PROPFIRM FAILED" if max_dd > 400 else "✅ SAFE"
    print(f"  Max Drawdown     : ${max_dd:>10,.2f}  {dd_flag}")
    print("=" * 55)
    print("\nNote: Backtester uses M5 OHLC only. Actual tick-level")
    print("execution may vary slightly. Run MT5 Strategy Tester for")
    print("tick-by-tick precision.")


if __name__ == "__main__":
    run_backtest()
