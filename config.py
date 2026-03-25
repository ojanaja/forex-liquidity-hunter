"""
Forex Liquidity Hunter - Configuration
All tunable parameters for the trading bot.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# MT5 CREDENTIALS (loaded from .env)
# =============================================================================
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", None)  # Optional: path to terminal64.exe

# =============================================================================
# ACCOUNT RULES (WeMasterTrade 10k Prop Firm)
# =============================================================================
ACCOUNT_BALANCE = 10_000.0

# Daily Loss < $200 → we use $150 as safe buffer
DAILY_LOSS_LIMIT = 150.0

# Total Loss < $400 → we use $350 as safe buffer
TOTAL_LOSS_LIMIT = 350.0

# Daily profit cap to maintain Profit Consistency ≤ 30%
# $600 target / 20 days = $30 avg. Cap at $120 so no single day > 20% of total
DAILY_PROFIT_CAP = 120.0

# Max risk per trade as percentage of balance
MAX_RISK_PER_TRADE_PCT = 0.5  # 0.5% = $50 per trade on 10k

# Profit target for withdrawal
PROFIT_TARGET = 600.0

# =============================================================================
# SESSION WINDOWS (UTC+7 / WIB)
# =============================================================================
# Format: (name, start_hour, start_minute, end_hour, end_minute)
SESSIONS = [
    ("Tokyo",   7,  0,   9,  0),   # 07:00 - 09:00 WIB
    ("London", 15,  0,  17,  0),   # 15:00 - 17:00 WIB
    ("NewYork", 20,  0,  22,  0),  # 20:00 - 22:00 WIB
]

TIMEZONE = "Asia/Jakarta"  # UTC+7

# =============================================================================
# TRADING PAIRS
# =============================================================================
SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "EURJPY",
    "GBPJPY",
]

# =============================================================================
# STRATEGY PARAMETERS (Session Liquidity Sweep)
# =============================================================================
# How many pips price must push beyond the session high/low to be a "sweep"
SWEEP_THRESHOLD_PIPS = 3.0

# Min wick-to-body ratio for a rejection candle (0.6 = wick is 60%+ of range)
REJECTION_WICK_RATIO = 0.6

# Risk/Reward ratio (1:1.5 default)
TP_RATIO = 1.5

# Extra buffer pips added to SL beyond the rejection wick
SL_BUFFER_PIPS = 2.0

# Timeframe for scanning rejection candles
SCAN_TIMEFRAME_MINUTES = 5  # M5

# Timeframe for the session range calculation
RANGE_TIMEFRAME_MINUTES = 15  # M15

# =============================================================================
# SAFETY / EXECUTION
# =============================================================================

# DRY_RUN mode: True = log trades only, False = execute real trades
DRY_RUN = True

# Max number of simultaneous open trades
MAX_OPEN_TRADES = 2

# Max allowed spread in pips (skip trade if spread is wider)
MAX_SPREAD_PIPS = 2.0

# How often to check for signals (seconds)
SCAN_INTERVAL_SECONDS = 15

# How often to log the daily summary (seconds)
SUMMARY_LOG_INTERVAL_SECONDS = 300  # 5 minutes

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
