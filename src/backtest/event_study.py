"""Event-study power: can the sample see the edge an event idea needs?

`evaluate` measures power from the strategy's own interval, so the strategy must
already exist and run. An event idea has a countable number of events, and the
question "is this worth a journal slot" can be answered before the first line of
strategy code:

- count eligible events (entry bar inside history, ex-bar inside the unsealed
  period, declaration known before the entry);
- measure the **unconditional** dispersion of the outcome variable, i.e. the
  spread of all rolling window-length returns of the tickers. This does not use
  the windows the idea would trade, so no strategy result enters the number;
- compute the MDE from `n` and `sigma`.

The result is a pre-check, not a verdict: it says whether the sample can separate
the target edge from zero at the configured power. A design whose MDE exceeds the
target must change (wider universe, excess return instead of raw return) before a
slot is spent.

Deliberately absent: any TP/SL, any entry rule, any PnL. Those belong to the
strategy and the simulator.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, stdev

from src.backtest.dividends import (
    DEFAULT_T1_SETTLEMENT_SWITCH,
    DividendEvent,
    find_ex_bar,
    ordered_candles,
)
from src.market_data.models import Candle

# Eligibility outcomes that keep an event out of the sample. They are counters,
# not errors: the report must show why the sample is smaller than the calendar.
OUT_OF_HISTORY = "out_of_history"
FUTURE = "future"
SEALED = "sealed"
DECLARED_AFTER_ENTRY = "declared_after_entry"
NO_ENTRY_BAR = "no_entry_bar"


@dataclass(frozen=True)
class EventWindow:
    """One event placed on the entry and exit bars, without any PnL.

    `entry_index` and `exit_index` are positions in the ordered candle list of
    the ticker. The window is inclusive of both bars, so `window_sessions = 10`
    means the return from the close of `D-10` to the close of `D`.
    """

    ticker: str
    last_buy_date: date
    declared_date: date | None
    entry_time: datetime
    exit_time: datetime
    entry_index: int
    exit_index: int
    status: str

    @property
    def declaration_known(self) -> bool:
        return self.declared_date is not None


@dataclass(frozen=True)
class WindowScan:
    """Eligible windows plus the counters that explain the exclusions."""

    windows: tuple[EventWindow, ...] = ()
    total_events: int = 0
    out_of_history: int = 0
    future: int = 0
    sealed: int = 0
    declared_after_entry: int = 0
    missing_declared_date: int = 0

    @property
    def events(self) -> int:
        return len(self.windows)

    def per_ticker(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for window in self.windows:
            counts[window.ticker] = counts.get(window.ticker, 0) + 1
        return dict(sorted(counts.items()))

    def describe(self) -> dict:
        return {
            "events": self.events,
            "total_events": self.total_events,
            "per_ticker": self.per_ticker(),
            "out_of_history": self.out_of_history,
            "future": self.future,
            "sealed": self.sealed,
            "declared_after_entry": self.declared_after_entry,
            "missing_declared_date": self.missing_declared_date,
        }


def scan_event_windows(
    candles_by_ticker: Mapping[str, Sequence[Candle]],
    events: Sequence[DividendEvent],
    *,
    window_sessions: int,
    sealed_start: date,
    t1_settlement_switch: date = DEFAULT_T1_SETTLEMENT_SWITCH,
    include_not_paid: bool = True,
) -> WindowScan:
    """Place every event on an entry and an exit bar and keep the eligible ones.

    The exit is the last session with the dividend (`D`), the bar before the
    ex-bar; the entry is `window_sessions` sessions earlier. Both come from the
    candle series, not from the calendar, so weekends and holidays are respected.

    Filters, fixed before the run:

    - an event before the first candle or after the last one is excluded and
      counted, not silently dropped;
    - an event whose ex-bar is at or after `sealed_start` is excluded: the sealed
      period must not influence even a pre-check;
    - `declared_date > entry date` is look-ahead and the event is excluded. When
      the calendar has no `declared_date` (v3 does not), the event is kept and
      counted in `missing_declared_date`: excluding all of them would leave an
      empty sample, and the report says the filter is not applied yet;
    - `status="not_paid"` is kept: the payout was announced before the entry and
      only failed later, so dropping it would be survivorship bias.
    """
    if window_sessions < 1:
        raise ValueError("window_sessions must be >= 1")

    ordered = {
        ticker.upper(): ordered_candles(candles) for ticker, candles in candles_by_ticker.items()
    }
    windows: list[EventWindow] = []
    counters = {
        OUT_OF_HISTORY: 0,
        FUTURE: 0,
        SEALED: 0,
        DECLARED_AFTER_ENTRY: 0,
    }
    missing_declared = 0

    for event in events:
        if event.status == "not_paid" and not include_not_paid:
            continue
        series = ordered.get(event.ticker.upper())
        if not series:
            counters[OUT_OF_HISTORY] += 1
            continue

        found = find_ex_bar(
            series, event.last_buy_date, t1_settlement_switch=t1_settlement_switch
        )
        if found is None:
            last_date = series[-1].open_time.date()
            counters[FUTURE if event.last_buy_date >= last_date else OUT_OF_HISTORY] += 1
            continue

        previous, ex_bar = found
        exit_index = _index_of(series, previous)
        if exit_index is None:
            counters[OUT_OF_HISTORY] += 1
            continue
        entry_index = exit_index - window_sessions
        if entry_index < 0:
            counters[OUT_OF_HISTORY] += 1
            continue
        if ex_bar.open_time.date() >= sealed_start:
            counters[SEALED] += 1
            continue

        entry_time = series[entry_index].open_time
        if event.declared_date is not None and event.declared_date > entry_time.date():
            counters[DECLARED_AFTER_ENTRY] += 1
            continue
        if event.declared_date is None:
            missing_declared += 1

        windows.append(
            EventWindow(
                ticker=event.ticker.upper(),
                last_buy_date=event.last_buy_date,
                declared_date=event.declared_date,
                entry_time=entry_time,
                exit_time=previous.open_time,
                entry_index=entry_index,
                exit_index=exit_index,
                status=event.status,
            )
        )

    return WindowScan(
        windows=tuple(windows),
        total_events=len(events),
        out_of_history=counters[OUT_OF_HISTORY],
        future=counters[FUTURE],
        sealed=counters[SEALED],
        declared_after_entry=counters[DECLARED_AFTER_ENTRY],
        missing_declared_date=missing_declared,
    )


def _index_of(series: Sequence[Candle], candle: Candle) -> int | None:
    """Index of the exact bar in the ordered series."""
    for index, item in enumerate(series):
        if item.open_time == candle.open_time:
            return index
    return None


def window_return_bps(series: Sequence[Candle], window: EventWindow) -> float | None:
    """Return from the close of the entry bar to the close of the exit bar."""
    if not 0 <= window.entry_index <= window.exit_index < len(series):
        return None
    entry_close = series[window.entry_index].close
    exit_close = series[window.exit_index].close
    if entry_close <= 0 or exit_close <= 0:
        return None
    return (exit_close / entry_close - 1.0) * 10_000.0


def rolling_returns_bps(series: Sequence[Candle], window_sessions: int) -> list[float]:
    """All overlapping `window_sessions`-bar returns of one ticker, in bps."""
    if window_sessions < 1:
        raise ValueError("window_sessions must be >= 1")
    returns: list[float] = []
    for index in range(len(series) - window_sessions):
        first = series[index].close
        last = series[index + window_sessions].close
        if first <= 0 or last <= 0:
            continue
        returns.append((last / first - 1.0) * 10_000.0)
    return returns


@dataclass(frozen=True)
class SigmaEstimate:
    """Unconditional dispersion of the outcome variable, per ticker and pooled."""

    sigma_bps: float | None
    windows: int
    per_ticker: Mapping[str, float] = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "sigma_bps": round(self.sigma_bps, 2) if self.sigma_bps is not None else None,
            "windows": self.windows,
            "per_ticker": {key: round(value, 2) for key, value in self.per_ticker.items()},
        }


def unconditional_sigma(
    candles_by_ticker: Mapping[str, Sequence[Candle]], window_sessions: int
) -> SigmaEstimate:
    """Pooled standard deviation of all rolling window returns.

    Overlapping windows are not independent observations, but the standard
    deviation of the return distribution is still estimated without bias; only
    the event count enters the MDE as a sample size, and the report says that
    overlapping events are an upper bound of independent trades.
    """
    pooled: list[float] = []
    per_ticker: dict[str, float] = {}
    for ticker, candles in candles_by_ticker.items():
        series = ordered_candles(candles)
        returns = rolling_returns_bps(series, window_sessions)
        if len(returns) < 2:
            continue
        per_ticker[ticker.upper()] = stdev(returns)
        pooled.extend(returns)
    sigma = stdev(pooled) if len(pooled) >= 2 else None
    return SigmaEstimate(sigma_bps=sigma, windows=len(pooled), per_ticker=per_ticker)


def basket_index(
    candles_by_ticker: Mapping[str, Sequence[Candle]],
    *,
    exclude: str | None = None,
) -> dict[date, float]:
    """Equal-weight index of the tickers, normalised to 1.0 at each first close.

    A plain average of prices is meaningless across instruments, so every series
    is divided by its own first close. On a date where some tickers have no bar
    the mean is taken over those that do: the basket rebalances to the available
    set instead of dropping the date. That is an approximation, and the report
    says so; it is good enough to estimate dispersion, not to trade.

    `exclude` drops one ticker. The traded stock must not be part of its own
    benchmark: on a 20-ticker basket its weight is 5%, which shrinks the excess
    return by that share and understates sigma.
    """
    totals = basket_totals(candles_by_ticker)
    index: dict[date, float] = {}
    for day, total in totals.sums.items():
        count = totals.counts[day]
        own = None
        if exclude is not None:
            own = totals.values.get(exclude.upper(), {}).get(day)
        if own is not None and count > 1:
            index[day] = (total - own) / (count - 1)
        elif own is None and count > 0:
            index[day] = total / count
    return index


@dataclass(frozen=True)
class BasketTotals:
    """Per-date sums and counts of normalised closes, so a basket costs O(1).

    Building the whole index once per ticker is quadratic and, on 100 tickers,
    both slow and memory hungry. The same numbers are enough to read the basket
    that excludes any single ticker: `(sum - own) / (count - 1)`.
    """

    values: Mapping[str, Mapping[date, float]] = field(default_factory=dict)
    sums: Mapping[date, float] = field(default_factory=dict)
    counts: Mapping[date, int] = field(default_factory=dict)

    def level(self, day: date, *, exclude: str | None = None) -> float | None:
        """Basket level on `day`, optionally without one ticker."""
        count = self.counts.get(day)
        total = self.sums.get(day)
        if not count or total is None:
            return None
        if exclude is None:
            return total / count
        own = self.values.get(exclude.upper(), {}).get(day)
        if own is None:
            return total / count
        if count < 2:
            return None
        return (total - own) / (count - 1)


def basket_totals(candles_by_ticker: Mapping[str, Sequence[Candle]]) -> BasketTotals:
    """Normalise every series to its first close and sum it per date."""
    values: dict[str, dict[date, float]] = {}
    for ticker, candles in candles_by_ticker.items():
        series = [candle for candle in ordered_candles(candles) if candle.close > 0]
        if not series:
            continue
        base = series[0].close
        values[ticker.upper()] = {
            candle.open_time.date(): candle.close / base for candle in series
        }
    sums: dict[date, float] = {}
    counts: dict[date, int] = {}
    for normalised in values.values():
        for day, value in normalised.items():
            sums[day] = sums.get(day, 0.0) + value
            counts[day] = counts.get(day, 0) + 1
    return BasketTotals(values=values, sums=sums, counts=counts)


def unconditional_excess_sigma(
    candles_by_ticker: Mapping[str, Sequence[Candle]], window_sessions: int
) -> SigmaEstimate:
    """Dispersion of the window return net of the equal-weight basket.

    This is the design option "measure the signal as an excess return": the
    market move is removed, so the sample needs fewer events for the same edge.
    Each ticker is compared with the basket of the **other** tickers; a ticker
    with no peers falls back to its raw return.
    """
    if window_sessions < 1:
        raise ValueError("window_sessions must be >= 1")

    totals = basket_totals(candles_by_ticker)
    pooled: list[float] = []
    per_ticker: dict[str, float] = {}
    for ticker, candles in candles_by_ticker.items():
        series = [candle for candle in ordered_candles(candles) if candle.close > 0]
        returns: list[float] = []
        for position in range(len(series) - window_sessions):
            start = series[position]
            end = series[position + window_sessions]
            own = (end.close / start.close - 1.0) * 10_000.0
            returns.append(own - _basket_move(totals, ticker, start, end))
        if len(returns) < 2:
            continue
        per_ticker[ticker.upper()] = stdev(returns)
        pooled.extend(returns)
    sigma = stdev(pooled) if len(pooled) >= 2 else None
    return SigmaEstimate(sigma_bps=sigma, windows=len(pooled), per_ticker=per_ticker)


def _basket_move(
    totals: BasketTotals, ticker: str, start: Candle, end: Candle
) -> float:
    """Basket return over two bars, in bps; zero when the basket is missing."""
    start_level = totals.level(start.open_time.date(), exclude=ticker)
    end_level = totals.level(end.open_time.date(), exclude=ticker)
    if start_level is None or end_level is None or start_level <= 0:
        return 0.0
    return (end_level / start_level - 1.0) * 10_000.0


def window_excess_bps(
    series: Sequence[Candle], window: EventWindow, totals: BasketTotals
) -> float | None:
    """Window return net of the basket of the other tickers, in bps."""
    own = window_return_bps(series, window)
    if own is None:
        return None
    start = series[window.entry_index]
    end = series[window.exit_index]
    return own - _basket_move(totals, window.ticker, start, end)


# ── clustering ─────────────────────────────────────────────────────────────

CLUSTER_BY_QUARTER = "quarter"
CLUSTER_BY_MONTH = "month"
CLUSTER_BY_YEAR = "year"


def cluster_key(day: date, by: str = CLUSTER_BY_QUARTER) -> tuple[int, int]:
    """Season bucket of an event: `(year, quarter)`, `(year, month)` or `(year, 0)`.

    Dividends cluster by season — most issuers go ex in June-July — so events in
    the same bucket share the market state of that season and are not independent
    observations. The bucket is the unit of the clustered MDE (ADR-033).
    """
    if by == CLUSTER_BY_QUARTER:
        return day.year, (day.month - 1) // 3 + 1
    if by == CLUSTER_BY_MONTH:
        return day.year, day.month
    if by == CLUSTER_BY_YEAR:
        return day.year, 0
    raise ValueError(f"unknown cluster unit: {by!r}")


def cluster_count(
    windows: Sequence[EventWindow], by: str = CLUSTER_BY_QUARTER
) -> int:
    """Number of distinct season buckets the events fall into."""
    return len({cluster_key(window.exit_time.date(), by) for window in windows})


def cluster_design_effect(events: int, clusters: int) -> float:
    """`events / clusters`: how much the iid sample size overstates the truth."""
    if clusters < 1 or events < 1:
        return 1.0
    return events / clusters


def median_sigma_bps(sigma: SigmaEstimate) -> float | None:
    """Median per-ticker sigma; a robust cross-check on the pooled number."""
    values = list(sigma.per_ticker.values())
    if not values:
        return None
    values.sort()
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def standard_error_bps(sigma_bps: float | None, trades: int) -> float | None:
    """SE of the mean per-trade effect for `trades` independent observations."""
    if sigma_bps is None or sigma_bps <= 0 or trades < 2:
        return None
    return sigma_bps / math.sqrt(trades)


# ── placebo windows ────────────────────────────────────────────────────────

PLACEBO_MIN_SHIFT = 20
PLACEBO_MAX_SHIFT = 60
DEFAULT_PLACEBO_SEED = 20261109
PLACEBO_STATUS = "placebo"


@dataclass(frozen=True)
class PlaceboWindows:
    """Windows shifted off the real dates, plus the ones that could not be placed."""

    windows: tuple[EventWindow, ...] = ()
    skipped: int = 0
    min_shift: int = PLACEBO_MIN_SHIFT
    max_shift: int = PLACEBO_MAX_SHIFT
    beyond_max_shift: int = 0
    seed: int = DEFAULT_PLACEBO_SEED

    @property
    def events(self) -> int:
        return len(self.windows)

    def describe(self) -> dict:
        return {
            "events": self.events,
            "skipped": self.skipped,
            "min_shift_sessions": self.min_shift,
            "max_shift_sessions": self.max_shift,
            "beyond_max_shift": self.beyond_max_shift,
            "seed": self.seed,
        }


def placebo_windows(
    candles_by_ticker: Mapping[str, Sequence[Candle]],
    windows: Sequence[EventWindow],
    *,
    window_sessions: int,
    sealed_start: date,
    min_shift: int = PLACEBO_MIN_SHIFT,
    max_shift: int = PLACEBO_MAX_SHIFT,
    seed: int = DEFAULT_PLACEBO_SEED,
) -> PlaceboWindows:
    """Move every real window to a random date at least `min_shift` sessions away.

    The placebo keeps the calendar structure: the shift is applied to the real
    exit date and is usually small (20-60 sessions), so a June event stays in the
    same season and the cluster sizes and the shared market state of those weeks
    are the ones a wider universe will have. A uniform date over the whole history
    would not: real events pile up in June-July, and the dependence between them
    is a property of those weeks.

    `|shift| >= min_shift` keeps a placebo window from touching its real window;
    with `window_sessions` bars on each side, 20 sessions leave a gap for a
    10-session window. A window with no legal shift inside `[min_shift, max_shift]`
    falls back to a wider shift and is counted in `beyond_max_shift`: the season
    is then no longer preserved, and the report must say how often that happens.
    A window whose legal shifts all fall outside the history or into the sealed
    period is skipped and counted, never moved silently.

    The generator is seeded per ticker, so the same seed and the same input give
    the same placebo sample, whatever the order of the events.
    """
    if window_sessions < 1:
        raise ValueError("window_sessions must be >= 1")
    if min_shift < 1:
        raise ValueError("min_shift must be >= 1")
    if max_shift < min_shift:
        raise ValueError("max_shift must be >= min_shift")

    ordered = {
        ticker.upper(): ordered_candles(candles) for ticker, candles in candles_by_ticker.items()
    }
    rngs: dict[str, random.Random] = {}
    placed: list[EventWindow] = []
    skipped = 0
    beyond = 0

    for window in windows:
        ticker = window.ticker.upper()
        series = ordered.get(ticker)
        if not series:
            skipped += 1
            continue
        rng = rngs.setdefault(ticker, random.Random(f"{seed}:{ticker}"))
        legal = [
            shift
            for shift in range(-len(series) + 1, len(series))
            if abs(shift) >= min_shift
            and window.exit_index + shift - window_sessions >= 0
            and window.exit_index + shift < len(series)
            and series[window.exit_index + shift].open_time.date() < sealed_start
        ]
        if not legal:
            skipped += 1
            continue
        near = [shift for shift in legal if abs(shift) <= max_shift]
        if not near:
            beyond += 1
        shift = rng.choice(near or legal)
        exit_index = window.exit_index + shift
        entry_index = exit_index - window_sessions
        placed.append(
            EventWindow(
                ticker=ticker,
                last_buy_date=window.last_buy_date,
                declared_date=window.declared_date,
                entry_time=series[entry_index].open_time,
                exit_time=series[exit_index].open_time,
                entry_index=entry_index,
                exit_index=exit_index,
                status=PLACEBO_STATUS,
            )
        )

    return PlaceboWindows(
        windows=tuple(placed),
        skipped=skipped,
        min_shift=min_shift,
        max_shift=max_shift,
        beyond_max_shift=beyond,
        seed=seed,
    )


# ── intra-cluster correlation ──────────────────────────────────────────────

ICC_LEVELS = (CLUSTER_BY_MONTH, CLUSTER_BY_QUARTER, CLUSTER_BY_YEAR)
# Fixed before the run: above this lag-1 correlation of adjacent cluster means the
# seasons are not independent even when the within-cluster ICC is small, so the
# gate moves one level coarser (ADR-033).
ICC_MERGE_LAG1 = 0.3
# Below this many clusters the lag-1 correlation is estimated from a handful of
# points and must not be read as a signal (the year level has about seven).
ICC_MIN_CLUSTERS_FOR_LAG1 = 10
DEFAULT_ICC_BOOTSTRAPS = 2000


def cluster_groups(
    windows: Sequence[EventWindow], values: Sequence[float], by: str
) -> list[list[float]]:
    """Values grouped by season bucket, in chronological order."""
    if len(windows) != len(values):
        raise ValueError("windows and values must have the same length")
    buckets: dict[tuple[int, int], list[float]] = {}
    for window, value in zip(windows, values, strict=True):
        buckets.setdefault(cluster_key(window.exit_time.date(), by), []).append(value)
    return [buckets[key] for key in sorted(buckets)]


def cluster_means(groups: Sequence[Sequence[float]]) -> list[float]:
    """Mean outcome of every season bucket, in chronological order.

    The means are only a dependence diagnostic (lag-1 autocorrelation) and are
    never printed as a result: the pre-check must not show what the windows
    returned (ADR-033).
    """
    return [fmean(group) for group in groups]


def lag1_autocorrelation(values: Sequence[float]) -> float | None:
    """Pearson correlation between adjacent values; `None` for a short series."""
    if len(values) < 3:
        return None
    left = list(values[:-1])
    right = list(values[1:])
    mean_left = fmean(left)
    mean_right = fmean(right)
    cov = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)
    if var_left <= 0 or var_right <= 0:
        return None
    return cov / math.sqrt(var_left * var_right)


@dataclass(frozen=True)
class IccEstimate:
    """One-way random-effects ICC on unbalanced clusters."""

    icc: float | None
    clusters: int
    observations: int
    mean_cluster_size: float
    ms_between: float | None = None
    ms_within: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    bootstraps: int = 0

    @property
    def significant(self) -> bool:
        """Whether the cluster bootstrap interval for the ICC stays above zero."""
        return self.ci_low is not None and self.ci_low > 0.0

    @property
    def design_effect_value(self) -> float:
        """`1 + (m - 1) * ICC`: how much the event count overstates the sample."""
        if self.icc is None:
            return 1.0
        return max(1.0, 1.0 + (self.mean_cluster_size - 1.0) * max(0.0, self.icc))

    def describe(self) -> dict:
        return {
            "icc": _round(self.icc),
            "ci_low": _round(self.ci_low),
            "ci_high": _round(self.ci_high),
            "clusters": self.clusters,
            "observations": self.observations,
            "mean_cluster_size": _round(self.mean_cluster_size),
            "ms_between": _round(self.ms_between),
            "ms_within": _round(self.ms_within),
            "bootstraps": self.bootstraps,
            "significant": self.significant,
            "design_effect": _round(self.design_effect_value),
        }


def icc_oneway(groups: Sequence[Sequence[float]]) -> IccEstimate:
    """Intra-cluster correlation of a one-way random-effects model.

    `ICC = (MSB - MSW) / (MSB + (m0 - 1) * MSW)` with `m0` the balanced-design
    cluster size, so unequal cluster sizes do not bias the estimate. A negative
    estimate is returned as is and only clamped when it enters a design effect:
    negative correlation is not a reason to shrink the sample.
    """
    sizes = [len(group) for group in groups]
    clusters = len(groups)
    observations = sum(sizes)
    if clusters < 2 or observations - clusters < 1:
        return IccEstimate(
            icc=None,
            clusters=clusters,
            observations=observations,
            mean_cluster_size=observations / clusters if clusters else 0.0,
        )

    grand = fmean(value for group in groups for value in group)
    ss_between = sum(
        size * (fmean(group) - grand) ** 2
        for group, size in zip(groups, sizes, strict=True)
    )
    ss_within = sum(
        sum((value - fmean(group)) ** 2 for value in group) for group in groups
    )
    ms_between = ss_between / (clusters - 1)
    ms_within = ss_within / (observations - clusters)
    m0 = (observations - sum(size * size for size in sizes) / observations) / (
        clusters - 1
    )
    denominator = ms_between + (m0 - 1.0) * ms_within
    icc = (ms_between - ms_within) / denominator if denominator > 0 else 0.0
    return IccEstimate(
        icc=icc,
        clusters=clusters,
        observations=observations,
        mean_cluster_size=observations / clusters,
        ms_between=ms_between,
        ms_within=ms_within,
    )


def icc_bootstrap_ci(
    groups: Sequence[Sequence[float]],
    *,
    samples: int = DEFAULT_ICC_BOOTSTRAPS,
    alpha: float = 0.05,
    seed: int = DEFAULT_PLACEBO_SEED,
) -> tuple[float | None, float | None]:
    """Percentile interval for the ICC, resampling whole clusters.

    Clusters are the independent unit: resampling observations would break the
    dependence the ICC is supposed to measure. The resample keeps the cluster
    sizes it draws, so the interval also reflects the unequal season sizes.
    """
    if samples < 100:
        raise ValueError("samples must be >= 100")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if len(groups) < 3:
        return None, None

    rng = random.Random(f"{seed}:icc")
    clusters = len(groups)
    estimates: list[float] = []
    for _ in range(samples):
        drawn = [groups[rng.randrange(clusters)] for _ in range(clusters)]
        estimate = icc_oneway(drawn).icc
        if estimate is not None:
            estimates.append(estimate)
    if len(estimates) < 100:
        return None, None
    estimates.sort()
    low_index = max(0, int(alpha / 2.0 * len(estimates)))
    high_index = min(len(estimates) - 1, int((1.0 - alpha / 2.0) * len(estimates)))
    return estimates[low_index], estimates[high_index]


@dataclass(frozen=True)
class ClusterLevel:
    """ICC and adjacent-season correlation at one bucketing of the events."""

    by: str
    icc: IccEstimate
    lag1: float | None = None

    @property
    def clusters(self) -> int:
        return self.icc.clusters

    @property
    def significant(self) -> bool:
        return self.icc.significant

    @property
    def lag1_interpretable(self) -> bool:
        """Too few clusters make the lag-1 estimate noise rather than a signal."""
        return self.clusters >= ICC_MIN_CLUSTERS_FOR_LAG1

    def describe(self) -> dict:
        return {
            "by": self.by,
            "clusters": self.clusters,
            "lag1": _round(self.lag1),
            "lag1_interpretable": self.lag1_interpretable,
            "icc": self.icc.describe(),
        }


@dataclass(frozen=True)
class ClusterDecision:
    """The level the gate uses, and why it is that level."""

    level: str
    reason: str
    merged: bool = False

    def describe(self) -> dict:
        return {"level": self.level, "reason": self.reason, "merged": self.merged}


def choose_cluster_level(
    levels: Sequence[ClusterLevel], merge_lag1: float = ICC_MERGE_LAG1
) -> ClusterDecision:
    """Coarsest level with an ICC interval above zero, then merge adjacent seasons.

    The rule is fixed before the run (ADR-033):

    1. the gate uses the **coarsest** level whose bootstrap interval for the ICC
       excludes zero. Clustering on the coarsest plausible scale is the safe
       direction: a finer bucket understates the variance when a common shock
       lives longer than that bucket;
    2. if the lag-1 autocorrelation of the cluster means is at or above
       `merge_lag1`, adjacent clusters still share a shock, so the gate moves one
       level coarser (month -> quarter -> year) even when the finer ICC is
       significant;
    3. when no level is significant, dependence is negligible, the finest bucket
       is used and the effective sample stays close to the event count.

    The year level has about seven clusters, so its interval is wide and it
    rarely becomes significant; in practice the choice is between month and
    quarter.
    """
    by_name = {level.by: level for level in levels}
    order = (CLUSTER_BY_YEAR, CLUSTER_BY_QUARTER, CLUSTER_BY_MONTH)
    significant = [
        name for name in order if by_name.get(name) and by_name[name].significant
    ]

    if not significant:
        return ClusterDecision(
            level=CLUSTER_BY_MONTH,
            reason=(
                "no level has an ICC interval above zero: dependence is negligible "
                "and the finest bucket keeps the effective sample near the event count"
            ),
        )

    chosen = significant[0]
    merged = False
    coarser = {
        CLUSTER_BY_MONTH: CLUSTER_BY_QUARTER,
        CLUSTER_BY_QUARTER: CLUSTER_BY_YEAR,
        CLUSTER_BY_YEAR: None,
    }
    while True:
        level = by_name.get(chosen)
        if level is None or level.lag1 is None or level.lag1 < merge_lag1:
            break
        if not level.lag1_interpretable:
            break
        next_level = coarser[chosen]
        if next_level is None:
            break
        chosen = next_level
        merged = True

    reason = f"coarsest level with an ICC interval above zero ({significant[0]})"
    if merged:
        reason += (
            f"; moved to {chosen}: lag-1 autocorrelation of adjacent cluster means "
            f"is at or above {merge_lag1:g}"
        )
    return ClusterDecision(level=chosen, reason=reason, merged=merged)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


# ── event universe fixed before the run ────────────────────────────────────

DEFAULT_POWER_UNIVERSE_PATH = "config/power_universe.json"


@dataclass(frozen=True)
class PowerUniverse:
    """A ticker list frozen before the first real run, with its digest.

    The universe of an event idea is chosen by a rule that must be written down
    before any result is seen: picking tickers after the fact is a selection
    bias, and changing the list after a run is a new attempt, not a fix. The
    digest is stored in the file and verified on load, so an edited list that
    was not re-hashed is refused instead of silently becoming a new universe.
    """

    version: str
    rule: str
    tickers: tuple[str, ...]
    sha256: str
    created: str = ""

    def describe(self) -> dict:
        return {
            "version": self.version,
            "rule": self.rule,
            "created": self.created,
            "tickers": len(self.tickers),
            "sha256": self.sha256,
        }


def universe_sha256(tickers: Sequence[str]) -> str:
    """Digest of the normalised list: upper case, sorted, one ticker a line."""
    normalised = "\n".join(sorted({ticker.strip().upper() for ticker in tickers}))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def load_power_universe(path: str | Path) -> PowerUniverse:
    """Read the frozen universe and refuse a list that does not match its hash."""
    payload = json.loads(Path(path).read_bytes())
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected an object")
    tickers = payload.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError(f"{path}: 'tickers' must be a non-empty list")
    stored = str(payload.get("sha256", ""))
    actual = universe_sha256([str(ticker) for ticker in tickers])
    if not stored:
        raise ValueError(f"{path}: 'sha256' is missing; freeze the list before a run")
    if stored != actual:
        raise ValueError(
            f"{path}: the ticker list does not match its sha256 ({stored[:12]} != "
            f"{actual[:12]}). A changed list is a new attempt: update the hash and "
            "record the attempt in the journal."
        )
    return PowerUniverse(
        version=str(payload.get("version", "")),
        rule=str(payload.get("rule", "")),
        tickers=tuple(str(ticker).upper() for ticker in tickers),
        sha256=stored,
        created=str(payload.get("created", "")),
    )
