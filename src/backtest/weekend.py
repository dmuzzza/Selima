"""Weekend-bar audit: is the weekend session a tradable session? (T2-23, ADR-031)

MOEX has run weekend trading since March 2025. Those bars are already in the
database and already take part in trades, but the execution model (ADR-012) and the
cost model (ADR-016, ADR-017) were built on weekday sessions: spread and slippage
are one number per instrument, and a 4h bar assumes the main session.

This module answers the data-quality question only. It never changes a trade set or
a PnL: it classifies trades, measures bar microstructure and compares the broker
trading schedule with the candles. The decision rule and its thresholds were frozen
before the run in `config/validation.json` and ADR-031, so the audit cannot pick its
answer after seeing the number:

1. a trade group below `min_sample_trades` cannot support a net-based decision -
   decide on microstructure and live executability instead;
2. weekend bars are structurally unusable when the broker schedule says
   `is_trading_day = false` for a day that has candles, or when the share of
   zero-volume weekend bars exceeds `weekend.max_zero_volume_share`;
3. no difference - keep an informational counter, do not change the model;
4. excluding bars because the net rose - that is a hypothesis, not an audit.

Every function here is pure: candles and trades in, numbers out. Database access and
the API live in `scripts/weekend_audit.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

from .portfolio import BlockBootstrap, PooledTrade, block_bootstrap

DEFAULT_WEEKEND_START = date(2025, 3, 1)
DEFAULT_MAX_ZERO_VOLUME_SHARE = 0.10

SATURDAY = 5
SUNDAY = 6


# ── configuration ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WeekendThresholds:
    """Frozen decision rule plus provenance, printed with the report."""

    start: date = DEFAULT_WEEKEND_START
    max_zero_volume_share: float = DEFAULT_MAX_ZERO_VOLUME_SHARE
    min_sample_trades: int = 30
    block_days: int = 30
    bootstrap_iterations: int = 10_000
    alpha: float = 0.05
    seed: int = 42
    cost_multiplier: float = 2.0
    source: str = "defaults"
    fingerprint: str = "defaults"

    def __post_init__(self) -> None:
        if not 0.0 <= self.max_zero_volume_share <= 1.0:
            raise ValueError("max_zero_volume_share must be in [0, 1]")
        if self.min_sample_trades < 1:
            raise ValueError("min_sample_trades must be positive")
        if self.block_days < 1:
            raise ValueError("block_days must be positive")
        if self.cost_multiplier <= 0:
            raise ValueError("cost_multiplier must be positive")

    def describe(self) -> dict:
        return {
            "weekend_start": self.start.isoformat(),
            "max_zero_volume_share": self.max_zero_volume_share,
            "min_sample_trades": self.min_sample_trades,
            "block_days": self.block_days,
            "bootstrap_iterations": self.bootstrap_iterations,
            "alpha": self.alpha,
            "seed": self.seed,
            "cost_multiplier": self.cost_multiplier,
            "validation_version": self.fingerprint,
            "validation_source": self.source,
        }


def load_weekend_thresholds(path: str | Path) -> WeekendThresholds:
    """Read `weekend` and the shared sample floor from config/validation.json.

    `min_sample_trades` sits at the top level on purpose: the heatmap cell and the
    weekend trade group share one number, so the two checks cannot drift apart. The
    bootstrap parameters come from the `multi_instrument` section - the same
    resampling as the pooled report, not a second convention.
    """
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    section = payload.get("weekend")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: missing 'weekend' section")
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    floor = payload.get("min_sample_trades")
    if floor is None:
        raise ValueError(f"{path}: missing 'min_sample_trades'")
    multi = payload.get("multi_instrument") or {}
    verdict = payload.get("verdict") or {}
    return WeekendThresholds(
        start=date.fromisoformat(str(section["start"])),
        max_zero_volume_share=float(section["max_zero_volume_share"]),
        min_sample_trades=int(floor),
        block_days=int(multi.get("block_days", 30)),
        bootstrap_iterations=int(multi.get("bootstrap_iterations", 10_000)),
        alpha=float(multi.get("alpha", 0.05)),
        seed=int(multi.get("seed", 42)),
        # The same multiplier the verdict uses for its cost gate: one number for
        # "what if costs are twice as high", not a second convention.
        cost_multiplier=float(verdict.get("cost_multiplier", 2.0)),
        source=str(path),
        fingerprint=f"{version}+{hashlib.sha256(raw).hexdigest()[:12]}",
    )


# ── bars ───────────────────────────────────────────────────────────────────


def is_weekend_bar(candle: Candle) -> bool:
    """A weekend bar is one whose session opens on Saturday or Sunday.

    Candles are stored in MSK (`HistoryManager`), so the weekday is the exchange
    weekday, not UTC. The marker is the calendar, not `candle_source`: the API field
    is checked separately, because a missing or misleading source value must not
    silently redefine the set of bars.
    """
    return candle.open_time.weekday() >= SATURDAY


@dataclass(frozen=True)
class BarStats:
    """Microstructure of a bar set, comparable between weekdays and weekends."""

    bars: int
    zero_volume: int
    zero_volume_share: float
    median_volume: float
    median_range_bps: float

    def describe(self) -> dict:
        return {
            "bars": self.bars,
            "zero_volume": self.zero_volume,
            "zero_volume_share": round(self.zero_volume_share, 4),
            "median_volume": round(self.median_volume, 1),
            "median_range_bps": round(self.median_range_bps, 1),
        }


def bar_stats(candles: Sequence[Candle]) -> BarStats:
    """Volume and high-low range of a bar set; range is in bps of the open."""
    if not candles:
        return BarStats(
            bars=0,
            zero_volume=0,
            zero_volume_share=0.0,
            median_volume=0.0,
            median_range_bps=0.0,
        )
    zero = sum(1 for candle in candles if not candle.volume or candle.volume <= 0)
    volumes = [float(candle.volume) for candle in candles if candle.volume and candle.volume > 0]
    ranges = [
        (candle.high - candle.low) / candle.open * 10_000.0
        for candle in candles
        if candle.open > 0
    ]
    return BarStats(
        bars=len(candles),
        zero_volume=zero,
        zero_volume_share=zero / len(candles),
        median_volume=median(volumes) if volumes else 0.0,
        median_range_bps=median(ranges) if ranges else 0.0,
    )


@dataclass(frozen=True)
class TickerMicrostructure:
    """Weekend versus weekday bars of one ticker inside its own window."""

    ticker: str
    window_start: date | None
    weekend: BarStats
    weekday: BarStats
    max_zero_volume_share: float = DEFAULT_MAX_ZERO_VOLUME_SHARE

    @property
    def weekend_unusable(self) -> bool:
        """Zero-volume criterion of branch 2, applied per ticker (ADR-031)."""
        return (
            self.weekend.bars > 0
            and self.weekend.zero_volume_share > self.max_zero_volume_share
        )

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "weekend": self.weekend.describe(),
            "weekday": self.weekday.describe(),
            "weekend_unusable": self.weekend_unusable,
        }


def ticker_window_start(candles: Sequence[Candle], *, start: date) -> date | None:
    """Window start for one ticker: the configured date or its first weekend bar.

    The portfolio set was expanded at different dates, so a ticker may have no
    weekend bars when the window opens. Starting such a ticker at the configured
    date would compare its weekdays against an empty weekend set. `None` means the
    ticker has no weekend bars at all and is out of the audit.
    """
    weekend_dates = [candle.open_time.date() for candle in candles if is_weekend_bar(candle)]
    if not weekend_dates:
        return None
    return max(start, min(weekend_dates))


def ticker_microstructure(
    ticker: str, candles: Sequence[Candle], *, start: date
) -> TickerMicrostructure:
    """Weekend and weekday bar stats of one ticker from its own window start."""
    window = ticker_window_start(candles, start=start)
    if window is None:
        empty = bar_stats([])
        return TickerMicrostructure(ticker=ticker, window_start=None, weekend=empty, weekday=empty)
    in_window = [candle for candle in candles if candle.open_time.date() >= window]
    weekend = [candle for candle in in_window if is_weekend_bar(candle)]
    weekday = [candle for candle in in_window if not is_weekend_bar(candle)]
    return TickerMicrostructure(
        ticker=ticker,
        window_start=window,
        weekend=bar_stats(weekend),
        weekday=bar_stats(weekday),
    )


@dataclass(frozen=True)
class SessionStructure:
    """One session: how many bars it has and how long it spans."""

    day: date
    weekend: bool
    bars: int
    first_open: datetime
    last_open: datetime
    span_hours: float

    def describe(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "weekend": self.weekend,
            "bars": self.bars,
            "first_open": self.first_open.isoformat(),
            "last_open": self.last_open.isoformat(),
            "span_hours": round(self.span_hours, 2),
        }


def session_structure(candles: Sequence[Candle]) -> list[SessionStructure]:
    """Bars per calendar day, so a short weekend session is visible as such.

    A 4h bar assumes the main session. If a weekend session carries fewer bars or
    opens at another hour, the bar is not the same object as a weekday bar, and
    `prepare_candles` must not merge it with a weekday bar.
    """
    by_day: dict[date, list[Candle]] = {}
    for candle in candles:
        by_day.setdefault(candle.open_time.date(), []).append(candle)
    sessions: list[SessionStructure] = []
    for day in sorted(by_day):
        bars = sorted(by_day[day], key=lambda candle: candle.open_time)
        sessions.append(
            SessionStructure(
                day=day,
                weekend=day.weekday() >= SATURDAY,
                bars=len(bars),
                first_open=bars[0].open_time,
                last_open=bars[-1].open_time,
                span_hours=(bars[-1].open_time - bars[0].open_time).total_seconds() / 3600.0,
            )
        )
    return sessions


# ── trade classification ───────────────────────────────────────────────────


@dataclass(frozen=True)
class BarCalendar:
    """Weekend markers for one ticker: bar opens and bar ends.

    A trade enters at the open of the bar after the signal and exits at the end of
    its last bar (ADR-012), so the two sets answer "was the entry bar a weekend bar"
    and "was the exit bar a weekend bar" without guessing a duration.
    """

    weekend_opens: frozenset[datetime]
    weekend_ends: frozenset[datetime]


def bar_calendar(candles: Sequence[Candle], *, bar_duration: timedelta) -> BarCalendar:
    """Index a ticker's candles by weekend status of their open and their end."""
    opens: set[datetime] = set()
    ends: set[datetime] = set()
    for candle in candles:
        if not is_weekend_bar(candle):
            continue
        opens.add(candle.open_time)
        ends.add(candle.open_time + bar_duration)
    return BarCalendar(weekend_opens=frozenset(opens), weekend_ends=frozenset(ends))


