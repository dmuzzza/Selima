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

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
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
    normalised: dict[str, dict[date, float]] = {}
    for ticker, candles in candles_by_ticker.items():
        if exclude is not None and ticker.upper() == exclude.upper():
            continue
        series = [candle for candle in ordered_candles(candles) if candle.close > 0]
        if not series:
            continue
        base = series[0].close
        normalised[ticker.upper()] = {
            candle.open_time.date(): candle.close / base for candle in series
        }
    dates = sorted({day for values in normalised.values() for day in values})
    index: dict[date, float] = {}
    for day in dates:
        values = [series[day] for series in normalised.values() if day in series]
        if values:
            index[day] = fmean(values)
    return index


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

    pooled: list[float] = []
    per_ticker: dict[str, float] = {}
    for ticker, candles in candles_by_ticker.items():
        index = basket_index(candles_by_ticker, exclude=ticker)
        series = [candle for candle in ordered_candles(candles) if candle.close > 0]
        returns: list[float] = []
        for position in range(len(series) - window_sessions):
            start = series[position]
            end = series[position + window_sessions]
            start_day = start.open_time.date()
            end_day = end.open_time.date()
            own = (end.close / start.close - 1.0) * 10_000.0
            if start_day not in index or end_day not in index:
                returns.append(own)
                continue
            basket = (index[end_day] / index[start_day] - 1.0) * 10_000.0
            returns.append(own - basket)
        if len(returns) < 2:
            continue
        per_ticker[ticker.upper()] = stdev(returns)
        pooled.extend(returns)
    sigma = stdev(pooled) if len(pooled) >= 2 else None
    return SigmaEstimate(sigma_bps=sigma, windows=len(pooled), per_ticker=per_ticker)


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
