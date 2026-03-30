"""
Forex Liquidity Hunter — Telegram Notifier
============================================
Sends trade notifications to Telegram via Bot API.

Notifications sent:
  - When a new position is OPENED
  - When a position is CLOSED (with P/L)
  - Daily summary reports

Setup:
  1. Create a bot via @BotFather on Telegram → get the BOT_TOKEN
  2. Send a message to your bot, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your CHAT_ID
  3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file
"""
import logging
import threading
from datetime import datetime
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


# ===========================================================================
# Telegram API
# ===========================================================================

def _send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message to the configured Telegram chat.
    Runs in a background thread to avoid blocking the main loop.
    Returns True if the message was queued successfully.
    """
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.debug("Telegram not configured (missing BOT_TOKEN or CHAT_ID). Skipping.")
        return False

    def _do_send():
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug("Telegram message sent successfully.")
            else:
                logger.warning(
                    f"Telegram send failed: HTTP {resp.status_code} — {resp.text}"
                )
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")

    # Fire-and-forget in background thread so it won't block trading
    thread = threading.Thread(target=_do_send, daemon=True)
    thread.start()
    return True


# ===========================================================================
# Notification Builders
# ===========================================================================

def notify_trade_opened(
    symbol: str,
    direction: str,
    lot_size: float,
    entry_price: float,
    sl: float,
    tp: float,
    rr_ratio: float,
    reason: str,
    session: str,
    ticket: Optional[int] = None,
):
    """Send a notification when a new trade is opened."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    emoji = "🟢" if direction == "BUY" else "🔴"
    mode = "🧪 DRY RUN" if config.DRY_RUN else "🔥 LIVE"
    ticket_str = f"#{ticket}" if ticket else "—"

    sl_pips = abs(entry_price - sl)
    tp_pips = abs(tp - entry_price)

    # Get pip size for display
    sym_info_digits = 5  # default
    try:
        import mt5_bridge
        sym_info = mt5_bridge.get_symbol_info(symbol)
        if sym_info:
            pip_size = sym_info.point * 10 if sym_info.digits in (3, 5) else sym_info.point
            sl_pips = abs(entry_price - sl) / pip_size
            tp_pips = abs(tp - entry_price) / pip_size
    except Exception:
        pass

    text = (
        f"{emoji} <b>NEW TRADE OPENED</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol}</b> — {direction}\n"
        f"🎟 Ticket: <code>{ticket_str}</code>\n"
        f"📈 Entry: <code>{entry_price:.5f}</code>\n"
        f"🛑 SL: <code>{sl:.5f}</code> ({sl_pips:.1f} pips)\n"
        f"🎯 TP: <code>{tp:.5f}</code> ({tp_pips:.1f} pips)\n"
        f"📐 RR: <b>1:{rr_ratio:.2f}</b>\n"
        f"💰 Lot: <code>{lot_size}</code>\n"
        f"⏰ Session: {session}\n"
        f"📋 Strategy: {reason}\n"
        f"🏷 Mode: {mode}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info(f"[TELEGRAM] Trade opened notification sent for {symbol}")


def notify_trade_closed(
    ticket: int,
    symbol: str,
    direction: str,
    gross_profit: float,
    commission: float,
    swap: float,
    net_profit: float,
):
    """Send a notification when a trade is closed."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    emoji = "✅" if net_profit >= 0 else "❌"
    pnl_emoji = "💰" if net_profit >= 0 else "💸"

    text = (
        f"{emoji} <b>TRADE CLOSED</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol}</b> — {direction}\n"
        f"🎟 Ticket: <code>#{ticket}</code>\n"
        f"{pnl_emoji} Gross P/L: <code>${gross_profit:+.2f}</code>\n"
        f"💳 Commission: <code>${commission:+.2f}</code>\n"
        f"🔄 Swap: <code>${swap:+.2f}</code>\n"
        f"<b>{pnl_emoji} Net P/L: <code>${net_profit:+.2f}</code></b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info(f"[TELEGRAM] Trade closed notification sent for {symbol} (${net_profit:+.2f})")


def notify_daily_summary(
    balance: float,
    equity: float,
    daily_pnl: float,
    total_trades: int,
    wins: int,
    losses: int,
):
    """Send a daily trading summary."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

    text = (
        f"📊 <b>DAILY SUMMARY</b> 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <code>${balance:.2f}</code>\n"
        f"📊 Equity: <code>${equity:.2f}</code>\n"
        f"{pnl_emoji} Daily P/L: <code>${daily_pnl:+.2f}</code>\n"
        f"📈 Trades: {total_trades} (W:{wins} / L:{losses})\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info("[TELEGRAM] Daily summary sent.")


def notify_bot_started():
    """Send a notification when the bot starts."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    mode = "🧪 DRY RUN" if config.DRY_RUN else "🔥 LIVE"
    symbols = ", ".join(config.SYMBOLS)

    text = (
        f"🚀 <b>BOT STARTED</b> 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 Mode: {mode}\n"
        f"📊 Pairs: {symbols}\n"
        f"⚙️ Max Trades: {config.MAX_OPEN_TRADES}\n"
        f"💰 Risk/Trade: {config.MAX_RISK_PER_TRADE_PCT}%\n"
        f"🛑 Daily Loss Limit: ${config.DAILY_LOSS_LIMIT}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info("[TELEGRAM] Bot started notification sent.")


def notify_bot_stopped(reason: str = "Manual shutdown"):
    """Send a notification when the bot stops."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    text = (
        f"🛑 <b>BOT STOPPED</b> 🛑\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Reason: {reason}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info("[TELEGRAM] Bot stopped notification sent.")


def notify_checkpoint_hit(
    symbol: str,
    ticket: int,
    checkpoint_name: str,
    rr_achieved: float,
    partial_closed: float = 0,
):
    """Send notification when a TP checkpoint is hit."""
    if not getattr(config, "ENABLE_TELEGRAM", False):
        return

    text = (
        f"🎯 <b>CHECKPOINT HIT: {checkpoint_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{symbol}</b> — Ticket <code>#{ticket}</code>\n"
        f"📐 R Achieved: <b>{rr_achieved:.2f}R</b>\n"
    )

    if partial_closed > 0:
        text += f"✂️ Partial Close: {partial_closed} lots\n"

    text += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    _send_message(text)
    logger.info(f"[TELEGRAM] Checkpoint {checkpoint_name} notification for {symbol}")
