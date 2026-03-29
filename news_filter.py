"""
Forex Liquidity Hunter – News Filter Module (V18)
===================================================
Blocks trade entries near high-impact economic news events.

Dual-source approach:
  1. MT5 Built-in Economic Calendar (primary)
  2. Static recurring schedule (fallback for known events)

Usage:
  from news_filter import news_filter
  is_blackout, reason = news_filter.is_news_blackout("EURUSDx")
"""
import logging
from datetime import datetime, timedelta, date
from typing import Optional
import calendar as cal

import pytz

import config
import mt5_bridge

logger = logging.getLogger(__name__)


# ======================================================================
# Currency ↔ Symbol Mapping
# ======================================================================

# Map base/quote currencies to the symbols that are affected
_CURRENCY_IN_SYMBOL = {
    "EUR": ["EURAUDx", "EURGBPx", "EURUSDx", "EURJPYx"],
    "USD": ["EURUSDx", "GBPUSDx", "AUDUSDx", "USDJPYx", "USDCADx", "USDCHFx", "XAUUSDx"],
    "GBP": ["GBPUSDx", "GBPJPYx", "EURGBPx"],
    "JPY": ["USDJPYx", "EURJPYx", "GBPJPYx", "AUDJPYx", "CADJPYx"],
    "AUD": ["AUDUSDx", "AUDJPYx", "EURAUDx"],
    "NZD": ["NZDUSDx"],
    "CAD": ["USDCADx", "CADJPYx"],
    "CHF": ["USDCHFx"],
    "XAU": ["XAUUSDx"],
}


def _symbol_affected_by_currency(symbol: str, currency: str) -> bool:
    """Check if a trading symbol is affected by news for a given currency."""
    affected_symbols = _CURRENCY_IN_SYMBOL.get(currency, [])
    # Handle suffix variations (e.g., EURUSDx, EURUSD, EURUSD.r)
    symbol_base = symbol.rstrip("x").rstrip(".r").upper()
    return any(
        s.rstrip("x").upper() == symbol_base
        for s in affected_symbols
    ) or currency in symbol_base


def _extract_currencies_from_symbol(symbol: str) -> list[str]:
    """Extract the base and quote currencies from a symbol name."""
    clean = symbol.rstrip("x").rstrip(".r").upper()

    # Special case: Gold
    if "XAU" in clean:
        return ["XAU", "USD"]

    # Standard forex pair: first 3 chars = base, next 3 = quote
    if len(clean) >= 6:
        return [clean[:3], clean[3:6]]

    return []


# ======================================================================
# Static Recurring Schedule (Fallback)
# ======================================================================

