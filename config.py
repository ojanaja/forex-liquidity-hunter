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
# CONCURRENT TRADE LIMIT
# =============================================================================
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
MIN_CONFIRMATIONS       = 3        # 3 confluences for high-quality entries

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
SWEEP_THRESHOLD_PIPS    = 2.0      # Ignore micro-sweeps (too noisy at 0.5)
FVG_MIN_SIZE_PIPS       = 1.0      # Real institutional FVG needs > 1.0 pip
SL_BUFFER_PIPS          = 3.0      # Extra SL room to avoid stop hunts
TP_RATIO                = 3.0      # 1:3 RR — gives room for TP2 checkpoint (2.5R < 3.0R)
USE_FVG_50_ENTRY        = True     # 50% Consequent Encroachment

# --- RSI Parameters ---
RSI_PERIOD = 14
RSI_OB     = 75  # Overbought (tighter = fewer but better signals)
RSI_OS     = 25  # Oversold   (tighter = fewer but better signals)

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
TP_CHECKPOINTS              = [1.5, 2.5, 3.5]    # Higher checkpoints = bigger partial wins
TP_PARTIAL_CLOSE_PCTS       = [0.80, 0.10, 0.00]  # TP1: 80%, TP2: 10%, TP3: keep 10% trailing
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
    "EURAUDx", "EURGBPx", "GBPUSDx", "GBPJPYx", "AUDUSDx", "XAUUSDx",
]

# =============================================================================
# SAFETY / EXECUTION
# =============================================================================
DRY_RUN = False                    # True = log only, False = real trades
MAX_SPREAD_PIPS = 80.0             # Max allowed spread (80.0 for Gold)
TRADE_COOLDOWN_MINUTES = 30        # Prevent rapid re-entry on same symbol
SCAN_INTERVAL_SECONDS = 10         # How often to check for signals
SUMMARY_LOG_INTERVAL_SECONDS = 300 # 5 minutes

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
