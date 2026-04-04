"""
Forex Liquidity Hunter — Web Monitoring Dashboard
====================================================
A lightweight Flask web server that provides a real-time browser-based
dashboard for monitoring the trading bot without needing Remote Desktop.

Run alongside the bot:
    python web_dashboard.py

Access: http://<VPS_IP>:5000/
"""
import json
import os
import glob
import functools
from datetime import datetime, date

from flask import (
    Flask, jsonify, request, send_from_directory, Response, send_file
)

import config

# ======================================================================
# Configuration
# ======================================================================
DASHBOARD_PORT = 5000
DASHBOARD_HOST = "0.0.0.0"  # Accessible from any IP

# Hardcoded credentials — change these!
AUTH_USERNAME = "admin"
AUTH_PASSWORD = "hunter2024"

# Heartbeat threshold: if heartbeat older than this, bot is considered stopped
HEARTBEAT_TIMEOUT_SECONDS = 120

# ======================================================================
# Flask App
# ======================================================================
app = Flask(__name__, static_folder="static", static_url_path="/static")


# ======================================================================
# Basic Auth
# ======================================================================
def check_auth(username, password):
    """Verify credentials."""
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        "Access denied. Please provide valid credentials.",
        401,
        {"WWW-Authenticate": 'Basic realm="Forex Liquidity Hunter Dashboard"'},
    )


def requires_auth(f):
    """Decorator for basic auth on routes."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# ======================================================================
# Helper: Read JSON files safely
# ======================================================================
def _read_json(filepath: str) -> dict | list | None:
    """Read a JSON file, return None on failure."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _get_heartbeat() -> dict:
    """Read the bot heartbeat file."""
    hb = _read_json(os.path.join(config.LOG_DIR, "heartbeat.json"))
    if hb is None:
        return {"status": "unknown", "timestamp": None}
    return hb


def _is_bot_running() -> bool:
    """Check if the bot is running based on heartbeat freshness."""
    hb = _get_heartbeat()
    ts = hb.get("timestamp")
    if not ts:
        return False
    try:
        hb_time = datetime.fromisoformat(ts)
        now = datetime.now(hb_time.tzinfo)
        diff = (now - hb_time).total_seconds()
        return diff < HEARTBEAT_TIMEOUT_SECONDS
    except (ValueError, TypeError):
        return False


# ======================================================================
# Routes: Dashboard
# ======================================================================
@app.route("/")
@requires_auth
def index():
    """Serve the main dashboard."""
    return send_from_directory("static", "index.html")


# ======================================================================
# API: Bot Status
# ======================================================================
@app.route("/api/status")
@requires_auth
def api_status():
    """Return bot status, heartbeat, and config summary."""
    hb = _get_heartbeat()
    running = _is_bot_running()

    # Read cumulative stats
    stats_file = os.path.join(config.LOG_DIR, "cumulative_stats.json")
    cum_stats = _read_json(stats_file) or {}

    return jsonify({
        "bot_running": running,
        "heartbeat": hb,
        "mode": "DRY RUN" if config.DRY_RUN else "LIVE",
        "version": "v1.9",
        "cumulative_pnl": cum_stats.get("cumulative_pnl", 0.0),
        "daily_realized_pnl": cum_stats.get("daily_realized_pnl", 0.0),
        "total_trade_count": cum_stats.get("total_trade_count", 0),
        "account_balance": config.ACCOUNT_BALANCE,
        "profit_target": config.PROFIT_TARGET,
        "daily_loss_limit": config.DAILY_LOSS_LIMIT,
        "max_risk_pct": config.MAX_RISK_PER_TRADE_PCT,
        "max_open_trades": config.MAX_OPEN_TRADES,
        "symbols": config.SYMBOLS,
        "sessions": [(s[0], f"{s[1]:02d}:{s[2]:02d}-{s[3]:02d}:{s[4]:02d}") for s in config.SESSIONS],
        "scan_interval": config.SCAN_INTERVAL_SECONDS,
    })


# ======================================================================
# API: Open Trades
# ======================================================================
@app.route("/api/trades/open")
@requires_auth
def api_trades_open():
    """Return currently open virtual trades."""
    trades_file = os.path.join(config.LOG_DIR, "dry_run_trades.json")
    data = _read_json(trades_file)

    if data is None:
        return jsonify({"trades": [], "count": 0})

    open_trades = list(data.get("open_trades", {}).values())

    return jsonify({
        "trades": open_trades,
        "count": len(open_trades),
    })


