# 🏦 Forex Liquidity Hunter

A Python-based scalping bot for **WeMasterTrade 10k prop firm accounts** using MetaTrader 5.

## Strategy: Session Liquidity Sweep

The bot identifies **liquidity grabs** (fakeout sweeps) at the open of major forex sessions, then trades the **reversal**. It targets the Asia High/Low being swept at the London or New York open.

## ⚡ Quick Start (Windows Laptop)

### Prerequisites
- **Windows 10/11** (required for MetaTrader5 Python library)
- **Python 3.11+** → [Download](https://www.python.org/downloads/)
- **MetaTrader 5** → [Download](https://www.metatrader5.com/en/download) (logged into your WeMasterTrade account)

### Installation

```powershell
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/forex-liquidity-hunter.git
cd forex-liquidity-hunter

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env file
copy .env.example .env
# Then edit .env with your MT5 credentials (login, password, server)

# 4. Open MetaTrader 5
# → Login to your WeMasterTrade account
# → Options → Expert Advisors → ✅ Allow algorithmic trading

# 5. Run the bot (DRY_RUN mode by default)
python main.py
```

### Going Live

> ⚠️ **IMPORTANT**: The bot starts in **DRY_RUN mode** (no real trades). Only switch to live after testing on a demo account.

1. Open `config.py`
2. Change `DRY_RUN = True` → `DRY_RUN = False`
3. Restart the bot: `python main.py`

## 📋 Prop Firm Rules (Auto-Enforced)

| Rule | Limit | Bot Setting (Safe Buffer) |
|---|---|---|
| Daily Loss | < $200 | Stops at **$150** |
| Total Loss | < $400 | Stops at **$350** |
| Profit Ratio | ≥ 6% ($600) | Tracks progress |
| Profit Consistency | ≤ 30% | Daily cap at **$120** |
| Risk Consistency | < 2% | Max **0.5%** per trade |

## 🕐 Session Windows (UTC+7 / WIB)

| Session | Start | End |
|---|---|---|
| Tokyo | 07:00 | 09:00 |
| London | 15:00 | 17:00 |
| New York | 20:00 | 22:00 |

The bot **only scans for trades** during these windows. Outside of sessions, it sleeps.

## 📁 Project Structure

```
forex-liquidity-hunter/
├── main.py            # Entry point — run this
├── config.py          # All tunable parameters
├── mt5_bridge.py      # MetaTrader 5 communication
├── risk_manager.py    # Prop firm rule enforcement
├── strategy.py        # Liquidity Sweep detection
├── requirements.txt   # Python dependencies
├── .env.example       # Template for MT5 credentials
├── .gitignore
└── logs/              # Daily log files (auto-created)
    ├── bot_2025-03-25.log
    └── cumulative_stats.json
```

## ⚙️ Configuration

All parameters are in `config.py`. Key settings:

| Parameter | Default | Description |
|---|---|---|
| `DRY_RUN` | `True` | Simulation mode (no real trades) |
| `MAX_RISK_PER_TRADE_PCT` | `0.5` | Risk per trade (% of balance) |
| `DAILY_PROFIT_CAP` | `120` | Max daily profit before stopping |
| `DAILY_LOSS_LIMIT` | `150` | Max daily loss before stopping |
| `SWEEP_THRESHOLD_PIPS` | `3.0` | Min pips beyond range for a valid sweep |
| `TP_RATIO` | `1.5` | Take Profit as multiple of Stop Loss |
| `SCAN_INTERVAL_SECONDS` | `15` | How often to check for signals |

## 📊 Logs & Monitoring

- **Console**: Real-time trade signals and daily summaries
- **Log files**: `logs/bot_YYYY-MM-DD.log` (full debug history)
- **State file**: `logs/cumulative_stats.json` (survives restarts — tracks total P/L)

## 🛑 Emergency Stop

- Press `Ctrl+C` to stop the bot gracefully
- The bot will automatically close all positions if:
  - Daily loss limit is hit
  - Total loss limit is hit
  - An unexpected error occurs

## License

Private use only. Not financial advice.
