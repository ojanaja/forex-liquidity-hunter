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
ACCOUNT_BALANCE = 10000.0   # Actual current balance
# 0.5% risk (only $49/trade — protect $243 buffer)
MAX_RISK_PER_TRADE_PCT = 1.0
DAILY_LOSS_LIMIT = 100.0    # Stop if down $100 in a day
TOTAL_LOSS_LIMIT = 200.0    # Hard stop at $9,643 (safe above $9,600)
PROFIT_TARGET = 600.0    # 6% monthly target
DAILY_PROFIT_CAP = 200.0    # Consistency rule enforcement

# =============================================================================
# CONCURRENT TRADE LIMIT
# =============================================================================
MAX_OPEN_TRADES = 2             # Max simultaneous open trades

# =============================================================================
# HTF TREND FILTER (Req #2) — Dual EMA + Market Structure
# =============================================================================
USE_HTF_FILTER = True
HTF_TIMEFRAME_MINUTES = 60       # H1 for trend analysis
HTF_EMA_FAST = 50       # EMA 50
HTF_EMA_SLOW = 200      # EMA 200
HTF_EMA_PERIOD = 20       # Legacy single EMA (kept for backtest compat)
HTF_STRUCTURE_LOOKBACK = 20       # Candles to detect HH/HL/LH/LL

# =============================================================================
# LTF CONFIRMATION (Req #2)
# =============================================================================
LTF_TIMEFRAME_MINUTES = 5        # M5 for entry timing
MIN_CONFIRMATIONS = 3        # 3 confluences for high-quality entries

# =============================================================================
# SIDEWAYS DETECTION (Req #3) — ATR + Bollinger Band Squeeze
# =============================================================================
ATR_PERIOD = 14
ATR_LOW_VOLATILITY_FACTOR = 0.5   # ATR < 50% of rolling avg = low vol
BB_PERIOD = 20
BB_STD_DEV = 2.0
BB_SQUEEZE_THRESHOLD = 0.003  # Band width / price < 0.3% = squeeze

# =============================================================================
# STRATEGY MODULES (V19 Quant Trader)
# =============================================================================
ENABLE_ONLY_QUANT = True
ENABLE_SMC_SWEEP = False
ENABLE_BREAKOUT = False
ENABLE_RSI_SCALP = False

# --- Strategy Parameters ---
SCAN_TIMEFRAME_MINUTES = 1        # Fast M1 scanning
RANGE_TIMEFRAME_MINUTES = 15       # Session range identification
SWEEP_THRESHOLD_PIPS = 2.0      # Ignore micro-sweeps (too noisy at 0.5)
FVG_MIN_SIZE_PIPS = 1.0      # Real institutional FVG needs > 1.0 pip
SL_BUFFER_PIPS = 5.0      # Extra SL room to avoid stop hunts (was 3.0)
TP_RATIO = 2.0      # 1:2 RR — full TP, no partial close
USE_FVG_50_ENTRY = True     # 50% Consequent Encroachment

# --- Minimum SL Distance (prevents razor-thin SL) ---
# Risk stays the same because lot size auto-adjusts: wider SL = smaller lot
MIN_SL_PIPS = 15.0     # Min SL for forex pairs (e.g., EURUSD, GBPJPY)
MIN_SL_PIPS_XAU = 50.0     # Min SL for XAUUSD (Gold needs more room)

# --- Impulse Candle Filter (blocks entry against strong momentum) ---
IMPULSE_BODY_MULTIPLIER = 2.0      # Candle body > 2x avg body = impulse
# Prevents selling into huge green candles (or vice versa)

# --- RSI Parameters ---
RSI_PERIOD = 14
RSI_OB = 75  # Overbought (tighter = fewer but better signals)
RSI_OS = 25  # Oversold   (tighter = fewer but better signals)

# --- Breakout Parameters ---
BREAKOUT_CONFIRMATION_CANDLES = 2

# --- Quant Multi-Factor Parameters ---
QUANT_TIMEFRAME_MINUTES = 5
QUANT_LOOKBACK_BARS = 320
QUANT_EMA_FAST = 20
QUANT_EMA_SLOW = 80
QUANT_ATR_PERIOD = 14

QUANT_MOMENTUM_SHORT_BARS = 12
QUANT_MOMENTUM_LONG_BARS = 48
QUANT_MEAN_WINDOW = 60
QUANT_ZSCORE_WINDOW = 80

QUANT_VOL_SHORT_WINDOW = 24
QUANT_VOL_LONG_WINDOW = 96

QUANT_W_TREND = 0.45
QUANT_W_MOMENTUM = 0.35
QUANT_W_MEAN_REVERSION = 0.20
QUANT_W_VOL_PENALTY = 0.25

QUANT_SCORE_ENTRY_THRESHOLD = 0.20
QUANT_ATR_SL_MULTIPLIER = 1.8
QUANT_TP_R_MULTIPLIER = 2.0

