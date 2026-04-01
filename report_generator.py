"""
Forex Liquidity Hunter — PDF Report Generator
================================================
Generates professional PDF trade reports (daily, weekly, monthly).
Reports include full trade history tables, per-pair breakdown,
and comprehensive statistics.

Uses fpdf2 for PDF generation.
"""
import logging
import os
from datetime import datetime, date, timedelta

import config

logger = logging.getLogger(__name__)

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False
    logger.warning("fpdf2 not installed. PDF reports disabled. Install with: pip install fpdf2")


# ===========================================================================
# Color palette
# ===========================================================================
COLOR_PRIMARY = (30, 58, 95)       # Dark navy
COLOR_SECONDARY = (52, 95, 148)    # Medium blue
COLOR_ACCENT = (41, 128, 185)      # Bright blue
COLOR_WIN = (39, 174, 96)          # Green
COLOR_LOSS = (192, 57, 43)         # Red
COLOR_HEADER_BG = (44, 62, 80)     # Dark header
COLOR_ROW_ALT = (236, 240, 245)    # Light gray alternate
COLOR_WHITE = (255, 255, 255)
COLOR_TEXT = (44, 62, 80)
COLOR_LIGHT_TEXT = (127, 140, 141)


class TradePDF(FPDF if FPDF_AVAILABLE else object):
    """Custom PDF class for trade reports."""

    def __init__(self, report_title: str, report_subtitle: str, mode: str = "DRY RUN"):
        if not FPDF_AVAILABLE:
            return
        super().__init__()
        self.report_title = report_title
        self.report_subtitle = report_subtitle
        self.mode = mode
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        # Top gradient bar
        self.set_fill_color(*COLOR_PRIMARY)
        self.rect(0, 0, 210, 35, 'F')
        self.set_fill_color(*COLOR_SECONDARY)
        self.rect(0, 35, 210, 3, 'F')

        # Title
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*COLOR_WHITE)
        self.set_y(8)
        self.cell(0, 8, "FOREX LIQUIDITY HUNTER", align="C", new_x="LMARGIN", new_y="NEXT")

        # Subtitle
        self.set_font("Helvetica", "", 10)
        self.set_text_color(180, 200, 220)
        self.cell(0, 6, self.report_subtitle, align="C", new_x="LMARGIN", new_y="NEXT")

        # Mode badge
        self.set_font("Helvetica", "B", 8)
        mode_text = f"  {self.mode}  "
        if "DRY" in self.mode:
            self.set_fill_color(243, 156, 18)  # Orange
        else:
            self.set_fill_color(*COLOR_WIN)
        self.set_text_color(*COLOR_WHITE)
        w = self.get_string_width(mode_text) + 6
        self.set_x((210 - w) / 2)
        self.cell(w, 5, mode_text, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*COLOR_LIGHT_TEXT)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        """Add a section title with colored bar."""
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*COLOR_PRIMARY)
        self.set_fill_color(*COLOR_ACCENT)
        self.rect(10, self.get_y(), 3, 8, 'F')
        self.set_x(16)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def summary_box(self, stats: dict):
        """Draw a summary statistics box."""
        y_start = self.get_y()

        # Background
        self.set_fill_color(245, 248, 252)
        self.rect(10, y_start, 190, 40, 'F')

        # Border
        self.set_draw_color(*COLOR_ACCENT)
        self.set_line_width(0.5)
        self.rect(10, y_start, 190, 40)

        # Stats in 2 rows
        col_width = 47.5
        self.set_y(y_start + 4)

        # Row 1
        self._stat_cell(12, "Total Trades", str(stats.get("total_trades", 0)), col_width)
        self._stat_cell(12 + col_width, "Wins", str(stats.get("wins", 0)), col_width, COLOR_WIN)
        self._stat_cell(12 + col_width * 2, "Losses", str(stats.get("losses", 0)), col_width, COLOR_LOSS)
        self._stat_cell(12 + col_width * 3, "Win Rate", f"{stats.get('win_rate', 0)}%", col_width)

        self.set_y(y_start + 22)

        # Row 2
        pnl = stats.get("total_pnl", 0)
        pnl_color = COLOR_WIN if pnl >= 0 else COLOR_LOSS
        self._stat_cell(12, "Net P/L", f"${pnl:+.2f}", col_width, pnl_color)
        self._stat_cell(12 + col_width, "Avg P/L", f"${stats.get('avg_pnl_per_trade', 0):+.2f}", col_width)
        self._stat_cell(12 + col_width * 2, "Max DD", f"${stats.get('max_drawdown', 0):.2f}", col_width, COLOR_LOSS)

        pf = stats.get("profit_factor", 0)
        pf_str = str(pf) if isinstance(pf, str) else f"{pf:.2f}"
        self._stat_cell(12 + col_width * 3, "Profit Factor", pf_str, col_width)

        self.set_y(y_start + 45)

    def _stat_cell(self, x: float, label: str, value: str, width: float, value_color=None):
        """Draw a single stat inside the summary box."""
        self.set_x(x)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*COLOR_LIGHT_TEXT)
        self.cell(width, 4, label.upper(), new_x="LEFT", new_y="NEXT")

        self.set_x(x)
        self.set_font("Helvetica", "B", 13)
        if value_color:
            self.set_text_color(*value_color)
        else:
            self.set_text_color(*COLOR_TEXT)
        self.cell(width, 8, value)

    def trade_history_table(self, trades: list, title: str = "TRADE HISTORY"):
        """Draw the full trade history table."""
        self.section_title(title)

        if not trades:
            self.set_font("Helvetica", "I", 10)
            self.set_text_color(*COLOR_LIGHT_TEXT)
            self.cell(0, 8, "No trades in this period.", new_x="LMARGIN", new_y="NEXT")
            self.ln(4)
            return

        # Column definitions: (header, width, align)
        columns = [
            ("#", 8, "C"),
            ("Time Open", 28, "C"),
            ("Time Close", 28, "C"),
            ("Symbol", 20, "C"),
            ("Dir", 10, "C"),
            ("Entry", 22, "C"),
            ("Exit", 22, "C"),
            ("SL", 13, "C"),
            ("TP", 13, "C"),
            ("Lot", 12, "C"),
            ("Result", 12, "C"),
            ("P/L", 18, "C"),
        ]

        # Header row
        self.set_font("Helvetica", "B", 6)
        self.set_fill_color(*COLOR_HEADER_BG)
        self.set_text_color(*COLOR_WHITE)
        self.set_draw_color(*COLOR_HEADER_BG)

        x_start = (210 - sum(c[1] for c in columns)) / 2
        self.set_x(x_start)

        for header, width, align in columns:
            self.cell(width, 6, header, border=1, fill=True, align=align)
        self.ln()

        # Data rows
        self.set_font("Helvetica", "", 6)
        self.set_draw_color(200, 200, 200)

        for i, trade in enumerate(trades):
            # Check if we need a new page
            if self.get_y() > 265:
                self.add_page()
                # Re-draw header
                self.set_font("Helvetica", "B", 6)
                self.set_fill_color(*COLOR_HEADER_BG)
                self.set_text_color(*COLOR_WHITE)
                self.set_draw_color(*COLOR_HEADER_BG)
                self.set_x(x_start)
                for header, width, align in columns:
                    self.cell(width, 6, header, border=1, fill=True, align=align)
                self.ln()
                self.set_font("Helvetica", "", 6)
                self.set_draw_color(200, 200, 200)

            # Alternate row color
            if i % 2 == 0:
                self.set_fill_color(*COLOR_ROW_ALT)
            else:
                self.set_fill_color(*COLOR_WHITE)

            self.set_text_color(*COLOR_TEXT)
            self.set_x(x_start)

            # Parse times
            open_time = self._format_time(trade.open_time)
            close_time = self._format_time(trade.close_time)

            # Format prices
            digits = 5
            if "XAU" in trade.symbol or "GOLD" in trade.symbol:
                digits = 2
            elif "JPY" in trade.symbol:
                digits = 3

            entry_str = f"{trade.entry_price:.{digits}f}"
            exit_str = f"{trade.close_price:.{digits}f}" if trade.is_closed else "OPEN"
            sl_pips = abs(trade.entry_price - trade.stop_loss) / trade.pip_size if trade.pip_size > 0 else 0
            tp_pips = abs(trade.take_profit - trade.entry_price) / trade.pip_size if trade.pip_size > 0 else 0

            # Direction emoji
            dir_text = trade.direction

            # Result
            result = trade.close_reason if trade.is_closed else "OPEN"

            # P/L with color
            pnl_str = f"${trade.pnl:+.2f}" if trade.is_closed else "-"

            row_data = [
                (str(i + 1), 8),
                (open_time, 28),
                (close_time, 28),
                (trade.symbol, 20),
                (dir_text, 10),
                (entry_str, 22),
                (exit_str, 22),
                (f"{sl_pips:.1f}", 13),
                (f"{tp_pips:.1f}", 13),
                (str(trade.lot_size), 12),
                (result, 12),
                (pnl_str, 18),
            ]

            for text, width in row_data:
                # Color P/L cell
                if text == pnl_str and trade.is_closed:
                    if trade.pnl >= 0:
                        self.set_text_color(*COLOR_WIN)
                    else:
                        self.set_text_color(*COLOR_LOSS)
                    self.set_font("Helvetica", "B", 6)
                    self.cell(width, 5, text, border=1, fill=True, align="C")
                    self.set_font("Helvetica", "", 6)
                    self.set_text_color(*COLOR_TEXT)
                # Color Result cell
                elif text in ("SL", "TP"):
                    if text == "TP":
                        self.set_text_color(*COLOR_WIN)
                    else:
                        self.set_text_color(*COLOR_LOSS)
                    self.set_font("Helvetica", "B", 6)
                    self.cell(width, 5, text, border=1, fill=True, align="C")
                    self.set_font("Helvetica", "", 6)
                    self.set_text_color(*COLOR_TEXT)
                else:
                    self.cell(width, 5, text, border=1, fill=True, align="C")

            self.ln()

        # Total row
        self.set_x(x_start)
        self.set_font("Helvetica", "B", 6)
        self.set_fill_color(*COLOR_PRIMARY)
        self.set_text_color(*COLOR_WHITE)
        self.set_draw_color(*COLOR_PRIMARY)

        total_pnl = sum(t.pnl for t in trades if t.is_closed)
        total_width = sum(c[1] for c in columns) - 18  # all except P/L
        self.cell(total_width, 6, f"TOTAL ({len(trades)} trades)", border=1, fill=True, align="R")

        if total_pnl >= 0:
            self.set_fill_color(*COLOR_WIN)
        else:
            self.set_fill_color(*COLOR_LOSS)
        self.cell(18, 6, f"${total_pnl:+.2f}", border=1, fill=True, align="C")
        self.ln(8)

    def pair_performance_table(self, pair_stats: dict):
        """Draw per-pair performance breakdown."""
        self.section_title("PER-PAIR PERFORMANCE")

        if not pair_stats:
            self.set_font("Helvetica", "I", 10)
            self.set_text_color(*COLOR_LIGHT_TEXT)
            self.cell(0, 8, "No data.", new_x="LMARGIN", new_y="NEXT")
            return

        columns = [
            ("Pair", 35, "C"),
            ("Trades", 20, "C"),
            ("Wins", 20, "C"),
            ("Losses", 20, "C"),
            ("Win Rate", 25, "C"),
            ("Net P/L", 30, "C"),
            ("Avg P/L", 30, "C"),
        ]

        total_width = sum(c[1] for c in columns)
        x_start = (210 - total_width) / 2

        # Header
        self.set_font("Helvetica", "B", 7)
        self.set_fill_color(*COLOR_HEADER_BG)
        self.set_text_color(*COLOR_WHITE)
        self.set_draw_color(*COLOR_HEADER_BG)
        self.set_x(x_start)

        for header, width, align in columns:
            self.cell(width, 6, header, border=1, fill=True, align=align)
        self.ln()

        # Data
        self.set_font("Helvetica", "", 7)
        self.set_draw_color(200, 200, 200)

        sorted_pairs = sorted(pair_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)

        for i, (pair, stats) in enumerate(sorted_pairs):
            if i % 2 == 0:
                self.set_fill_color(*COLOR_ROW_ALT)
            else:
                self.set_fill_color(*COLOR_WHITE)

            self.set_text_color(*COLOR_TEXT)
            self.set_x(x_start)

            trades = stats["trades"]
            wins = stats["wins"]
            losses = stats["losses"]
            pnl = stats["pnl"]
            win_rate = (wins / trades * 100) if trades > 0 else 0
            avg_pnl = pnl / trades if trades > 0 else 0

            self.cell(35, 5, pair, border=1, fill=True, align="C")
            self.cell(20, 5, str(trades), border=1, fill=True, align="C")

            self.set_text_color(*COLOR_WIN)
            self.cell(20, 5, str(wins), border=1, fill=True, align="C")

            self.set_text_color(*COLOR_LOSS)
            self.cell(20, 5, str(losses), border=1, fill=True, align="C")

            self.set_text_color(*COLOR_TEXT)
            self.cell(25, 5, f"{win_rate:.1f}%", border=1, fill=True, align="C")

            self.set_text_color(*(COLOR_WIN if pnl >= 0 else COLOR_LOSS))
            self.set_font("Helvetica", "B", 7)
            self.cell(30, 5, f"${pnl:+.2f}", border=1, fill=True, align="C")

            self.set_text_color(*(COLOR_WIN if avg_pnl >= 0 else COLOR_LOSS))
            self.cell(30, 5, f"${avg_pnl:+.2f}", border=1, fill=True, align="C")

            self.set_font("Helvetica", "", 7)
            self.ln()

        self.ln(6)

    def additional_stats(self, stats: dict):
        """Draw additional statistics section."""
        self.section_title("DETAILED STATISTICS")

        items = [
            ("SL Hits", str(stats.get("sl_count", 0))),
            ("TP Hits", str(stats.get("tp_count", 0))),
            ("Avg RR Achieved", f"{stats.get('avg_rr_achieved', 0):.2f}"),
            ("Max Drawdown", f"${stats.get('max_drawdown', 0):.2f}"),
            ("Profit Factor", str(stats.get("profit_factor", "-"))),
            ("Longest Win Streak", str(stats.get("longest_win_streak", 0))),
            ("Longest Loss Streak", str(stats.get("longest_loss_streak", 0))),
        ]

        col_width = 60
        x_start = 15
        self.set_draw_color(220, 220, 220)

        for i, (label, value) in enumerate(items):
            col = i % 3
            if col == 0 and i > 0:
                self.ln()

            x = x_start + col * col_width
            self.set_x(x)

            self.set_font("Helvetica", "", 7)
            self.set_text_color(*COLOR_LIGHT_TEXT)
            self.cell(30, 5, label.upper())

            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*COLOR_TEXT)
            self.cell(25, 5, value)

        self.ln(10)

    def daily_breakdown_table(self, daily_data: list[dict]):
        """Draw per-day breakdown (for weekly/monthly reports)."""
        self.section_title("DAILY BREAKDOWN")

        if not daily_data:
            self.set_font("Helvetica", "I", 10)
            self.set_text_color(*COLOR_LIGHT_TEXT)
            self.cell(0, 8, "No data.", new_x="LMARGIN", new_y="NEXT")
            return

        columns = [
            ("Date", 35, "C"),
            ("Trades", 20, "C"),
            ("W/L", 25, "C"),
            ("Win Rate", 25, "C"),
            ("P/L", 30, "C"),
            ("Cumulative", 30, "C"),
        ]

        total_width = sum(c[1] for c in columns)
        x_start = (210 - total_width) / 2

        # Header
        self.set_font("Helvetica", "B", 7)
        self.set_fill_color(*COLOR_HEADER_BG)
        self.set_text_color(*COLOR_WHITE)
        self.set_draw_color(*COLOR_HEADER_BG)
        self.set_x(x_start)
        for header, width, align in columns:
            self.cell(width, 6, header, border=1, fill=True, align=align)
        self.ln()

        # Data
        self.set_font("Helvetica", "", 7)
        self.set_draw_color(200, 200, 200)
        cumulative = 0.0

        for i, day in enumerate(daily_data):
            if i % 2 == 0:
                self.set_fill_color(*COLOR_ROW_ALT)
            else:
                self.set_fill_color(*COLOR_WHITE)

            cumulative += day.get("pnl", 0)
            trades = day.get("trades", 0)
            wins = day.get("wins", 0)
            losses = day.get("losses", 0)
            pnl = day.get("pnl", 0)
            win_rate = (wins / trades * 100) if trades > 0 else 0

            self.set_text_color(*COLOR_TEXT)
            self.set_x(x_start)
            self.cell(35, 5, day.get("date", ""), border=1, fill=True, align="C")
            self.cell(20, 5, str(trades), border=1, fill=True, align="C")
            self.cell(25, 5, f"{wins}/{losses}", border=1, fill=True, align="C")
            self.cell(25, 5, f"{win_rate:.0f}%", border=1, fill=True, align="C")

            self.set_text_color(*(COLOR_WIN if pnl >= 0 else COLOR_LOSS))
            self.set_font("Helvetica", "B", 7)
            self.cell(30, 5, f"${pnl:+.2f}", border=1, fill=True, align="C")

            self.set_text_color(*(COLOR_WIN if cumulative >= 0 else COLOR_LOSS))
            self.cell(30, 5, f"${cumulative:+.2f}", border=1, fill=True, align="C")

            self.set_font("Helvetica", "", 7)
            self.ln()

        self.ln(6)

    def progress_section(self, cumulative_pnl: float, target: float):
        """Draw progress towards profit target."""
        self.section_title("PROGRESS TO TARGET")

        progress_pct = (cumulative_pnl / target * 100) if target > 0 else 0
        progress_pct = min(progress_pct, 100)

        # Progress bar
        bar_width = 170
        bar_height = 12
        x_start = 20
        y = self.get_y()

        # Background
        self.set_fill_color(220, 225, 230)
        self.rect(x_start, y, bar_width, bar_height, 'F')

        # Fill
        fill_width = max(0, bar_width * progress_pct / 100)
        if progress_pct >= 100:
            self.set_fill_color(*COLOR_WIN)
        elif progress_pct >= 50:
            self.set_fill_color(*COLOR_ACCENT)
        else:
            self.set_fill_color(243, 156, 18)  # Orange
        if fill_width > 0:
            self.rect(x_start, y, fill_width, bar_height, 'F')

        # Text on bar
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*COLOR_WHITE)
        self.set_xy(x_start, y + 2)
        self.cell(bar_width, 8,
                  f"${cumulative_pnl:+.2f} / ${target:.2f} ({progress_pct:.1f}%)",
                  align="C")

        self.set_y(y + bar_height + 8)

    @staticmethod
    def _format_time(time_str: str) -> str:
        """Format ISO time string to compact display."""
        if not time_str:
            return "-"
        try:
            dt = datetime.fromisoformat(time_str)
            return dt.strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            return time_str[:16] if len(time_str) > 16 else time_str


