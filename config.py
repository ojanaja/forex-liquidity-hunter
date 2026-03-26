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
# ─── Risk Management ──────────────────────────────────────────────────────────
ACCOUNT_BALANCE         = 10000.0  # Default evaluation balance
MAX_RISK_PER_TRADE_PCT  = 0.5      # 0.5% risk per trade ($50)
DAILY_LOSS_LIMIT        = 150.0    # Stop trading if down $150 in a day
TOTAL_LOSS_LIMIT        = 350.0    # Stop trading if down $350 total
PROFIT_TARGET           = 1000.0   # Target for WD
DAILY_PROFIT_CAP        = 300.0    # 30% consistency rule enforcement ($1000 * 0.3)
MAX_OPEN_TRADES         = 1        # One at a time for maximum focus

# ─── Strategy Parameters (Optimized) ──────────────────────────────────────────
SCAN_TIMEFRAME_MINUTES  = 5        # Entry checking interval
RANGE_TIMEFRAME_MINUTES = 15       # Session range identification
SWEEP_THRESHOLD_PIPS    = 1.5      # 1.5 pips sweep for higher sensitivity
FVG_MIN_SIZE_PIPS       = 0.5      # 0.5 pips minimum gap
SL_BUFFER_PIPS          = 2.0      # 2.0 pips extra SL room
TP_RATIO                = 2.0      # Target 2:1 Reward to Risk
AUTO_BREAK_EVEN         = True     # Protected trades
BE_ACTIVATION_RATIO     = 1.5      # 1.5R before moving to BE
USE_FVG_50_ENTRY        = True     # 50% Consequent Encroachment entry strategy

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
    "EURUSDx",
    "GBPUSDx",
    "USDJPYx",
    "EURJPYx",
    "GBPJPYx",
    "XAUUSDx",   # Gold is highly volatile, ensure MAX_SPREAD_PIPS accommodates it
]

# =============================================================================
# STRATEGY PARAMETERS (Session Liquidity Sweep)
# =============================================================================
# How many pips price must push beyond the session high/low to be a "sweep"
SWEEP_THRESHOLD_PIPS = 3.0

# Min wick-to-body ratio for a rejection candle (0.6 = wick is 60%+ of range)
REJECTION_WICK_RATIO = 0.6

# Risk/Reward ratio (we aim for 3.0 RR with tighter FVG 50% entries)
TP_RATIO = 3.0

# Extra buffer pips added to SL beyond the rejection wick
SL_BUFFER_PIPS = 2.0

# Timeframe for scanning rejection candles
SCAN_TIMEFRAME_MINUTES = 5  # M5

# Timeframe for the session range calculation
RANGE_TIMEFRAME_MINUTES = 15  # M15

# =============================================================================
# SMART MONEY CONCEPTS (SMC) ENTRY PARAMETERS
# =============================================================================

# Use Fair Value Gap (FVG) confirmation for entries
USE_FVG_FILTER = True

# Minimum size in pips for a valid FVG
FVG_MIN_SIZE_PIPS = 1.0

# Consequent Encroachment: enter only at 50% midpoint of the FVG (tightens SL)
USE_FVG_50_ENTRY = True

# Auto Break-Even Manager: Move SL to Entry if price goes in our favor
AUTO_BREAK_EVEN = True

# At what RR threshold should we move SL to Break Even? (1.0 = 1R profit)
BE_ACTIVATION_RATIO = 1.0

# =============================================================================
# HIGHER TIMEFRAME (HTF) TREND FILTER
# =============================================================================
USE_HTF_FILTER = True

# The timeframe to check for the overall trend (e.g., H1 = 60 minutes)
HTF_TIMEFRAME_MINUTES = 60

# The period of the Exponential Moving Average (EMA) to determine trend direction
# Price above EMA = Bullish bias (Only look for buys)
# Price below EMA = Bearish bias (Only look for sells)
HTF_EMA_PERIOD = 20

# =============================================================================
# SAFETY / EXECUTION
# =============================================================================

# DRY_RUN mode: True = log trades only, False = execute real trades
DRY_RUN = True

# Max number of simultaneous open trades
MAX_OPEN_TRADES = 2

# Max allowed spread in pips (skip trade if spread is wider)
MAX_SPREAD_PIPS = 2.0

# Cooldown to prevent rapid consecutive trades on the same symbol (in minutes)
TRADE_COOLDOWN_MINUTES = 15

# How often to check for signals (seconds)
SCAN_INTERVAL_SECONDS = 15

# How often to log the daily summary (seconds)
SUMMARY_LOG_INTERVAL_SECONDS = 300  # 5 minutes

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