def entry_is_weekend(trade: Trade, calendar: BarCalendar | None) -> bool:
    """Entry on a weekend bar. Falls back to the calendar weekday of `entry_time`."""
    if calendar is not None and trade.entry_time in calendar.weekend_opens:
        return True
    return trade.entry_time.weekday() >= SATURDAY


def exit_is_weekend(trade: Trade, calendar: BarCalendar | None) -> bool:
    """Exit on a weekend bar.

    `exit_time` is the end of the exit bar, so the bar itself is identified through
    the calendar. When the exact boundary is absent (a gap in the stored series),
    the weekday of `exit_time` is used and the fallback is documented, not silent.
    """
    if trade.exit_time is None:
        return False
    if calendar is not None and trade.exit_time in calendar.weekend_ends:
        return True
    return trade.exit_time.weekday() >= SATURDAY


def holding_spans_weekend(trade: Trade) -> bool:
    """True when the holding period covers a Saturday or Sunday date.

    Independent of the entry and exit flags: a Friday entry and a Monday exit spans
    a weekend without either bar being a weekend bar.
    """
    if trade.exit_time is None:
        return False
    day = trade.entry_time.date()
    last = trade.exit_time.date()
    while day <= last:
        if day.weekday() >= SATURDAY:
            return True
        day += timedelta(days=1)
    return False


