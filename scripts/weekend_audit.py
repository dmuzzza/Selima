"""Weekend-bar audit: trade groups, microstructure, schedule, gaps (T2-23, ADR-031).

Answers the data-quality question only: it never changes a trade set or a PnL.
The decision rule and its thresholds are frozen in `config/validation.json` and
ADR-031 before the run, so the audit reports which branch fires instead of choosing
one after seeing the number.

    uv run python scripts/weekend_audit.py --tag t2-23
    uv run python scripts/weekend_audit.py --tag t2-23 --no-schedule   # offline

Candles come from PostgreSQL through `HistoryManager`; the runner is
`build_runner` from `scripts/portfolio_run.py` and the pool is
`portfolio.pool_trades`, so this script holds no copy of the backtest logic.
The broker schedule is read with `InstrumentsService.trading_schedules`: read-only,
no orders, and the token is never printed or logged.

Outputs go to debug/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402
from portfolio_run import build_runner, load_instruments  # noqa: E402
from verdict import doubled_costs  # noqa: E402

from src.backtest.costs import load_costs  # noqa: E402
from src.backtest.portfolio import (  # noqa: E402
    gap_series,
    load_multi_instrument_thresholds,
    load_portfolio,
    pool_trades,
)
from src.backtest.weekend import (  # noqa: E402
    CANDLES_WITHOUT_TRADING_DAY,
    TRADING_DAY_WITHOUT_CANDLES,
    BarStats,
    BreakGap,
    ScheduleDay,
    ScheduleMismatch,
    TickerMicrostructure,
    TradeAudit,
    WeekendThresholds,
    audit_trades,
    bar_calendar,
    bar_stats,
    is_weekend_bar,
    load_weekend_thresholds,
    schedule_mismatches,
    session_structure,
    source_marker_counts,
    structural_rule,
    ticker_microstructure,
    weekend_breaks,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.models import Candle  # noqa: E402
from src.market_data.preparation import prepare_candles  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402

logger = logging.getLogger("weekend_audit")

DEFAULT_OUT_DIR = ROOT / "debug"
MSK = ZoneInfo("Europe/Moscow")
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
PORTFOLIO_PATH = ROOT / "config" / "portfolio.json"
THRESHOLDS_PATH = ROOT / "config" / "validation.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekend-bar audit (T2-23)")
    parser.add_argument("--tag", default="current", help="report name")
    parser.add_argument("--portfolio-file", default=str(PORTFOLIO_PATH))
    parser.add_argument("--thresholds-file", default=str(THRESHOLDS_PATH))
    parser.add_argument(
        "--costs-file", default=str(ROOT / "config" / "costs.json"), help="per-instrument costs"
    )
    parser.add_argument("--limit", type=int, help="first N tickers of the set, for a smoke run")
    parser.add_argument(
        "--exchange",
        default="MOEX,MOEX_STCKS,MOEX_WEEKEND,MOEX_EXTENDED,MOEX_CLOSE",
        help="comma-separated exchange calendars; one name is one schedule",
    )
    parser.add_argument(
        "--schedule-days",
        type=int,
        default=14,
        help="forward window for trading_schedules; the API caps it at 14 days",
    )
    parser.add_argument(
        "--no-schedule",
        action="store_true",
        help="skip the broker schedule call (offline run, candles only)",
    )
    parser.add_argument(
        "--max-breaks",
        type=int,
        default=15,
        help="how many weekend breaks to print, largest absolute total first",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


# ── data collection ────────────────────────────────────────────────────────


def read_candles(
    history: HistoryManager,
    instruments: dict[str, dict],
    tickers: list[str],
    timeframe: Timeframe,
) -> dict[str, list[Candle]]:
    """Candles per ticker from PostgreSQL; a ticker without data is skipped."""
    candles: dict[str, list[Candle]] = {}
    for ticker in tickers:
        series = history.get_candles(instruments[ticker]["figi"], timeframe)
        if len(series) < 2:
            logger.warning("no candles for %s, skipped", ticker)
            continue
        candles[ticker] = series
    return candles


def fetch_schedules(
    exchanges: list[str], days_ahead: int
) -> dict[str, list[ScheduleDay]]:
    """Trading schedules for the next days, one entry per exchange name.

    Read-only, no orders, and the token is never printed or logged.

    API constraints, confirmed by the reply and `sdk_t_bank_docs/errors/api_errors.json`:

    - 30003 `from` can't be less than the current date - the method is
      forward-looking, so the stored history cannot be validated with it;
    - 30002 the period must not exceed 14 days.

    That is why the historical marker test uses `candle_source` instead, and this
    call answers a different question: can live execute weekend signals from now on.
    Several exchange names are asked because one name is one calendar: `MOEX` is the
    main session, `MOEX_STCKS` carries the stock weekend session. Guessing a single
    name would decide the answer by the parameter.
    """
    from t_tech.invest import Client

    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise RuntimeError("TINKOFF_API_TOKEN is not set (environment or .env)")

    today = datetime.now(UTC).date()
    start_dt = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time(), tzinfo=UTC)
    result: dict[str, list[ScheduleDay]] = {}
    with Client(token) as client:
        for exchange in exchanges:
            response = client.instruments.trading_schedules(
                exchange=exchange, from_=start_dt, to=end_dt
            )
            days: list[ScheduleDay] = []
            for schedule in response.exchanges:
                for item in schedule.days:
                    trading = bool(item.is_trading_day)
                    days.append(
                        ScheduleDay(
                            day=item.date.date(),
                            is_trading_day=trading,
                            # A non-trading day carries 00:00, which is "no session",
                            # not a midnight session.
                            start=item.start_time if trading else None,
                            end=item.end_time if trading else None,
                        )
                    )
            result[exchange] = sorted(days, key=lambda entry: entry.day)
    return result


def prepare_effect(candles: list[Candle]) -> dict:
    """How `prepare_candles` treats weekend bars: kept, dropped, lost to warmup.

    The rule (sort, drop `volume <= 0`, ADR-028) is shared with the backtest, so a
    dropped weekend bar would also disappear from a live run. The audit reports the
    counts instead of assuming the filter is neutral.
    """
    prepared = prepare_candles(candles, min_bars=1)
    kept = {candle.open_time for candle in prepared}
    weekend = [candle for candle in candles if is_weekend_bar(candle)]
    return {
        "bars_before": len(candles),
        "bars_after": len(prepared),
        "weekend_bars": len(weekend),
        "weekend_kept": sum(1 for candle in weekend if candle.open_time in kept),
        "weekend_dropped": sum(1 for candle in weekend if candle.open_time not in kept),
        "warmup_dropped_all": len(prepared) == 0,
    }


# ── report ─────────────────────────────────────────────────────────────────


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}"


def _group_row(group, scaled_bps: float | None = None) -> str:
    flag = " *" if group.below_floor else ""
    scaled = f"{_fmt(scaled_bps)}" if scaled_bps is not None else "—"
    return (
        f"| {group.name} | {group.trades}{flag} | {_fmt(group.net_rub)} | "
        f"{_fmt(group.net_bps)} | [{_fmt(group.ci_low)}; {_fmt(group.ci_high)}] | {scaled} |"
    )


def _msk(value: datetime | None) -> str:
    """Timestamp in Moscow time, so it can be compared with the stored candles."""
    if value is None:
        return "—"
    return value.astimezone(MSK).strftime("%Y-%m-%d %H:%M")


def render_report(
    *,
    tag: str,
    strategy: str,
    timeframe: str,
    tp_percent: float,
    sl_percent: float,
    thresholds: WeekendThresholds,
    audit: TradeAudit,
    scaled: TradeAudit,
    same_trade_set: bool,
    microstructure: list[TickerMicrostructure],
    overall_weekend: BarStats,
    overall_weekday: BarStats,
    sessions: dict[str, list],
    schedules: dict[str, list[ScheduleDay]],
    schedule_error: str | None,
    mismatches: list[ScheduleMismatch],
    source_counts: dict[tuple[bool, int | None], int],
    breaks: list[BreakGap],
    prepared: dict[str, dict],
    rule: str | None,
    max_breaks: int,
    last_candle_day: date,
) -> str:
    lines: list[str] = []
    lines.append(f"# Weekend-bar audit — {tag}")
    lines.append("")
    lines.append(f"- strategy: `{strategy}` {timeframe}, TP {tp_percent:g} / SL {sl_percent:g}")
    lines.append(f"- window start: `{thresholds.start.isoformat()}` (per ticker: its first weekend bar)")
    lines.append(
        f"- thresholds: `{thresholds.fingerprint}`, "
        f"min_sample_trades {thresholds.min_sample_trades}, "
        f"max_zero_volume_share {thresholds.max_zero_volume_share:g}, "
        f"block {thresholds.block_days}d, iterations {thresholds.bootstrap_iterations}"
    )
    lines.append("")
    lines.append("## 1. Trade groups (partition)")
    lines.append("")
    lines.append(
        "The five groups are mutually exclusive and sum to the pool, so they can be "
        "added up. `*` marks a group below `min_sample_trades`: its net does not decide "
        "anything (ADR-031, branch 1). The last column is the same group under costs "
        f"×{thresholds.cost_multiplier:g}: informational, because the weekend session is "
        "thinner than a weekday one while the cost model is the same."
    )
    lines.append("")
    lines.append("| Group | Trades | Net, RUB | Net, bps | CI95 | bps at ×2 costs |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(_group_row(audit.entry_only, scaled.entry_only.net_bps))
    lines.append(_group_row(audit.exit_only, scaled.exit_only.net_bps))
    lines.append(_group_row(audit.both, scaled.both.net_bps))
    lines.append(_group_row(audit.spans_only, scaled.spans_only.net_bps))
    lines.append(_group_row(audit.other, scaled.other.net_bps))
    lines.append("")
    lines.append(
        f"Partition check: {audit.partition_trades} of {audit.total} trades — "
        + ("sums to the pool." if audit.partition_trades == audit.total else "**does not sum**.")
    )
    lines.append("")
    lines.append(
        f"Totals behind the split: entry on a weekend bar **{audit.entry_weekend_trades}** "
        f"(entry only {audit.entry_only.trades} + both {audit.both.trades}), "
        f"exit on a weekend bar **{audit.exit_weekend_trades}** "
        f"(exit only {audit.exit_only.trades} + both {audit.both.trades})."
    )
    lines.append("")
    if same_trade_set:
        lines.append(
            "The ×2 run produced the same trade set (ticker, entry, exit), as ADR-012 "
            "requires: levels come from the raw open, costs change only the fill price."
        )
    else:
        lines.append(
            "**Warning:** the ×2 run produced a different trade set. Levels must come "
            "from the raw open (ADR-012); the ×2 column is not comparable until that "
            "is explained."
        )
    lines.append("")
    lines.append("## 2. Microstructure: weekend against weekday")
    lines.append("")
    lines.append(
        "Per ticker inside its own window, plus a pooled row. A ticker with a thin "
        "weekend session must not be averaged away by liquid ones, so both numbers "
        "are printed."
    )
    lines.append("")
    lines.append(
        "| Ticker | Window from | Weekend bars | Weekend zero-vol | Weekend volume | "
        "Weekend range, bps | Weekday zero-vol | Weekday volume | Weekday range, bps |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in microstructure:
        lines.append(
            f"| {item.ticker} | {item.window_start.isoformat() if item.window_start else '—'} | "
            f"{item.weekend.bars} | {item.weekend.zero_volume_share:.1%} | "
            f"{item.weekend.median_volume:,.0f} | {item.weekend.median_range_bps:.1f} | "
            f"{item.weekday.zero_volume_share:.1%} | {item.weekday.median_volume:,.0f} | "
            f"{item.weekday.median_range_bps:.1f} |"
        )
    lines.append(
        f"| **pooled** | — | {overall_weekend.bars} | {overall_weekend.zero_volume_share:.1%} | "
        f"{overall_weekend.median_volume:,.0f} | {overall_weekend.median_range_bps:.1f} | "
        f"{overall_weekday.zero_volume_share:.1%} | {overall_weekday.median_volume:,.0f} | "
        f"{overall_weekday.median_range_bps:.1f} |"
    )
    lines.append("")
    thin = [item.ticker for item in microstructure if item.weekend_unusable]
    lines.append(
        f"Tickers above `max_zero_volume_share` {thresholds.max_zero_volume_share:g}: "
        f"{', '.join(thin) if thin else 'none'}"
    )
    lines.append("")
    lines.append("## 3. Session structure")
    lines.append("")
    lines.append("Bars per session; a 4h weekend bar assumes a main session that may be shorter.")
    lines.append("")
    lines.append("| Ticker | Weekend sessions | Bars per weekend session | First open |")
    lines.append("|---|---|---|---|")
    for ticker, items in sessions.items():
        weekend = [item for item in items if item.weekend]
        if not weekend:
            lines.append(f"| {ticker} | 0 | — | — |")
            continue
        counts = Counter(item.bars for item in weekend)
        distribution = ", ".join(f"{bars}×{count}" for bars, count in sorted(counts.items()))
        first_open = Counter(item.first_open.strftime("%H:%M") for item in weekend).most_common(1)
        lines.append(
            f"| {ticker} | {len(weekend)} | {distribution} | {first_open[0][0]} |"
        )
    lines.append("")
    lines.append("## 4. `prepare_candles` and weekend bars")
    lines.append("")
    lines.append("| Ticker | Bars | Weekend bars | Weekend kept | Weekend dropped |")
    lines.append("|---|---|---|---|---|")
    for ticker, item in prepared.items():
        lines.append(
            f"| {ticker} | {item['bars_before']} | {item['weekend_bars']} | "
            f"{item['weekend_kept']} | {item['weekend_dropped']} |"
        )
    lines.append("")
    lines.append("## 5. Broker schedule (forward-looking)")
    lines.append("")
    lines.append(
        "`trading_schedules` rejects `from` earlier than today (API 30003) and a period "
        "over 14 days (30002), so it answers one question only: can live execute weekend "
        "signals from now on. The stored history cannot be validated with this method; "
        "section 6 tests the marker on the stored field instead."
    )
    lines.append("")
    lines.append(
        "One exchange name is one calendar, so several are asked. The answer that matters "
        "for stocks is the calendar whose weekend days are marked as trading."
    )
    lines.append("")
    if schedule_error:
        lines.append(f"Schedule unavailable: `{schedule_error}`. Candle sections stand on their own.")
    elif not schedules:
        lines.append("Schedule returned no days.")
    else:
        lines.append("| Exchange | Days | Weekend days | Weekend trading | Weekend session, MSK |")
        lines.append("|---|---|---|---|---|")
        for name, days in schedules.items():
            weekend_days = [entry for entry in days if entry.day.weekday() >= 5]
            trading = [entry for entry in weekend_days if entry.is_trading_day]
            window = "—"
            if trading and trading[0].start and trading[0].end:
                if trading[0].start == trading[0].end:
                    window = "whole day (00:00 → 00:00)"
                else:
                    window = f"{_msk(trading[0].start)} → {_msk(trading[0].end)}"
            lines.append(
                f"| {name} | {len(days)} | {len(weekend_days)} | {len(trading)} | {window} |"
            )
        primary = max(
            schedules.items(),
            key=lambda item: sum(
                1 for entry in item[1] if entry.day.weekday() >= 5 and entry.is_trading_day
            ),
        )[0]
        lines.append("")
        lines.append(f"Weekend dates on `{primary}` (the calendar with weekend trading), MSK:")
        lines.append("")
        lines.append("| Day | is_trading_day | Start | End |")
        lines.append("|---|---|---|---|")
        for entry in schedules[primary]:
            if entry.day.weekday() < 5:
                continue
            lines.append(
                f"| {entry.day.isoformat()} | {entry.is_trading_day} | "
                f"{_msk(entry.start)} | {_msk(entry.end)} |"
            )
        lines.append("")
        lines.append(
            "`start == end` means the calendar marks the whole day, not a zero-length "
            "session. The stored weekend bars open at 07:00 MSK (section 3), so the "
            "calendar answers 'the day trades', not the exact executable window; that "
            "window has to come from the exchange schedule, not from a guessed timestamp."
        )
    lines.append("")
    lines.append("## 6. Is `candle_source == 2` a reliable weekend marker?")
    lines.append("")
    lines.append(
        "Cross-tab of the stored `candle_source` against the calendar weekday of the "
        "session. The marker is usable in a rule only if weekend bars carry 2 and "
        "weekday bars do not; a `None` value is a missing field, not a weekday bar."
    )
    lines.append("")
    lines.append("| Session | candle_source | Bars |")
    lines.append("|---|---|---|")
    for (weekend, source), count in sorted(
        source_counts.items(), key=lambda item: (not item[0][0], str(item[0][1]))
    ):
        session = "weekend" if weekend else "weekday"
        lines.append(f"| {session} | {source if source is not None else 'None'} | {count} |")
    weekend_sources = {source for (weekend, source) in source_counts if weekend}
    weekday_sources = {source for (weekend, source) in source_counts if not weekend}
    if weekend_sources == {2} and 2 not in weekday_sources:
        lines.append("")
        lines.append("Verdict: the marker is **reliable** on this history.")
    else:
        lines.append("")
        lines.append(
            f"Verdict: the marker is **not reliable** — weekend sources "
            f"{sorted(str(item) for item in weekend_sources)}, weekday sources "
            f"{sorted(str(item) for item in weekday_sources)}."
        )
    lines.append("")
    lines.append("### Schedule against candles")
    lines.append("")
    if schedule_error:
        lines.append("Not computed: no schedule.")
    elif not mismatches:
        lines.append(
            f"No overlap: the last stored candle is {last_candle_day.isoformat()} and the "
            "schedule window starts today, so the two sources cannot be compared on history."
        )
    else:
        counts = Counter(item.kind for item in mismatches)
        lines.append(
            f"- `{CANDLES_WITHOUT_TRADING_DAY}`: {counts.get(CANDLES_WITHOUT_TRADING_DAY, 0)} "
            "(broker says no trading, a bar exists: the branch-2 criterion, and the test of "
            "whether `candle_source == 2` is a reliable marker)"
        )
        lines.append(
            f"- `trading_day_without_candles`: {counts.get(TRADING_DAY_WITHOUT_CANDLES, 0)} "
            "(a trading day with no stored bar: a hole in the history)"
        )
        lines.append("")
        lines.append("| Day | Kind |")
        lines.append("|---|---|")
        for item in mismatches:
            lines.append(f"| {item.day.isoformat()} | `{item.kind}` |")
    lines.append("")
    lines.append("## 7. Gaps across a weekend")
    lines.append("")
    lines.append(
        "`total` is the sum of every transition inside the break, not "
        "`Friday close -> Monday open`: with weekend trading a break has several "
        "transitions, and one transition understates it."
    )
    lines.append("")
    if not breaks:
        lines.append("No weekend breaks in the stored series.")
    else:
        traded = sum(1 for item in breaks if item.weekend_traded)
        no_monday = [item for item in breaks if item.weekend_traded and not item.resumed_on_monday]
        lines.append(
            f"{len(breaks)} breaks, {traded} with trading on the weekend, "
            f"{len(no_monday)} where trading resumed after Monday."
        )
        ordered = sorted(breaks, key=lambda item: abs(item.total_percent), reverse=True)
        lines.append("")
        lines.append(
            f"Top {min(max(1, max_breaks), len(ordered))} breaks by absolute total:"
        )
        lines.append("")
        lines.append(
            "| Ticker | First bar | Last bar | Transitions | Total, % | Weekend traded | Monday |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for item in ordered[: max(1, max_breaks)]:
            lines.append(
                f"| {item.ticker} | {item.first_bar:%Y-%m-%d %H:%M} | "
                f"{item.last_bar:%Y-%m-%d %H:%M} | "
                f"{item.transitions} | {_fmt(item.total_percent)} | "
                f"{item.weekend_traded} | {item.resumed_on_monday} |"
            )
        mtss = [
            item
            for item in breaks
            if item.ticker == "MTSS"
            and item.first_bar.year == 2025
            and item.first_bar.month == 7
        ]
        if mtss:
            lines.append("")
            lines.append("MTSS 2025-07 case (T2-22a remainder):")
            for item in sorted(mtss, key=lambda entry: entry.first_bar):
                lines.append(
                    f"- {item.first_bar:%Y-%m-%d} → {item.last_bar:%Y-%m-%d}: "
                    f"{item.transitions} transitions, total {_fmt(item.total_percent)}%"
                )
    lines.append("")
    lines.append("## 8. Decision per ADR-031")
    lines.append("")
    groups = (audit.entry_only, audit.exit_only, audit.both, audit.spans_only, audit.other)
    below = [group for group in groups if group.trades > 0 and group.below_floor]
    if below:
        lines.append(f"- branch 1, groups below `min_sample_trades` {thresholds.min_sample_trades}:")
        for group in below:
            lines.append(f"  - {group.name}: {group.trades} trades → net does not decide")
    else:
        lines.append(
            f"- branch 1: every non-empty group reaches `min_sample_trades` "
            f"{thresholds.min_sample_trades}, so the intervals below are decidable"
        )
    separated = [
        group
        for group in groups
        if group.trades > 0 and not group.below_floor and (group.ci_low > 0 or group.ci_high < 0)
    ]
    not_separated = [
        group for group in groups if group.trades > 0 and not group.below_floor
    ]
    not_separated = [group for group in not_separated if group not in separated]
    below_but_separated = [
        group
        for group in groups
        if group.trades > 0 and group.below_floor and (group.ci_low > 0 or group.ci_high < 0)
    ]
    for group in separated:
        lines.append(
            f"- CI excludes zero: {group.name} — {group.net_bps:+.2f} bps "
            f"[{group.ci_low:+.2f}; {group.ci_high:+.2f}]"
        )
    for group in not_separated:
        lines.append(
            f"- CI contains zero: {group.name} — {group.net_bps:+.2f} bps "
            f"[{group.ci_low:+.2f}; {group.ci_high:+.2f}]"
        )
    for group in below_but_separated:
        lines.append(
            f"- CI excludes zero but the group is below the floor: {group.name} — "
            f"{group.net_bps:+.2f} bps [{group.ci_low:+.2f}; {group.ci_high:+.2f}] on "
            f"{group.trades} trades; branch 1 says this interval does not decide"
        )
    if rule is None:
        lines.append("- branch 2 (structural unusability): **does not fire**")
        if separated:
            lines.append(
                "- one or more groups are separated from zero, which branch 2 does not "
                "cover: this is a result to weigh in ADR-032, not an automatic exclusion"
            )
        else:
            lines.append(
                "- branch 3 applies: no group at or above `min_sample_trades` separates "
                "from zero and the bars are usable, so the model is unchanged — the "
                "weekend counter stays informational and `model_revision` stays 3"
            )
    else:
        lines.append(f"- branch 2 fires: {rule}")
        lines.append(
            "- that is a model change: `model_revision` 3 → 4, new config version, "
            "re-run the verdict; describe it in ADR-032"
        )
    lines.append("")
    lines.append("### Limitations recorded with this verdict")
    lines.append("")
    lines.append(
        "- **The schedule criterion of branch 2 was not decidable.** `trading_schedules` "
        "answers only from today (30003) and for at most 14 days (30002), so it cannot "
        "check stored history; and one exchange name is one calendar — section 5 shows "
        "some MOEX calendars with no weekend trading and others with every weekend day "
        "trading. No name is 'the' calendar for stocks, so the criterion would decide by "
        "the parameter. The substitute evidence is stated in ADR-031: weekend bars carry "
        "real non-zero volume (zero-volume share "
        f"{overall_weekend.zero_volume_share:.1%}), which is measured on the stored data "
        "and does not depend on a calendar name."
    )
    lines.append(
        "- **A weekend session is 3 bars of 4h from 07:00 MSK**, not a full day. "
        "Indicators count bars, so a 50-bar window covers fewer calendar days on a week "
        "with a weekend session than on a week without one; calendar-time comparisons "
        "drift on those weeks. The effect is small and it is a property of bar-counting "
        "indicators, not a defect of the data, but a report that says 'the last 50 bars' "
        "must not be read as 'the last N calendar days'."
    )
    lines.append(
        "- **A thinner session with one cost model.** Median weekend volume is "
        f"{overall_weekend.median_volume:,.0f} against "
        f"{overall_weekday.median_volume:,.0f} on weekdays and the range is "
        f"{overall_weekend.median_range_bps:.1f} against "
        f"{overall_weekday.median_range_bps:.1f} bps, while spread and slippage are one "
        f"number per instrument. The ×{thresholds.cost_multiplier:g} column in section 1 "
        "is the informational sensitivity; a stop filled in a thin weekend book is the "
        "live-side question, not a backtest number."
    )
    lines.append(
        "- **Forward schedule for live.** The same `trading_schedules` call answers the "
        "live question without orders: the next weekend dates show whether the broker "
        "trades the set on a weekend session. It is a cheap executability check to repeat "
        "before enabling weekend signals in live."
    )
    lines.append("")
    lines.append(
        "The audit is a data-quality check, not a hypothesis: it spends no journal budget. "
        "Excluding bars because the net rose would be one idea in `journal/hypotheses.jsonl`."
    )
    lines.append("")
    return "\n".join(lines)


# ── entry point ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    config = load_portfolio(args.portfolio_file)
    thresholds = load_weekend_thresholds(args.thresholds_file)
    multi = load_multi_instrument_thresholds(args.thresholds_file)
    instruments = load_instruments(INSTRUMENTS_PATH)
    tickers = list(config.tickers)
    if args.limit:
        tickers = tickers[: args.limit]
    unknown = [ticker for ticker in tickers if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")

    timeframe = Timeframe(config.timeframe)
    costs_config = load_costs(args.costs_file)
    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    candles_by_ticker = read_candles(history, instruments, tickers, timeframe)
    if not candles_by_ticker:
        raise SystemExit("No ticker has candles; check the database")

    runs: dict[str, list[Trade]] = {}
    gaps: dict[str, dict] = {}
    for ticker, series in candles_by_ticker.items():
        costs = costs_config.for_ticker(ticker)
        if not costs.is_tradable:
            logger.warning("%s has a zero-cost model, skipped", ticker)
            continue
        runner = build_runner(config.strategy, config.timeframe, ticker, costs, config)
        runs[ticker] = runner(series)
        gaps[ticker] = gap_series(series)
    if not runs:
        raise SystemExit("No ticker produced a run; check config/costs.json")

    # Informational sensitivity: the same trade set under doubled costs. Levels are
    # built from the raw open (ADR-012), so the set of trades must not change; the
    # script checks that instead of assuming it.
    scaled_costs = doubled_costs(costs_config, thresholds.cost_multiplier)
    scaled_runs: dict[str, list[Trade]] = {}
    for ticker, series in candles_by_ticker.items():
        if ticker not in runs:
            continue
        runner = build_runner(
            config.strategy, config.timeframe, ticker, scaled_costs(ticker), config
        )
        scaled_runs[ticker] = runner(series)

    pooled = pool_trades(
        runs,
        gaps,
        gap_threshold_percent=multi.gap_threshold_percent,
    )
    bar_duration = timeframe.to_timedelta
    calendars = {
        ticker: bar_calendar(series, bar_duration=bar_duration)
        for ticker, series in candles_by_ticker.items()
    }
    audit = audit_trades(pooled, calendars, thresholds=thresholds)

    scaled_pooled = pool_trades(
        scaled_runs,
        gaps,
        gap_threshold_percent=multi.gap_threshold_percent,
    )
    scaled_audit = audit_trades(scaled_pooled, calendars, thresholds=thresholds)
    base_keys = Counter(
        (item.ticker, item.trade.entry_time, item.trade.exit_time) for item in pooled
    )
    scaled_keys = Counter(
        (item.ticker, item.trade.entry_time, item.trade.exit_time) for item in scaled_pooled
    )
    same_trade_set = base_keys == scaled_keys

    microstructure: list[TickerMicrostructure] = []
    overall_weekend: list[Candle] = []
    overall_weekday: list[Candle] = []
    sessions: dict[str, list] = {}
    prepared: dict[str, dict] = {}
    breaks: list[BreakGap] = []
    for ticker, series in candles_by_ticker.items():
        item = ticker_microstructure(ticker, series, start=thresholds.start)
        microstructure.append(item)
        if item.window_start is not None:
            in_window = [
                candle for candle in series if candle.open_time.date() >= item.window_start
            ]
            overall_weekend.extend(c for c in in_window if is_weekend_bar(c))
            overall_weekday.extend(c for c in in_window if not is_weekend_bar(c))
            sessions[ticker] = session_structure(in_window)
        prepared[ticker] = prepare_effect(series)
        breaks.extend(weekend_breaks(series, ticker=ticker))

    last_day = max(series[-1].open_time.date() for series in candles_by_ticker.values())
    schedules: dict[str, list[ScheduleDay]] = {}
    schedule_error: str | None = None
    if args.no_schedule:
        schedule_error = "skipped by --no-schedule"
    else:
        exchanges = [name.strip() for name in args.exchange.split(",") if name.strip()]
        try:
            schedules = fetch_schedules(exchanges, args.schedule_days)
        except Exception as exc:  # noqa: BLE001 - the audit degrades, it does not crash
            schedule_error = f"{type(exc).__name__}: {exc}"

    source_counts: dict[tuple[bool, int | None], int] = {}
    for series in candles_by_ticker.values():
        for key, count in source_marker_counts(series).items():
            source_counts[key] = source_counts.get(key, 0) + count

    mismatches: list[ScheduleMismatch] = []
    for days in schedules.values():
        for series in candles_by_ticker.values():
            mismatches.extend(schedule_mismatches(days, series))
    mismatches = sorted(
        {(item.day, item.kind): item for item in mismatches}.values(),
        key=lambda item: (item.day, item.kind),
    )

    rule = structural_rule(microstructure, mismatches, thresholds=thresholds)

    report = render_report(
        tag=args.tag,
        strategy=config.strategy,
        timeframe=config.timeframe,
        tp_percent=config.parameters.tp_percent,
        sl_percent=config.parameters.sl_percent,
        thresholds=thresholds,
        audit=audit,
        scaled=scaled_audit,
        same_trade_set=same_trade_set,
        microstructure=microstructure,
        overall_weekend=bar_stats(overall_weekend),
        overall_weekday=bar_stats(overall_weekday),
        sessions=sessions,
        schedules=schedules,
        schedule_error=schedule_error,
        mismatches=mismatches,
        source_counts=source_counts,
        breaks=breaks,
        prepared=prepared,
        rule=rule,
        max_breaks=args.max_breaks,
        last_candle_day=last_day,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"weekend_{args.tag}.md"
    md_path.write_text(report, encoding="utf-8")
    json_path = out_dir / f"weekend_{args.tag}.json"
    json_path.write_text(
        json.dumps(
            {
                "thresholds": thresholds.describe(),
                "portfolio": config.fingerprint,
                "strategy": config.strategy,
                "timeframe": config.timeframe,
                "trades": audit.describe(),
                "microstructure": [item.describe() for item in microstructure],
                "overall_weekend": bar_stats(overall_weekend).describe(),
                "overall_weekday": bar_stats(overall_weekday).describe(),
                "schedule_error": schedule_error,
                "schedules": {
                    name: {
                        "days": len(days),
                        "weekend_days": sum(1 for entry in days if entry.day.weekday() >= 5),
                        "weekend_trading": sum(
                            1
                            for entry in days
                            if entry.day.weekday() >= 5 and entry.is_trading_day
                        ),
                    }
                    for name, days in schedules.items()
                },
                "mismatches": [item.describe() for item in mismatches],
                "breaks": [item.describe() for item in breaks],
                "prepared": prepared,
                "structural_rule": rule,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"{config.strategy} {config.timeframe} TP {config.parameters.tp_percent:g} / "
        f"SL {config.parameters.sl_percent:g} tickers={len(candles_by_ticker)}"
    )
    print(f"validation={thresholds.fingerprint} window from {thresholds.start.isoformat()}")
    print(
        f"trades={audit.total} entry-weekend={audit.entry_weekend_trades} "
        f"exit-weekend={audit.exit_weekend_trades} both={audit.both.trades} "
        f"spans-only={audit.spans_only.trades} other={audit.other.trades} "
        f"partition={audit.partition_trades}"
    )
    print(
        f"weekend bars={bar_stats(overall_weekend).bars} "
        f"zero-vol={bar_stats(overall_weekend).zero_volume_share:.1%} "
        f"weekday zero-vol={bar_stats(overall_weekday).zero_volume_share:.1%}"
    )
    if schedule_error:
        print(f"schedule: unavailable: {schedule_error}")
    else:
        for name, days in schedules.items():
            weekend = [entry for entry in days if entry.day.weekday() >= 5]
            trading = sum(1 for entry in weekend if entry.is_trading_day)
            print(f"schedule {name}: {len(weekend)} weekend days, {trading} trading")
    print(f"mismatches={len(mismatches)} breaks={len(breaks)}")
    print(f"structural rule: {rule if rule else 'does not fire'}")
    print(f"report: {md_path}")
    print(f"snapshot: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
