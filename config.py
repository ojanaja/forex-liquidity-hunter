"""
Forex Liquidity Hunter - Configuration (V17 Strategy Hybrid)
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

MAX_RISK_PER_TRADE_PCT = 0.25  # Lowered risk for multiple concurrent trades
MAX_OPEN_TRADES = 2            # Allowed 2 concurrent trades
TRADE_COOLDOWN_MINUTES = 10    # Faster turnaround
DAILY_TRADE_LIMIT = 5          # Higher daily trade count for V17 energy

# =============================================================================
# STRATEGY PARAMETERS (V17 RESTORED)
# =============================================================================
SCAN_TIMEFRAME_MINUTES = 1    # Rapid scanning on M1
RANGE_TIMEFRAME_MINUTES = 15  # Session range context on M15
USE_FVG_50_ENTRY = True       # 50% CE entry enabled for V17

# V17 STRATEGIES
ENABLE_SMC_SWEEP = True
ENABLE_BREAKOUT = True
ENABLE_RSI_SCALP = True

# V18 INTELLIGENCE (Still active but less restrictive)
ADX_PERIOD = 14
ADX_TRENDING_THRESHOLD = 25   # Standard V17 threshold
HTF_TIMEFRAME_MINUTES = 60    # H1 context for EMA bias
HTF_EMA_PERIOD = 20           # Fast V17 HTF Bias (was 50)
USE_HTF_FILTER = True

# SMC Logic (V17 Aggressive)
SWEEP_THRESHOLD_PIPS = 0.5     # Ultra-sensitive sweep
FVG_MIN_SIZE_PIPS = 0.5        # Sensitive gap
TP_RATIO = 1.5                 # V17 Standard RR

# RSI Logic
RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30
SL_BUFFER_PIPS = 2.0           # Extra room for RSI scalps

# Breakout Logic (London/NY)
BREAKOUT_BUFFER_PIPS = 1.5

# =============================================================================
# BREAK-EVEN PLUS (V18)
# =============================================================================
AUTO_BREAK_EVEN = True
BE_ACTIVATION_RATIO = 1.1     # Move to BE at 1.1R profit
BE_BUFFER_PIPS = 1.0          # Move SL to Entry + 1.0 pip

# =============================================================================
# CONNECTIVITY (VPS SAFE)
# =============================================================================
MT5_LOGIN = os.getenv("MT5_LOGIN", "")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = "WeMasterTrade-Virtual"
MT5_PATH = ""                  # Optional path to terminal64.exe

DRY_RUN = False               # LIVE TRADING ENABLED
MAX_SPREAD_PIPS = 80.0        # Gold compatible spread (V17 Setting)

# =============================================================================
# SESSIONS (WIB - Western Indonesia Time)
# =============================================================================
SESSIONS = [
    ("Tokyo",   7,  0,   9,  0),   # 07:00 - 09:00 WIB
    ("London", 15,  0,  17,  0),   # 15:00 - 17:00 WIB
    ("NewYork", 20,  0,  22,  0),  # 20:00 - 22:00 WIB
]

TIMEZONE = "Asia/Jakarta"  # UTC+7

# =============================================================================
# TRADING PAIRS (Multi-Asset 14 Pairs Restored)
# =============================================================================
SYMBOLS = [
    "EURUSDx", "GBPUSDx", "USDJPYx", "EURJPYx", "GBPJPYx", "XAUUSDx",
    "AUDUSDx", "NZDUSDx", "USDCADx", "USDCHFx", "EURGBPx", "EURAUDx", 
    "AUDJPYx", "CADJPYx"
]

# =============================================================================
# LOGGING & HOUSEKEEPING
# =============================================================================
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
SUMMARY_LOG_INTERVAL_SECONDS = 360
SCAN_INTERVAL_SECONDS = 5