# ======================================================================
# API: Closed Trades
# ======================================================================
@app.route("/api/trades/closed")
@requires_auth
def api_trades_closed():
    """Return closed trade history with optional date filter and pagination."""
    trades_file = os.path.join(config.LOG_DIR, "dry_run_trades.json")
    data = _read_json(trades_file)

    if data is None:
        return jsonify({"trades": [], "count": 0, "total": 0})

    closed_trades = data.get("closed_trades", [])

    # Date filter
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    if date_from:
        closed_trades = [
            t for t in closed_trades
            if t.get("close_time", "") >= date_from
        ]
    if date_to:
        closed_trades = [
            t for t in closed_trades
            if t.get("close_time", "")[:10] <= date_to
        ]

    # Sort by close_time descending (most recent first)
    closed_trades.sort(key=lambda t: t.get("close_time", ""), reverse=True)

    total = len(closed_trades)

    # Pagination
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        "trades": closed_trades[start:end],
        "count": len(closed_trades[start:end]),
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# ======================================================================
# API: Stats
# ======================================================================
@app.route("/api/stats")
@requires_auth
def api_stats():
    """Return trading statistics."""
    stats_file = os.path.join(config.LOG_DIR, "cumulative_stats.json")
    cum_stats = _read_json(stats_file) or {}

    trades_file = os.path.join(config.LOG_DIR, "dry_run_trades.json")
    data = _read_json(trades_file) or {}

    closed_trades = data.get("closed_trades", [])
    open_trades = list(data.get("open_trades", {}).values())

    # Calculate stats from closed trades
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t.get("pnl", 0) >= 0)
    losses = sum(1 for t in closed_trades if t.get("pnl", 0) < 0)
    total_pnl = sum(t.get("pnl", 0) for t in closed_trades)
    win_rate = (wins / total * 100) if total > 0 else 0

    # Profit factor
    gross_wins = sum(t.get("pnl", 0) for t in closed_trades if t.get("pnl", 0) > 0)
    gross_losses = abs(sum(t.get("pnl", 0) for t in closed_trades if t.get("pnl", 0) < 0))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0

    # Equity curve (running P&L)
    equity_curve = []
    running = 0
    sorted_trades = sorted(closed_trades, key=lambda t: t.get("close_time", ""))
    for t in sorted_trades:
        running += t.get("pnl", 0)
        equity_curve.append({
            "time": t.get("close_time", "")[:16],
            "pnl": round(running, 2),
            "symbol": t.get("symbol", ""),
        })

    # Per-pair stats
    pair_stats = {}
    for t in closed_trades:
        sym = t.get("symbol", "unknown")
        if sym not in pair_stats:
            pair_stats[sym] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        pair_stats[sym]["trades"] += 1
        pair_stats[sym]["pnl"] = round(pair_stats[sym]["pnl"] + t.get("pnl", 0), 2)
        if t.get("pnl", 0) >= 0:
            pair_stats[sym]["wins"] += 1
        else:
            pair_stats[sym]["losses"] += 1

    # Daily P&L history
    daily_pnl = {}
    for t in sorted_trades:
        ct = t.get("close_time", "")[:10]
        if ct:
            daily_pnl[ct] = round(daily_pnl.get(ct, 0) + t.get("pnl", 0), 2)

    daily_pnl_list = [{"date": k, "pnl": v} for k, v in sorted(daily_pnl.items())]

    # Max drawdown
    peak = 0
    max_dd = 0
    running = 0
    for t in sorted_trades:
        running += t.get("pnl", 0)
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # SL/TP breakdown
    sl_count = sum(1 for t in closed_trades if t.get("close_reason") == "SL")
    tp_count = sum(1 for t in closed_trades if t.get("close_reason") == "TP")

    # Win/loss streaks
    longest_win = 0
    longest_loss = 0
    cur_win = 0
    cur_loss = 0
    for t in sorted_trades:
        if t.get("pnl", 0) >= 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)

    return jsonify({
        "total_trades": total,
        "open_trades_count": len(open_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / total, 2) if total > 0 else 0,
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "sl_count": sl_count,
        "tp_count": tp_count,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "cumulative_pnl": cum_stats.get("cumulative_pnl", 0) + cum_stats.get("daily_realized_pnl", 0),
        "daily_realized_pnl": cum_stats.get("daily_realized_pnl", 0),
        "daily_profits": cum_stats.get("daily_profits", []),
        "equity_curve": equity_curve,
        "pair_stats": pair_stats,
        "daily_pnl": daily_pnl_list,
        "profit_target": config.PROFIT_TARGET,
        "account_balance": config.ACCOUNT_BALANCE,
    })