@dataclass(frozen=True)
class TradeGroup:
    """One attribute of the trade set: count, net, expectancy and interval."""

    name: str
    trades: int
    net_rub: float
    net_bps: float
    ci_low: float
    ci_high: float
    below_floor: bool

    def describe(self) -> dict:
        return {
            "group": self.name,
            "trades": self.trades,
            "net_rub": round(self.net_rub, 2),
            "net_bps": round(self.net_bps, 2),
            "ci95": [round(self.ci_low, 2), round(self.ci_high, 2)],
            "below_floor": self.below_floor,
        }


def summarize_group(
    name: str,
    trades: Sequence[PooledTrade],
    *,
    min_sample_trades: int,
    block_days: int,
    bootstrap_iterations: int,
    alpha: float,
    seed: int,
) -> TradeGroup:
    """Count, net and block bootstrap interval for one trade attribute."""
    if not trades:
        return TradeGroup(
            name=name,
            trades=0,
            net_rub=0.0,
            net_bps=0.0,
            ci_low=0.0,
            ci_high=0.0,
            below_floor=True,
        )
    interval: BlockBootstrap = block_bootstrap(
        trades,
        block_days=block_days,
        iterations=bootstrap_iterations,
        alpha=alpha,
        seed=seed,
    )
    return TradeGroup(
        name=name,
        trades=len(trades),
        net_rub=sum(item.trade.pnl_net or 0.0 for item in trades),
        net_bps=interval.net_bps,
        ci_low=interval.ci_low,
        ci_high=interval.ci_high,
        below_floor=len(trades) < min_sample_trades,
    )


