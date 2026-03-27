"""
Forex Liquidity Hunter - Configuration
Core settings for Risk, Strategy, and Connectivity.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# ACCOUNT & RISK SETTINGS
# =============================================================================
ACCOUNT_BALANCE = 10000.0
PROFIT_TARGET = 600.0         # Reward $600
DAILY_LOSS_LIMIT = 150.0      # Safety Stop $150
TOTAL_LOSS_LIMIT = 350.0      # Hard Stop $350
DAILY_PROFIT_CAP = 120.0      # Consistency rule log

MAX_RISK_PER_TRADE_PCT = 0.50  # Double risk (0.5%) for high-prob setups
MAX_OPEN_TRADES = 1            # Focus on 1 high-prob trade at a time
TRADE_COOLDOWN_MINUTES = 120   # 2-hour wait between trades for same signal
DAILY_TRADE_LIMIT = 3          # Hard cap of 1-3 trades per day

# =============================================================================
# STRATEGY PARAMETERS
# =============================================================================
SCAN_TIMEFRAME_MINUTES = 1    # Rapid scanning on M1
RANGE_TIMEFRAME_MINUTES = 15  # Session range context on M15
USE_FVG_50_ENTRY = False      # Aggressive entry at FVG start

# V17 STRATEGIES
ENABLE_SMC_SWEEP = True
ENABLE_BREAKOUT = True
ENABLE_RSI_SCALP = True

# V18 INTELLIGENCE
ADX_PERIOD = 14
ADX_TRENDING_THRESHOLD = 30   # Stricter trend filter (was 25)
HTF_TIMEFRAME_MINUTES = 60    # H1 context for EMA bias
HTF_EMA_PERIOD = 50           # Bias filter

# SMC Logic
SWEEP_THRESHOLD_PIPS = 2.0     # More significant sweep (was 1.0)
FVG_MIN_SIZE_PIPS = 1.5        # More convincing gap (was 1.0)
TP_RATIO = 2.0                 # Target 1:2 RR

# RSI Logic
RSI_PERIOD = 7
RSI_OB = 75
RSI_OS = 25
SL_BUFFER_PIPS = 2.0           # Extra room for RSI scalps

# Breakout Logic (London/NY)
BREAKOUT_BUFFER_PIPS = 1.5

# =============================================================================
# BREAK-EVEN PLUS (V18)
# =============================================================================
AUTO_BREAK_EVEN = True
BE_ACTIVATION_RATIO = 1.1     # Move to BE at 1.1R profit
BE_BUFFER_PIPS = 1.0          # Move SL to Entry + 1.0 pip (cover commission)

# =============================================================================
# CONNECTIVITY
# =============================================================================
MT5_LOGIN = os.getenv("MT5_LOGIN", "")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = "WeMasterTrade-Virtual"
MT5_PATH = ""                  # Optional path to terminal64.exe

DRY_RUN = False               # LIVE TRADING ENABLED
MAX_SPREAD_PIPS = 3.0         # Spread filter (WeMasterTrade spreads vary)

# =============================================================================
# SESSIONS (WIB - Western Indonesia Time)
# =============================================================================
# (Name, StartH, StartM, EndH, EndM)
SESSIONS = [
    ("Tokyo",   7,  0,   9,  0),   # 07:00 - 09:00 WIB
    ("London", 15,  0,  17,  0),   # 15:00 - 17:00 WIB
    ("NewYork", 20,  0,  22,  0),  # 20:00 - 22:00 WIB
]

TIMEZONE = "Asia/Jakarta"  # UTC+7

# =============================================================================
# TRADING PAIRS (Multi-Asset Global Selection)
# =============================================================================
SYMBOLS = [
    "EURUSDx", "GBPUSDx", "XAUUSDx", "USDJPYx", 
    "GBPJPYx", "USDCADx", "EURGBPx", "EURAUDx"
]

# =============================================================================
# LOGGING & HOUSEKEEPING
# =============================================================================
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
SUMMARY_LOG_INTERVAL_SECONDS = 360  # Log summary every 6 minutes
SCAN_INTERVAL_SECONDS = 5