# ======================================================================
# API: Logs
# ======================================================================
@app.route("/api/logs")
@requires_auth
def api_logs():
    """Return the latest N lines from today's log file."""
    lines_count = int(request.args.get("lines", 100))
    log_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

    log_file = os.path.join(config.LOG_DIR, f"bot_{log_date}.log")

    if not os.path.exists(log_file):
        # Try to find any available log files
        available = sorted(glob.glob(os.path.join(config.LOG_DIR, "bot_*.log")))
        return jsonify({
            "lines": [],
            "file": log_file,
            "exists": False,
            "available_dates": [
                os.path.basename(f).replace("bot_", "").replace(".log", "")
                for f in available[-10:]
            ],
        })

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        # Return the last N lines
        tail = all_lines[-lines_count:]

        return jsonify({
            "lines": [line.rstrip() for line in tail],
            "file": os.path.basename(log_file),
            "exists": True,
            "total_lines": len(all_lines),
        })
    except IOError:
        return jsonify({"lines": [], "file": log_file, "exists": False})


# ======================================================================
# API: Reports
# ======================================================================
@app.route("/api/reports")
@requires_auth
def api_reports():
    """List available PDF reports."""
    reports_dir = getattr(config, "REPORTS_DIR", "reports")
    if not os.path.isdir(reports_dir):
        return jsonify({"reports": []})

    files = sorted(glob.glob(os.path.join(reports_dir, "*.pdf")), reverse=True)

    reports = []
    for f in files:
        basename = os.path.basename(f)
        size = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).isoformat()

        report_type = "daily"
        if "weekly" in basename:
            report_type = "weekly"
        elif "monthly" in basename:
            report_type = "monthly"

        reports.append({
            "filename": basename,
            "type": report_type,
            "size_kb": round(size / 1024, 1),
            "modified": mtime,
        })

    return jsonify({"reports": reports})


@app.route("/api/reports/<filename>")
@requires_auth
def api_report_download(filename):
    """Download a specific PDF report."""
    reports_dir = getattr(config, "REPORTS_DIR", "reports")
    filepath = os.path.join(reports_dir, filename)

    if not os.path.exists(filepath) or not filename.endswith(".pdf"):
        return jsonify({"error": "Report not found"}), 404

    return send_file(
        filepath,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ======================================================================
# API: Config (read-only)
# ======================================================================
@app.route("/api/config")
@requires_auth
def api_config():
    """Return current bot configuration (read-only, no secrets)."""
    return jsonify({
        "account_balance": config.ACCOUNT_BALANCE,
        "max_risk_per_trade_pct": config.MAX_RISK_PER_TRADE_PCT,
        "daily_loss_limit": config.DAILY_LOSS_LIMIT,
        "total_loss_limit": config.TOTAL_LOSS_LIMIT,
        "profit_target": config.PROFIT_TARGET,
        "daily_profit_cap": config.DAILY_PROFIT_CAP,
        "max_open_trades": config.MAX_OPEN_TRADES,
        "min_risk_reward_ratio": config.MIN_RISK_REWARD_RATIO,
        "min_confirmations": config.MIN_CONFIRMATIONS,
        "symbols": config.SYMBOLS,
        "sessions": [
            {"name": s[0], "start": f"{s[1]:02d}:{s[2]:02d}", "end": f"{s[3]:02d}:{s[4]:02d}"}
            for s in config.SESSIONS
        ],
        "timezone": config.TIMEZONE,
        "dry_run": config.DRY_RUN,
        "scan_interval_seconds": config.SCAN_INTERVAL_SECONDS,
        "trade_cooldown_minutes": getattr(config, "TRADE_COOLDOWN_MINUTES", 15),
        "enable_checkpoint_tp": config.ENABLE_CHECKPOINT_TP,
        "tp_checkpoints": config.TP_CHECKPOINTS,
        "tp_partial_close_pcts": config.TP_PARTIAL_CLOSE_PCTS,
        "enable_news_filter": config.ENABLE_NEWS_FILTER,
        "news_blackout_before": config.NEWS_BLACKOUT_MINUTES_BEFORE,
        "news_blackout_after": config.NEWS_BLACKOUT_MINUTES_AFTER,
        "correlation_groups": config.CORRELATION_GROUPS,
        "htf_timeframe": config.HTF_TIMEFRAME_MINUTES,
        "ltf_timeframe": config.LTF_TIMEFRAME_MINUTES,
        "quant_score_threshold": config.QUANT_SCORE_ENTRY_THRESHOLD,
        "enable_elliott_wave": config.ENABLE_ELLIOTT_WAVE,
    })


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    print(f"""
╔═══════════════════════════════════════════════════╗
║   FOREX LIQUIDITY HUNTER — Web Dashboard          ║
║   http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/       ║
║   Auth: {AUTH_USERNAME} / {'*' * len(AUTH_PASSWORD)}              ║
╚═══════════════════════════════════════════════════╝
    """)
    app.run(
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        debug=False,
        threaded=True,
    )
