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
MAX_RISK_PER_TRADE_PCT  = 0.25     # Lowered to 0.25% for V15 Sniper-X (Allow 2 trades)
DAILY_LOSS_LIMIT        = 150.0    # Stop trading if down $150 in a day
TOTAL_LOSS_LIMIT        = 350.0    # Stop trading if down $350 total
PROFIT_TARGET           = 600.0    # Reaching 6% month is a high-tier professional result
DAILY_PROFIT_CAP        = 200.0    # Tight 30% consistency rule enforcement
# Max open trades (Increased to 2 for Sniper-X)
MAX_OPEN_TRADES = 2

# ─── Strategy Parameters (V15 Sniper-X Aggressive) ───────────────────────────
SCAN_TIMEFRAME_MINUTES  = 1        # Fast M1 scanning
RANGE_TIMEFRAME_MINUTES = 15       # Session range identification
SWEEP_THRESHOLD_PIPS    = 0.5      # More sensitive 0.5 pip sweep
FVG_MIN_SIZE_PIPS       = 0.2      # 0.2 pips minimum gap
SL_BUFFER_PIPS          = 2.0      # 2.0 pips extra SL room
TP_RATIO                = 1.5      # Stable 1.5:1 Reward to Risk
AUTO_BREAK_EVEN         = True     # Protected trades
BE_ACTIVATION_RATIO     = 1.1      # 1.1R before moving to BE
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
DRY_RUN = False

# Max allowed spread in pips (80.0 for Gold compatibility)
MAX_SPREAD_PIPS = 80.0

# Cooldown to prevent rapid consecutive trades on the same symbol (in minutes)
TRADE_COOLDOWN_MINUTES = 5

# How often to check for signals (seconds)
SCAN_INTERVAL_SECONDS = 10

# How often to log the daily summary (seconds)
SUMMARY_LOG_INTERVAL_SECONDS = 300  # 5 minutes

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