@dataclass(frozen=True)
class TradeAudit:
    """A partition of the pool by weekend contact, plus the totals it implies.

    The five groups are mutually exclusive and sum to the pool: `entry_only`,
    `exit_only`, `both` (entry and exit on weekend bars), `spans_only` (neither bar
    is a weekend bar, but the holding covers a weekend date) and `other` (no contact).
    An attribute view would be easier to read but not additive: a trade that enters
    on a weekend bar also spans a weekend, and printing overlapping groups next to
    each other invites adding them up.
    """

    entry_only: TradeGroup
    exit_only: TradeGroup
    both: TradeGroup
    spans_only: TradeGroup
    other: TradeGroup
    total: int

    @property
    def partition_trades(self) -> int:
        """Sum of the groups; must equal `total`, printed as a check."""
        return (
            self.entry_only.trades
            + self.exit_only.trades
            + self.both.trades
            + self.spans_only.trades
            + self.other.trades
        )

    @property
    def entry_weekend_trades(self) -> int:
        """Trades whose entry bar is a weekend bar, whatever the exit is."""
        return self.entry_only.trades + self.both.trades

    @property
    def exit_weekend_trades(self) -> int:
        """Trades whose exit bar is a weekend bar, whatever the entry is."""
        return self.exit_only.trades + self.both.trades

    def describe(self) -> dict:
        return {
            "entry_only": self.entry_only.describe(),
            "exit_only": self.exit_only.describe(),
            "both": self.both.describe(),
            "spans_only": self.spans_only.describe(),
            "other": self.other.describe(),
            "total": self.total,
            "partition_trades": self.partition_trades,
            "entry_weekend_trades": self.entry_weekend_trades,
            "exit_weekend_trades": self.exit_weekend_trades,
        }