# ===========================================================================
# Report Generation Functions
# ===========================================================================

def _get_daily_breakdown(trades: list, start_date: date, end_date: date) -> list[dict]:
    """Calculate per-day breakdown from a list of trades."""
    daily = {}
    current = start_date
    while current <= end_date:
        daily[str(current)] = {"date": str(current), "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        current += timedelta(days=1)

    for t in trades:
        if not t.close_time:
            continue
        try:
            close_date = str(datetime.fromisoformat(t.close_time).date())
        except (ValueError, TypeError):
            continue
        if close_date in daily:
            daily[close_date]["trades"] += 1
            daily[close_date]["pnl"] += t.pnl
            if t.pnl >= 0:
                daily[close_date]["wins"] += 1
            else:
                daily[close_date]["losses"] += 1

    return [v for v in daily.values() if v["trades"] > 0]


def generate_daily_report(
    trades: list,
    stats: dict,
    report_date: date,
    cumulative_pnl: float = 0.0,
) -> str:
    """
    Generate a daily PDF report.
    Returns the file path of the generated PDF.
    """
    if not FPDF_AVAILABLE:
        logger.error("fpdf2 not installed. Cannot generate PDF report.")
        return ""

    reports_dir = getattr(config, "REPORTS_DIR", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    filename = f"daily_{report_date.isoformat()}.pdf"
    filepath = os.path.join(reports_dir, filename)

    mode = "DRY RUN" if config.DRY_RUN else "LIVE"
    pdf = TradePDF(
        report_title="DAILY TRADE REPORT",
        report_subtitle=f"Daily Report - {report_date.strftime('%A, %B %d, %Y')}",
        mode=mode,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    # Summary box
    pdf.section_title("SUMMARY")
    pdf.summary_box(stats)

    # Full trade history
    pdf.trade_history_table(trades, "TRADE HISTORY")

    # Per-pair performance
    pdf.pair_performance_table(stats.get("pair_stats", {}))

    # Detailed stats
    pdf.additional_stats(stats)

    # Progress
    pdf.progress_section(cumulative_pnl, config.PROFIT_TARGET)

    pdf.output(filepath)
    logger.info(f"[REPORT] Daily PDF generated: {filepath}")
    return filepath


def generate_weekly_report(
    trades: list,
    stats: dict,
    week_start: date,
    week_end: date,
    cumulative_pnl: float = 0.0,
) -> str:
    """
    Generate a weekly PDF report.
    Returns the file path of the generated PDF.
    """
    if not FPDF_AVAILABLE:
        logger.error("fpdf2 not installed. Cannot generate PDF report.")
        return ""

    reports_dir = getattr(config, "REPORTS_DIR", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    iso_year, iso_week, _ = week_start.isocalendar()
    filename = f"weekly_{iso_year}-W{iso_week:02d}.pdf"
    filepath = os.path.join(reports_dir, filename)

    mode = "DRY RUN" if config.DRY_RUN else "LIVE"
    pdf = TradePDF(
        report_title="WEEKLY TRADE REPORT",
        report_subtitle=(
            f"Week {iso_week}, {iso_year} "
            f"({week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')})"
        ),
        mode=mode,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    # Summary
    pdf.section_title("WEEKLY SUMMARY")
    pdf.summary_box(stats)

    # Daily breakdown
    daily_data = _get_daily_breakdown(trades, week_start, week_end)
    pdf.daily_breakdown_table(daily_data)

    # Full trade history
    pdf.trade_history_table(trades, "FULL TRADE HISTORY")

    # Per-pair performance
    pdf.pair_performance_table(stats.get("pair_stats", {}))

    # Stats
    pdf.additional_stats(stats)

    # Top/bottom trades
    if trades:
        sorted_by_pnl = sorted([t for t in trades if t.is_closed], key=lambda t: t.pnl, reverse=True)
        top3 = sorted_by_pnl[:3] if len(sorted_by_pnl) >= 3 else sorted_by_pnl
        bottom3 = sorted_by_pnl[-3:] if len(sorted_by_pnl) >= 3 else sorted_by_pnl
        bottom3.reverse()

        if top3:
            pdf.trade_history_table(top3, "TOP 3 BEST TRADES")
        if bottom3 and bottom3 != top3:
            pdf.trade_history_table(bottom3, "TOP 3 WORST TRADES")

    # Progress
    pdf.progress_section(cumulative_pnl, config.PROFIT_TARGET)

    pdf.output(filepath)
    logger.info(f"[REPORT] Weekly PDF generated: {filepath}")
    return filepath


def generate_monthly_report(
    trades: list,
    stats: dict,
    report_month: int,
    report_year: int,
    cumulative_pnl: float = 0.0,
) -> str:
    """
    Generate a monthly PDF report.
    Returns the file path of the generated PDF.
    """
    if not FPDF_AVAILABLE:
        logger.error("fpdf2 not installed. Cannot generate PDF report.")
        return ""

    reports_dir = getattr(config, "REPORTS_DIR", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    filename = f"monthly_{report_year}-{report_month:02d}.pdf"
    filepath = os.path.join(reports_dir, filename)

    month_name = date(report_year, report_month, 1).strftime("%B %Y")
    mode = "DRY RUN" if config.DRY_RUN else "LIVE"

    pdf = TradePDF(
        report_title="MONTHLY TRADE REPORT",
        report_subtitle=f"Monthly Report - {month_name}",
        mode=mode,
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    # Summary
    pdf.section_title("MONTHLY SUMMARY")
    pdf.summary_box(stats)

    # Weekly breakdown (group by week)
    month_start = date(report_year, report_month, 1)
    if report_month == 12:
        month_end = date(report_year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(report_year, report_month + 1, 1) - timedelta(days=1)

    # Per-day breakdown
    daily_data = _get_daily_breakdown(trades, month_start, month_end)
    pdf.daily_breakdown_table(daily_data)

    # Full trade history
    pdf.trade_history_table(trades, "FULL TRADE HISTORY")

    # Per-pair performance
    pdf.pair_performance_table(stats.get("pair_stats", {}))

    # Stats
    pdf.additional_stats(stats)

    # Consistency check section
    pdf.section_title("CONSISTENCY CHECK")
    total_pnl = stats.get("total_pnl", 0)
    if daily_data and total_pnl > 0:
        max_day_pnl = max(d["pnl"] for d in daily_data)
        max_day_pct = (max_day_pnl / total_pnl * 100) if total_pnl > 0 else 0
        pdf.set_font("Helvetica", "", 9)

        if max_day_pct <= 30:
            pdf.set_text_color(*COLOR_WIN)
            pdf.cell(0, 6, f"PASS - Max single-day profit: {max_day_pct:.1f}% of total (limit: 30%)",
                     new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_text_color(*COLOR_LOSS)
            pdf.cell(0, 6, f"FAIL - Max single-day profit: {max_day_pct:.1f}% of total (limit: 30%)",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # Progress
    pdf.progress_section(cumulative_pnl, config.PROFIT_TARGET)

    pdf.output(filepath)
    logger.info(f"[REPORT] Monthly PDF generated: {filepath}")
    return filepath