def _get_nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Get the nth occurrence of a weekday in a month (1-indexed)."""
    first_day = date(year, month, 1)
    # Days until first occurrence of weekday
    days_ahead = weekday - first_day.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


def _generate_static_schedule(year: int) -> list[dict]:
    """
    Generate a static schedule of known high-impact recurring events.

    All times are in UTC. The bot converts to local tz for comparison.
    These serve as fallback when MT5 calendar is unavailable.
    """
    events = []
    tz = pytz.UTC

    for month in range(1, 13):
        # ── NFP (Non-Farm Payrolls) ──
        # First Friday of each month, 08:30 ET = 13:30 UTC
        first_friday = _get_nth_weekday(year, month, 4, 1)  # 4 = Friday
        events.append({
            "time": datetime(first_friday.year, first_friday.month, first_friday.day,
                             13, 30, tzinfo=tz),
            "currency": "USD",
            "importance": "HIGH",
            "event_name": "Non-Farm Payrolls (NFP)",
            "source": "static",
        })

        # ── US CPI (Consumer Price Index) ──
        # Usually around 10th-13th of each month, 08:30 ET = 13:30 UTC
        # Approximate: second Tuesday-Thursday
        second_tuesday = _get_nth_weekday(year, month, 1, 2)  # 1 = Tuesday
        cpi_date = second_tuesday + timedelta(days=1)  # Wednesday estimate
        events.append({
            "time": datetime(cpi_date.year, cpi_date.month, cpi_date.day,
                             13, 30, tzinfo=tz),
            "currency": "USD",
            "importance": "HIGH",
            "event_name": "US CPI (Consumer Price Index)",
            "source": "static",
        })

        # ── US PPI ──
        # Usually day after CPI
        ppi_date = cpi_date + timedelta(days=1)
        events.append({
            "time": datetime(ppi_date.year, ppi_date.month, ppi_date.day,
                             13, 30, tzinfo=tz),
            "currency": "USD",
            "importance": "HIGH",
            "event_name": "US PPI (Producer Price Index)",
            "source": "static",
        })

        # ── ECB Interest Rate Decision ──
        # ~8 meetings per year (Jan, Mar, Apr, Jun, Jul, Sep, Oct, Dec)
        if month in (1, 3, 4, 6, 7, 9, 10, 12):
            # Usually second or third Thursday, 13:15 CET = 12:15 UTC
            second_thurs = _get_nth_weekday(year, month, 3, 2)
            events.append({
                "time": datetime(second_thurs.year, second_thurs.month, second_thurs.day,
                                 12, 15, tzinfo=tz),
                "currency": "EUR",
                "importance": "HIGH",
                "event_name": "ECB Interest Rate Decision",
                "source": "static",
            })

        # ── BOE Interest Rate Decision ──
        # ~8 meetings per year (Feb, Mar, May, Jun, Aug, Sep, Nov, Dec)
        if month in (2, 3, 5, 6, 8, 9, 11, 12):
            second_thurs = _get_nth_weekday(year, month, 3, 2)
            events.append({
                "time": datetime(second_thurs.year, second_thurs.month, second_thurs.day,
                                 12, 0, tzinfo=tz),
                "currency": "GBP",
                "importance": "HIGH",
                "event_name": "BOE Interest Rate Decision",
                "source": "static",
            })

        # ── RBA Interest Rate Decision ──
        # First Tuesday of each month except January
        if month != 1:
            first_tues = _get_nth_weekday(year, month, 1, 1)
            events.append({
                "time": datetime(first_tues.year, first_tues.month, first_tues.day,
                                 3, 30, tzinfo=tz),
                "currency": "AUD",
                "importance": "HIGH",
                "event_name": "RBA Interest Rate Decision",
                "source": "static",
            })

    # ── FOMC Rate Decision ──
    # 8 meetings per year, approximately:
    # Jan, Mar, May, Jun, Jul, Sep, Nov, Dec
    fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
    for month in fomc_months:
        # Usually third Wednesday, 14:00 ET = 19:00 UTC
        third_wed = _get_nth_weekday(year, month, 2, 3)  # 2 = Wednesday
        events.append({
            "time": datetime(third_wed.year, third_wed.month, third_wed.day,
                             19, 0, tzinfo=tz),
            "currency": "USD",
            "importance": "HIGH",
            "event_name": "FOMC Interest Rate Decision",
            "source": "static",
        })

    # ── BOJ Interest Rate Decision ──
    # ~8 meetings per year
    boj_months = [1, 3, 4, 6, 7, 9, 10, 12]
    for month in boj_months:
        third_thurs = _get_nth_weekday(year, month, 3, 3)
        events.append({
            "time": datetime(third_thurs.year, third_thurs.month, third_thurs.day,
                             3, 0, tzinfo=tz),
            "currency": "JPY",
            "importance": "HIGH",
            "event_name": "BOJ Interest Rate Decision",
            "source": "static",
        })

    return events


# ======================================================================
# News Filter Class
# ======================================================================

class NewsFilter:
    """
    Manages economic news calendar and blackout windows.

    Caches events and refreshes periodically.
    Checks if a symbol is in a blackout window near high-impact news.
    """

    def __init__(self):
        self._cached_events: list[dict] = []
        self._last_fetch_time: Optional[datetime] = None
        self._static_schedule: list[dict] = []

        # Pre-generate static schedule for current and next year
        now = datetime.now(pytz.UTC)
        self._static_schedule = (
            _generate_static_schedule(now.year) +
            _generate_static_schedule(now.year + 1)
        )
        logger.info(
            f"[NEWS] Static schedule loaded: {len(self._static_schedule)} events "
            f"for {now.year}-{now.year + 1}"
        )

    def _should_refresh(self) -> bool:
        """Check if cache needs refreshing."""
        if self._last_fetch_time is None:
            return True
        elapsed = (datetime.now(pytz.UTC) - self._last_fetch_time).total_seconds()
        return elapsed > (getattr(config, "NEWS_CACHE_MINUTES", 30) * 60)

    def _fetch_mt5_events(self) -> list[dict]:
        """Fetch upcoming events from MT5 calendar."""
        now = datetime.now()
        # Fetch events for the next 24 hours
        from_date = now - timedelta(hours=1)
        to_date = now + timedelta(hours=24)

        all_events = []
        currencies = getattr(config, "NEWS_AFFECTED_CURRENCIES", ["USD"])
        min_importance = getattr(config, "NEWS_MIN_IMPORTANCE", "HIGH")

        importance_rank = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}
        min_rank = importance_rank.get(min_importance, 3)

        for currency in currencies:
            events = mt5_bridge.get_calendar_events(from_date, to_date, currency)
            for event in events:
                event_rank = importance_rank.get(event.get("importance", "NONE"), 0)
                if event_rank >= min_rank:
                    event["source"] = "mt5"
                    all_events.append(event)

        return all_events

    def _get_upcoming_events(self) -> list[dict]:
        """
        Get combined list of upcoming high-impact events.
        Uses MT5 calendar as primary, with static schedule as fallback.
        """
        if not getattr(config, "ENABLE_NEWS_FILTER", True):
            return []

        if not self._should_refresh():
            return self._cached_events

        # Try MT5 calendar first
        mt5_events = self._fetch_mt5_events()

        now = datetime.now(pytz.UTC)
        window_start = now - timedelta(hours=1)
        window_end = now + timedelta(hours=24)

        if mt5_events:
            self._cached_events = mt5_events
            logger.info(f"[NEWS] Refreshed from MT5 calendar: {len(mt5_events)} events")
        else:
            # Fallback to static schedule
            upcoming_static = []
            for event in self._static_schedule:
                event_time = event["time"]
                if window_start <= event_time <= window_end:
                    upcoming_static.append(event)

            self._cached_events = upcoming_static
            if upcoming_static:
                logger.info(
                    f"[NEWS] MT5 calendar unavailable, using static fallback: "
                    f"{len(upcoming_static)} events"
                )

        self._last_fetch_time = now
        return self._cached_events

    def is_news_blackout(self, symbol: str) -> tuple[bool, str]:
        """
        Check if the given symbol is in a news blackout window.

        Args:
            symbol: Trading symbol (e.g., "EURUSDx")

        Returns:
            (True, "NFP in 12 min") if blackout active
            (False, "") if clear to trade
        """
        if not getattr(config, "ENABLE_NEWS_FILTER", True):
            return False, ""

        events = self._get_upcoming_events()
        if not events:
            return False, ""

        now = datetime.now(pytz.UTC)
        blackout_before = timedelta(minutes=getattr(config, "NEWS_BLACKOUT_MINUTES_BEFORE", 15))
        blackout_after = timedelta(minutes=getattr(config, "NEWS_BLACKOUT_MINUTES_AFTER", 10))

        # Get currencies that affect this symbol
        symbol_currencies = _extract_currencies_from_symbol(symbol)

        for event in events:
            # Check if this event's currency affects our symbol
            event_currency = event.get("currency", "")
            if event_currency not in symbol_currencies:
                continue

            event_time = event["time"]

            # Ensure timezone-aware comparison
            if event_time.tzinfo is None:
                event_time = pytz.UTC.localize(event_time)

            # Calculate time distance
            time_diff = event_time - now
            total_minutes = time_diff.total_seconds() / 60

            # Check if we're in the blackout window
            # Before event: -blackout_before <= diff <= 0
            # After event: 0 <= diff <= blackout_after (negative total_minutes)
            minutes_before = blackout_before.total_seconds() / 60
            minutes_after = blackout_after.total_seconds() / 60

            if -minutes_after <= total_minutes <= minutes_before:
                event_name = event.get("event_name", "Unknown Event")

                if total_minutes > 0:
                    reason = f"{event_name} ({event_currency}) in {total_minutes:.0f} min"
                elif total_minutes > -1:
                    reason = f"{event_name} ({event_currency}) happening NOW"
                else:
                    reason = f"{event_name} ({event_currency}) released {abs(total_minutes):.0f} min ago"

                logger.info(
                    f"[NEWS] ⚠️ BLACKOUT for {symbol}: {reason} "
                    f"(window: -{minutes_before:.0f}m to +{minutes_after:.0f}m)"
                )
                return True, reason

        return False, ""

    def log_upcoming_events(self):
        """Log all upcoming events for the next few hours."""
        events = self._get_upcoming_events()
        if not events:
            logger.info("[NEWS] No high-impact events in the next 24h")
            return

        now = datetime.now(pytz.UTC)
        tz_local = pytz.timezone(getattr(config, "TIMEZONE", "Asia/Jakarta"))

        # Sort by time
        sorted_events = sorted(events, key=lambda e: e.get("time", now))

        logger.info(f"\n{'='*55}")
        logger.info(f"📰 UPCOMING HIGH-IMPACT NEWS ({len(sorted_events)} events)")
        logger.info(f"{'='*55}")

        for event in sorted_events[:15]:  # Show max 15
            event_time = event["time"]
            if event_time.tzinfo is None:
                event_time = pytz.UTC.localize(event_time)

            local_time = event_time.astimezone(tz_local)
            time_diff = (event_time - now).total_seconds() / 60

            # Format time distance
            if time_diff > 0:
                if time_diff > 60:
                    dist_str = f"in {time_diff / 60:.1f}h"
                else:
                    dist_str = f"in {time_diff:.0f}m"
            else:
                dist_str = f"{abs(time_diff):.0f}m ago"

            source_emoji = "🌐" if event.get("source") == "mt5" else "📋"

            logger.info(
                f"  {source_emoji} {local_time.strftime('%H:%M WIB')} "
                f"[{event.get('currency', '???')}] "
                f"{event.get('event_name', 'Unknown')} "
                f"({dist_str})"
            )

        logger.info(f"{'='*55}")

    def get_next_event_for_symbol(self, symbol: str) -> Optional[dict]:
        """Get the next upcoming event that affects a given symbol."""
        events = self._get_upcoming_events()
        now = datetime.now(pytz.UTC)
        symbol_currencies = _extract_currencies_from_symbol(symbol)

        for event in sorted(events, key=lambda e: e.get("time", now)):
            event_time = event["time"]
            if event_time.tzinfo is None:
                event_time = pytz.UTC.localize(event_time)

            if event_time > now and event.get("currency", "") in symbol_currencies:
                return event

        return None


# ======================================================================
# Module-level singleton (import once, use everywhere)
# ======================================================================

news_filter = NewsFilter()