def audit_trades(
    pooled: Sequence[PooledTrade],
    calendars: Mapping[str, BarCalendar],
    *,
    thresholds: WeekendThresholds,
) -> TradeAudit:
    """Partition the pool by weekend contact without changing any trade.

    Every closed trade lands in exactly one group, so the five counts add up to the
    pool. The entry-weekend and exit-weekend totals are reported separately: they
    are the numbers a reader actually wants, and they are sums of groups rather than
    a second, overlapping classification.
    """
    entry_only: list[PooledTrade] = []
    exit_only: list[PooledTrade] = []
    both: list[PooledTrade] = []
    spans_only: list[PooledTrade] = []
    other: list[PooledTrade] = []
    for item in pooled:
        calendar = calendars.get(item.ticker)
        on_entry = entry_is_weekend(item.trade, calendar)
        on_exit = exit_is_weekend(item.trade, calendar)
        if on_entry and on_exit:
            both.append(item)
        elif on_entry:
            entry_only.append(item)
        elif on_exit:
            exit_only.append(item)
        elif holding_spans_weekend(item.trade):
            spans_only.append(item)
        else:
            other.append(item)

    def summarize(name: str, group: Sequence[PooledTrade]) -> TradeGroup:
        return summarize_group(
            name,
            group,
            min_sample_trades=thresholds.min_sample_trades,
            block_days=thresholds.block_days,
            bootstrap_iterations=thresholds.bootstrap_iterations,
            alpha=thresholds.alpha,
            seed=thresholds.seed,
        )

    return TradeAudit(
        entry_only=summarize("entry on a weekend bar, exit on a weekday", entry_only),
        exit_only=summarize("exit on a weekend bar, entry on a weekday", exit_only),
        both=summarize("entry and exit on weekend bars", both),
        spans_only=summarize("spans a weekend, no weekend bar", spans_only),
        other=summarize("no weekend contact", other),
        total=len(pooled),
    )


# ── gaps across a weekend ──────────────────────────────────────────────────


@dataclass(frozen=True)
class BreakGap:
    """One weekend break: every transition inside it, summed.

    `total_percent` is the sum of the per-transition gaps, not
    `Friday close -> Monday open`. With weekend trading the break consists of
    several transitions (Fri -> Sat, Sat -> Mon), and a single transition
    understates it: MTSS 2025-07 splits into -3.22% and -11.89%.
    """

    first_bar: datetime
    last_bar: datetime
    transitions: int
    total_percent: float
    weekend_traded: bool
    resumed_on_monday: bool
    ticker: str = ""

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "first_bar": self.first_bar.isoformat(),
            "last_bar": self.last_bar.isoformat(),
            "transitions": self.transitions,
            "total_percent": round(self.total_percent, 2),
            "weekend_traded": self.weekend_traded,
            "resumed_on_monday": self.resumed_on_monday,
        }


def _dates_contain_weekend(first: date, last: date) -> bool:
    """True when the closed date range [first, last] includes a Saturday or Sunday."""
    day = first
    while day <= last:
        if day.weekday() >= SATURDAY:
            return True
        day += timedelta(days=1)
    return False


def weekend_breaks(candles: Sequence[Candle], *, ticker: str = "") -> list[BreakGap]:
    """Group adjacent-session transitions into weekend breaks and sum each one.

    Two consecutive transitions belong to the same break when both contain a weekend
    date: `Fri -> Sat` and `Sat -> Mon` are one break, while `Wed -> Thu` is not a
    break at all. A break where the weekend traded but Monday did not is flagged by
    `resumed_on_monday = False`, so the report can name it instead of averaging it
    away.
    """
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    breaks: list[BreakGap] = []
    first_bar: datetime | None = None
    last_bar: datetime | None = None
    transitions = 0
    total = 0.0

    def flush() -> None:
        nonlocal first_bar, last_bar, transitions, total
        if first_bar is None or last_bar is None:
            return
        inside = [candle for candle in ordered if first_bar <= candle.open_time <= last_bar]
        breaks.append(
            BreakGap(
                first_bar=first_bar,
                last_bar=last_bar,
                transitions=transitions,
                total_percent=total,
                weekend_traded=any(is_weekend_bar(candle) for candle in inside),
                resumed_on_monday=last_bar.weekday() == 0,
                ticker=ticker,
            )
        )
        first_bar = None
        last_bar = None
        transitions = 0
        total = 0.0

    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.close <= 0:
            flush()
            continue
        gap = (current.open - previous.close) / previous.close * 100.0
        if not _dates_contain_weekend(previous.open_time.date(), current.open_time.date()):
            flush()
            continue
        if first_bar is None:
            first_bar = previous.open_time
        last_bar = current.open_time
        transitions += 1
        total += gap
    flush()
    return breaks