# Optional per-symbol overrides for quant parameters.
# Use exact symbol names from SYMBOLS.
QUANT_SYMBOL_OVERRIDES = {
    "XAUUSDx": {
        "QUANT_W_TREND": 0.55,
        "QUANT_W_MOMENTUM": 0.25,
        "QUANT_W_MEAN_REVERSION": 0.20,
        "QUANT_W_VOL_PENALTY": 0.35,
        "QUANT_SCORE_ENTRY_THRESHOLD": 0.28,
        "QUANT_ATR_SL_MULTIPLIER": 2.1,
    },
    "GBPJPYx": {
        "QUANT_W_TREND": 0.50,
        "QUANT_W_MOMENTUM": 0.35,
        "QUANT_W_MEAN_REVERSION": 0.15,
        "QUANT_SCORE_ENTRY_THRESHOLD": 0.24,
    },
}

# Walk-forward optimization defaults
WFO_DAYS_BACK = 180
WFO_TRAIN_DAYS = 60
WFO_TEST_DAYS = 20
WFO_MIN_TRADES_PER_FOLD = 10
WFO_HOLD_BARS = 12
WFO_THRESHOLD_GRID = [0.12, 0.16, 0.20, 0.24, 0.28, 0.32]
QUANT_CSV_DATA_DIR = "data"

# --- Elliott Wave Parameters ---
ENABLE_ELLIOTT_WAVE = True
EW_ZIGZAG_DEPTH = 8        # Min bars between swings
EW_MIN_WAVE1_PIPS = 10.0     # Min Wave 1 size in pips
EW_WAVE2_RETRACE_MIN = 0.382    # Min Fibonacci retracement (38.2%)
EW_WAVE2_RETRACE_MAX = 0.786    # Max Fibonacci retracement (78.6%)
EW_LOOKBACK_BARS = 120      # M15 bars to analyze (120 × 15min = 30h)
EW_MAX_SL_PIPS = 50.0     # Max SL for EW trades

# --- Minimum Risk Reward (Req #7) ---
MIN_RISK_REWARD_RATIO = 2.0       # Minimum 1:2 RR required (matches TP_RATIO)

# =============================================================================
# BREAKEVEN + PARTIAL TP SYSTEM
# =============================================================================
# When price hits 1R (same distance as SL, i.e. 1:1 RR):
#   - Move SL to Break-Even + commission buffer (lock in zero-loss)
#   - Close 80% of position (secure profit)
#   - Remaining 20% rides to full TP at 2R
# =============================================================================
ENABLE_CHECKPOINT_TP = True
TP_CHECKPOINTS = [1.0]       # Single checkpoint at 1R (1:1 RR)
TP_PARTIAL_CLOSE_PCTS = [0.80]      # Close 80% at 1R
# No trailing — let remaining 20% hit TP at 2R
ENABLE_TRAILING_AFTER_FINAL = False
TRAILING_STEP_PIPS = 10.0        # (unused)
TRAILING_ACTIVATION_R = 3.0         # (unused)

# Commission / spread buffer for BE level
ESTIMATED_COMMISSION_PER_LOT = 7.0  # $ per round-turn lot
ESTIMATED_SPREAD_COST_PIPS = 1.5   # Fallback spread cost in pips

# =============================================================================
# PAIR CORRELATION GROUPS (Req #6)
# =============================================================================
CORRELATION_GROUPS = [
    # EUR/GBP vs USD
    ["EURUSDx", "GBPUSDx", "EURGBPx"],
    ["USDJPYx", "EURJPYx", "GBPJPYx", "AUDJPYx", "CADJPYx"],       # JPY crosses
    # AUD/NZD cluster
    ["AUDUSDx", "NZDUSDx", "EURAUDx"],
    ["USDCADx", "USDCHFx"],                                         # USD longs
    # Gold standalone
    ["XAUUSDx"],
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
# NEWS FILTER (Avoid entry near high-impact economic news)
# =============================================================================
ENABLE_NEWS_FILTER = True
NEWS_BLACKOUT_MINUTES_BEFORE = 15      # Block entry X minutes before news
NEWS_BLACKOUT_MINUTES_AFTER = 10      # Block entry X minutes after news
NEWS_MIN_IMPORTANCE = "HIGH"  # "HIGH", "MODERATE", or "LOW"
NEWS_CACHE_MINUTES = 30      # How often to re-fetch calendar
NEWS_AFFECTED_CURRENCIES = [       # Currencies we care about
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU",
]

# =============================================================================
# SAFETY / EXECUTION
# =============================================================================
DRY_RUN = True                     # SAFETY: validate strategy before risking real $
MAX_SPREAD_PIPS = 80.0             # Max allowed spread (80.0 for Gold)
TRADE_COOLDOWN_MINUTES = 30        # Prevent rapid re-entry on same symbol
SCAN_INTERVAL_SECONDS = 10         # How often to check for signals
SUMMARY_LOG_INTERVAL_SECONDS = 300  # 5 minutes

# =============================================================================
# TELEGRAM NOTIFICATIONS
# =============================================================================
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "True").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# =============================================================================
# SCHEDULED REPORTS
# =============================================================================
DAILY_REPORT_HOUR = 6       # Jam kirim daily report (WIB)
ENABLE_WEEKLY_REPORT = True    # Kirim weekly report setiap Senin
ENABLE_MONTHLY_REPORT = True    # Kirim monthly report setiap tanggal 1
REPORTS_DIR = "reports"

# Logging
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
