"""
Forex Liquidity Hunter - Configuration (V18 Disciplined Trader)
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
# ACCOUNT RULES
# =============================================================================
ACCOUNT_BALANCE         = 10000.0  # Default evaluation balance
MAX_RISK_PER_TRADE_PCT  = 1.0      # 1-2% risk per trade (Req #7)
DAILY_LOSS_LIMIT        = 150.0    # Stop trading if down $150 in a day
TOTAL_LOSS_LIMIT        = 350.0    # Stop trading if down $350 total
PROFIT_TARGET           = 600.0    # 6% monthly target
DAILY_PROFIT_CAP        = 200.0    # Consistency rule enforcement

# =============================================================================
# DAILY TRADE LIMIT (Req #1)
# =============================================================================
MAX_TRADES_PER_DAY = 3             # Max 1-3 trades per day
MAX_OPEN_TRADES    = 2             # Max simultaneous open trades

# =============================================================================
# HTF TREND FILTER (Req #2) — Dual EMA + Market Structure
# =============================================================================
USE_HTF_FILTER          = True
HTF_TIMEFRAME_MINUTES   = 60       # H1 for trend analysis
HTF_EMA_FAST            = 50       # EMA 50
HTF_EMA_SLOW            = 200      # EMA 200
HTF_EMA_PERIOD          = 20       # Legacy single EMA (kept for backtest compat)
HTF_STRUCTURE_LOOKBACK  = 20       # Candles to detect HH/HL/LH/LL

# =============================================================================
# LTF CONFIRMATION (Req #2)
# =============================================================================
LTF_TIMEFRAME_MINUTES   = 5        # M5 for entry timing
MIN_CONFIRMATIONS       = 2        # Need at least 2-3 confluences

# =============================================================================
# SIDEWAYS DETECTION (Req #3) — ATR + Bollinger Band Squeeze
# =============================================================================
ATR_PERIOD                  = 14
ATR_LOW_VOLATILITY_FACTOR   = 0.5   # ATR < 50% of rolling avg = low vol
BB_PERIOD                   = 20
BB_STD_DEV                  = 2.0
BB_SQUEEZE_THRESHOLD        = 0.003 # Band width / price < 0.3% = squeeze

# =============================================================================
# STRATEGY MODULES (V18 Multi-Engine)
# =============================================================================
ENABLE_SMC_SWEEP = True
ENABLE_BREAKOUT  = True
ENABLE_RSI_SCALP = True

# --- Strategy Parameters ---
SCAN_TIMEFRAME_MINUTES  = 1        # Fast M1 scanning
RANGE_TIMEFRAME_MINUTES = 15       # Session range identification
SWEEP_THRESHOLD_PIPS    = 0.5      # 0.5 pip sweep sensitivity
FVG_MIN_SIZE_PIPS       = 0.2      # 0.2 pips minimum gap
SL_BUFFER_PIPS          = 2.0      # 2.0 pips extra SL room
TP_RATIO                = 2.0      # Updated to 1:2 minimum RR (Req #7)
USE_FVG_50_ENTRY        = True     # 50% Consequent Encroachment

# --- RSI Parameters ---
RSI_PERIOD = 14
RSI_OB     = 70  # Overbought
RSI_OS     = 30  # Oversold

# --- Breakout Parameters ---
BREAKOUT_CONFIRMATION_CANDLES = 2

# --- Minimum Risk Reward (Req #7) ---
MIN_RISK_REWARD_RATIO = 2.0       # Minimum 1:2 RR required

# =============================================================================
# HYBRID TP CHECKPOINT SYSTEM
# =============================================================================
# Checkpoint levels in multiples of Risk (1R = SL distance)
# At each checkpoint: partial close + move SL to previous checkpoint
# After final checkpoint: remove TP and trail SL
#
# Example with TP_CHECKPOINTS = [1.0, 2.0, 3.0]:
#   TP1 (1R):  Close 40%, move SL to BE + commission
#   TP2 (2R):  Close 30%, move SL to TP1 level
#   TP3 (3R):  Keep remaining 30%, REMOVE TP, move SL to TP2, start trailing
# =============================================================================
ENABLE_CHECKPOINT_TP        = True
TP_CHECKPOINTS              = [1.0, 2.0, 3.0]    # Checkpoint levels in R multiples
TP_PARTIAL_CLOSE_PCTS       = [0.40, 0.30, 0.00]  # % of ORIGINAL volume to close at each
ENABLE_TRAILING_AFTER_FINAL = True                 # Trail SL after last checkpoint
TRAILING_STEP_PIPS          = 10.0                 # Trail SL step size in pips
TRAILING_ACTIVATION_R       = 3.0                  # Start trailing after this R level

# Commission / spread buffer for BE level
ESTIMATED_COMMISSION_PER_LOT = 7.0  # $ per round-turn lot
ESTIMATED_SPREAD_COST_PIPS  = 1.5   # Fallback spread cost in pips

# =============================================================================
# PAIR CORRELATION GROUPS (Req #6)
# =============================================================================
CORRELATION_GROUPS = [
    ["EURUSDx", "GBPUSDx", "EURGBPx"],                              # EUR/GBP vs USD
    ["USDJPYx", "EURJPYx", "GBPJPYx", "AUDJPYx", "CADJPYx"],       # JPY crosses
    ["AUDUSDx", "NZDUSDx", "EURAUDx"],                              # AUD/NZD cluster
    ["USDCADx", "USDCHFx"],                                         # USD longs
    ["XAUUSDx"],                                                     # Gold standalone
]
MAX_POSITIONS_PER_CORRELATION_GROUP = 1

# =============================================================================
# SESSION WINDOWS (UTC+7 / WIB)
# =============================================================================
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
    "EURUSDx", "GBPUSDx", "USDJPYx", "EURJPYx", "GBPJPYx", "XAUUSDx",
    "AUDUSDx", "NZDUSDx", "USDCADx", "USDCHFx", "EURGBPx", "EURAUDx",
    "AUDJPYx", "CADJPYx"
]

# =============================================================================
# SAFETY / EXECUTION
# =============================================================================
DRY_RUN = False                    # True = log only, False = real trades
MAX_SPREAD_PIPS = 80.0             # Max allowed spread (80.0 for Gold)
TRADE_COOLDOWN_MINUTES = 5         # Cooldown per symbol (minutes)
SCAN_INTERVAL_SECONDS = 10         # How often to check for signals
SUMMARY_LOG_INTERVAL_SECONDS = 300 # 5 minutes

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