# ── broker schedule ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScheduleDay:
    """One day from `InstrumentsService.trading_schedules`."""

    day: date
    is_trading_day: bool
    start: datetime | None = None
    end: datetime | None = None

    def describe(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "is_trading_day": self.is_trading_day,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
        }


@dataclass(frozen=True)
class ScheduleMismatch:
    """A day where the broker schedule and the stored candles disagree."""

    day: date
    kind: str

    def describe(self) -> dict:
        return {"day": self.day.isoformat(), "kind": self.kind}


def source_marker_counts(candles: Iterable[Candle]) -> dict[tuple[bool, int | None], int]:
    """Cross-tab of `candle_source` against the calendar weekend marker.

    `trading_schedules` only answers for the current and future dates (API error
    30003), so it cannot validate the stored history. The stored source field can:
    if every weekend bar carries `candle_source == 2` and no weekday bar does, the
    marker is reliable and a rule may use it. A bar with `None` is a missing value,
    not a weekday bar, and is counted separately.
    """
    counts: dict[tuple[bool, int | None], int] = {}
    for candle in candles:
        key = (is_weekend_bar(candle), candle.candle_source)
        counts[key] = counts.get(key, 0) + 1
    return counts


CANDLES_WITHOUT_TRADING_DAY = "candles_without_trading_day"
TRADING_DAY_WITHOUT_CANDLES = "trading_day_without_candles"


def schedule_mismatches(
    schedule: Iterable[ScheduleDay], candles: Sequence[Candle]
) -> list[ScheduleMismatch]:
    """Compare the broker schedule with the stored candles, day by day.

    The two sources can disagree in both directions, and each direction means
    something different:

    - `candles_without_trading_day` - the broker says the day is not trading, yet a
      bar exists. This is the criterion of branch 2 and also the test of whether
      `candle_source == 2` is a reliable weekend marker;
    - `trading_day_without_candles` - a trading day with no stored bar, so the
      history has a hole and a trade could not have been simulated there.

    Only dates covered by the candles are compared: an empty tail of the schedule is
    not a mismatch.
    """
    if not candles:
        return []
    by_day: dict[date, int] = {}
    for candle in candles:
        by_day[candle.open_time.date()] = by_day.get(candle.open_time.date(), 0) + 1
    first, last = min(by_day), max(by_day)
    mismatches: list[ScheduleMismatch] = []
    for entry in schedule:
        if not first <= entry.day <= last:
            continue
        has_candles = entry.day in by_day
        if entry.is_trading_day and not has_candles:
            mismatches.append(ScheduleMismatch(day=entry.day, kind=TRADING_DAY_WITHOUT_CANDLES))
        elif not entry.is_trading_day and has_candles:
            mismatches.append(ScheduleMismatch(day=entry.day, kind=CANDLES_WITHOUT_TRADING_DAY))
    return mismatches


def structural_rule(
    microstructure: Sequence[TickerMicrostructure],
    mismatches: Sequence[ScheduleMismatch],
    *,
    thresholds: WeekendThresholds,
) -> str | None:
    """Branch 2 as a concrete rule, or `None` when it does not fire.

    The answer names the criterion and the tickers, so the follow-up ADR can state
    the rule without making a new decision after the number.
    """
    by_schedule = sorted(
        {item.day for item in mismatches if item.kind == CANDLES_WITHOUT_TRADING_DAY}
    )
    by_volume = sorted(
        item.ticker
        for item in microstructure
        if item.weekend.bars > 0
        and item.weekend.zero_volume_share > thresholds.max_zero_volume_share
    )
    if not by_schedule and not by_volume:
        return None
    parts: list[str] = []
    if by_schedule:
        days = ", ".join(day.isoformat() for day in by_schedule[:5])
        more = "" if len(by_schedule) <= 5 else f" (+{len(by_schedule) - 5} more)"
        parts.append(f"drop bars on days the broker marks as non-trading: {days}{more}")
    if by_volume:
        parts.append(
            "drop bars of tickers whose zero-volume weekend share exceeds "
            f"{thresholds.max_zero_volume_share:g}: {', '.join(by_volume)}"
        )
    return "; ".join(parts)
