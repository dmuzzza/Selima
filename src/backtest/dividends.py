"""Dividend calendar: mark trades that span an ex-dividend bar (T2-22, ADR-025).

The model is deliberately narrow:

- **no cash flow and no tax**: a dividend is not added to `pnl_net`. A trade is
  only *marked* `dividend_affected`, so a report can show totals with and
  without such trades. Modelling the payment needs a gross/net decision that the
  data does not settle (ADR-025);
- **no level changes**: the set of trades must be identical with and without the
  calendar. The calendar explains a phantom gap, it does not rewrite candles;
- **ex-bar rule**: the first trading bar strictly after `last_buy_date`, weekend
  bars included. `last_buy_date` is the last day to buy and still be in the
  registry (T+1 settlement), so the price drop lands on the next bar;
- **a drop is required**: an event whose ex-bar does not fall by at least
  `gap_match_ratio` of the expected dividend gap is `no_gap_match` and is not
  applied. A price rise never counts as a match, however large.

`dividend_net` in the T-Invest API is the announced (gross) amount per share:
SBER 2024 came back as 33.30 for a declared 33.30 RUB dividend. The field name is
misleading, so the config stores it as `dividend_gross`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

logger = logging.getLogger(__name__)

DEFAULT_DIVIDENDS_PATH = Path(__file__).resolve().parents[2] / "config" / "dividends.json"
DEFAULT_VALIDATION_PATH = Path(__file__).resolve().parents[2] / "config" / "validation.json"
DEFAULT_GAP_MATCH_RATIO = 0.3

# Event statuses. Only `applied` marks trades.
APPLIED = "applied"
NO_GAP_MATCH = "no_gap_match"
NO_CANDLES = "no_candles"
NO_PREV_CLOSE = "no_prev_close"
OUT_OF_HISTORY = "out_of_history"
FUTURE = "future"
NOT_PAID = "not_paid"

VALID_SOURCES = frozenset({"api", "manual"})
# Declared but not paid (a meeting voted the payout down) and paid events.
VALID_STATUSES = frozenset({"active", "not_paid"})


# ── configuration ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DividendEvent:
    """One declared payment: the amount per share and the last day to buy it.

    `payouts` keeps the individual amounts when two payouts share one ex-date
    (for example CHMF 2024-06-17: 191.51 for 2023 and 38.30 for Q1 2024). The
    total is `dividend_gross`; the split is kept for tracing, not for the gap
    check, which compares the total with the observed drop.

    `status` is `active` for a paid event and `not_paid` for a declared payout
    that shareholders rejected: the API returns it, the market never paid it.
    """

    ticker: str
    last_buy_date: date
    dividend_gross: float
    source: str = "api"
    record_date: date | None = None
    reference: str | None = None
    status: str = "active"
    payouts: tuple[float, ...] = ()
    note: str | None = None

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "last_buy_date": self.last_buy_date.isoformat(),
            "record_date": self.record_date.isoformat() if self.record_date else None,
            "dividend_gross": self.dividend_gross,
            "source": self.source,
            "reference": self.reference,
        }


@dataclass(frozen=True)
class DividendCalendar:
    """Parsed config/dividends.json with its fingerprint."""

    version: str
    sha256: str
    source: str
    events: tuple[DividendEvent, ...]

    def for_ticker(self, ticker: str) -> tuple[DividendEvent, ...]:
        key = ticker.upper()
        return tuple(event for event in self.events if event.ticker == key)

    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted({event.ticker for event in self.events}))

    def describe(self) -> dict:
        return {
            "dividends_version": f"{self.version}+{self.sha256[:12]}",
            "dividends_source": self.source,
            "dividends_events": len(self.events),
            "dividends_tickers": list(self.tickers()),
        }


def _parse_event(
    raw: Mapping[str, object],
    path: Path,
    index: int,
    annotations: Mapping[str, Mapping[str, object]] | None = None,
) -> DividendEvent:
    where = f"{path}: events[{index}]"
    merged = dict(raw)
    # The `annotations` layer is the hand-written part that survives a rewrite by
    # scripts/fetch_dividends.py; it is keyed by "TICKER|last_buy_date".
    key = f"{str(raw.get('ticker') or '').strip().upper()}|{raw.get('last_buy_date')}"
    annotation = (annotations or {}).get(key) or {}
    for field in ("status", "note", "reference"):
        if annotation.get(field) and not merged.get(field):
            merged[field] = annotation[field]
    raw = merged

    ticker = str(raw.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(f"{where}: missing 'ticker'")

    last_buy_raw = raw.get("last_buy_date")
    if not last_buy_raw:
        raise ValueError(f"{where}: missing 'last_buy_date'")
    try:
        last_buy_date = date.fromisoformat(str(last_buy_raw))
    except ValueError as exc:
        raise ValueError(f"{where}: bad 'last_buy_date' {last_buy_raw!r}") from exc

    try:
        dividend_gross = float(raw["dividend_gross"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{where}: missing or non-numeric 'dividend_gross'") from exc
    if dividend_gross <= 0:
        raise ValueError(f"{where}: 'dividend_gross' must be positive")

    source = str(raw.get("source") or "").strip().lower()
    if source not in VALID_SOURCES:
        raise ValueError(f"{where}: 'source' must be one of {sorted(VALID_SOURCES)}")

    reference = raw.get("reference")
    if source == "manual" and not (isinstance(reference, str) and reference.strip()):
        raise ValueError(
            f"{where}: manual event for {ticker} needs a 'reference' to an independent source"
        )

    record_raw = raw.get("record_date")
    record_date: date | None = None
    if record_raw:
        try:
            record_date = date.fromisoformat(str(record_raw))
        except ValueError as exc:
            raise ValueError(f"{where}: bad 'record_date' {record_raw!r}") from exc

    status = str(raw.get("status") or "active").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"{where}: 'status' must be one of {sorted(VALID_STATUSES)}")

    payouts: tuple[float, ...] = ()
    payouts_raw = raw.get("payouts")
    if payouts_raw:
        if not isinstance(payouts_raw, list):
            raise ValueError(f"{where}: 'payouts' must be a list")
        try:
            amounts = tuple(float(item["dividend_gross"]) for item in payouts_raw)  # type: ignore[index]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{where}: bad 'payouts' entry") from exc
        if any(amount <= 0 for amount in amounts):
            raise ValueError(f"{where}: every 'payouts' amount must be positive")
        if abs(sum(amounts) - dividend_gross) > 1e-6:
            raise ValueError(
                f"{where}: 'payouts' sum {sum(amounts)} does not match "
                f"'dividend_gross' {dividend_gross}"
            )
        payouts = amounts

    note = raw.get("note")
    return DividendEvent(
        ticker=ticker,
        last_buy_date=last_buy_date,
        dividend_gross=dividend_gross,
        source=source,
        record_date=record_date,
        reference=str(reference) if reference else None,
        status=status,
        payouts=payouts,
        note=str(note) if note else None,
    )


_cache: dict[Path, DividendCalendar] = {}


def load_dividends(path: Path | str = DEFAULT_DIVIDENDS_PATH) -> DividendCalendar:
    """Read and cache config/dividends.json.

    Raises:
        FileNotFoundError: the file is missing.
        ValueError: invalid JSON, unknown source, non-positive amount, or a
            `manual` event without a reference.
    """
    resolved = Path(path).resolve()
    cached = _cache.get(resolved)
    if cached is not None:
        return cached

    raw = resolved.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {resolved}: {exc}") from exc

    entries = data.get("events")
    if not isinstance(entries, list):
        raise ValueError(f"{resolved} must contain an 'events' array")

    raw_annotations = data.get("annotations") or {}
    if not isinstance(raw_annotations, dict):
        raise ValueError(f"{resolved}: 'annotations' must be an object")
    annotations: dict[str, Mapping[str, object]] = {
        str(key): value
        for key, value in raw_annotations.items()
        if isinstance(value, Mapping)
    }

    events = tuple(
        _parse_event(entry, resolved, index, annotations)
        for index, entry in enumerate(entries)
    )

    # Fingerprint the payload, not the bytes: reformatting must not change the
    # version, but any value must.
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    calendar = DividendCalendar(
        version=str(data.get("version", "0")),
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        source=str(resolved),
        events=events,
    )
    _cache[resolved] = calendar
    logger.debug("Loaded dividend calendar %s (version %s)", resolved, calendar.version)
    return calendar


def clear_dividends_cache() -> None:
    """Drop the cache; used by tests that rewrite the config file."""
    _cache.clear()


# ── thresholds ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DividendThresholds:
    """The `dividends` section of config/validation.json."""

    gap_match_ratio: float
    version: str
    model_revision: str
    sha256: str
    source: str

    def describe(self) -> dict:
        return {
            "gap_match_ratio": self.gap_match_ratio,
            "dividends_thresholds": f"{self.version}+{self.sha256[:12]}",
            "model_revision": self.model_revision,
        }


_threshold_cache: dict[Path, DividendThresholds] = {}


def load_dividend_thresholds(
    path: Path | str = DEFAULT_VALIDATION_PATH,
) -> DividendThresholds:
    """Read the `dividends` section of config/validation.json.

    Raises:
        FileNotFoundError: the file is missing.
        ValueError: missing section, non-numeric ratio, or ratio outside (0, 1).
    """
    resolved = Path(path).resolve()
    cached = _threshold_cache.get(resolved)
    if cached is not None:
        return cached

    raw = resolved.read_bytes()
    payload = json.loads(raw)
    section = payload.get("dividends")
    if not isinstance(section, dict):
        raise ValueError(f"{resolved}: missing 'dividends' section")

    ratio = float(section.get("gap_match_ratio", DEFAULT_GAP_MATCH_RATIO))
    if not 0.0 < ratio <= 1.0:
        raise ValueError(f"{resolved}: gap_match_ratio must be in (0, 1], got {ratio}")

    thresholds = DividendThresholds(
        gap_match_ratio=ratio,
        version=str(payload.get("version", "")),
        model_revision=str(payload.get("model_revision", "1")),
        sha256=hashlib.sha256(raw).hexdigest(),
        source=str(resolved),
    )
    _threshold_cache[resolved] = thresholds
    return thresholds


def clear_threshold_cache() -> None:
    """Drop the thresholds cache; used by tests."""
    _threshold_cache.clear()


# ── ex-bar resolution ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedDividend:
    """An event placed on a concrete bar, with the gap it should have caused."""

    ticker: str
    last_buy_date: date
    dividend_gross: float
    status: str
    source: str
    ex_bar_open: datetime | None = None
    prev_close: float | None = None
    expected_gap_percent: float | None = None
    actual_gap_percent: float | None = None
    drop_percent: float | None = None
    weekend_ex_bar: bool = False

    @property
    def applied(self) -> bool:
        return self.status == APPLIED

    @property
    def status_label(self) -> str:
        """Status with the weekend marker attached, for reports and snapshots."""
        return f"{self.status} (weekend_ex_bar)" if self.weekend_ex_bar else self.status

    @property
    def reason(self) -> str:
        """Why the event was accepted or rejected, in one line."""
        if self.status == NOT_PAID:
            return "declared payout was not paid (shareholders voted it down)"
        if self.status == OUT_OF_HISTORY:
            return "event is before the first loaded candle"
        if self.status == FUTURE:
            return "no candle after last_buy_date yet"
        if self.status == NO_CANDLES:
            return "no candle after last_buy_date"
        if self.status == NO_PREV_CLOSE:
            return "previous bar has no positive close"
        if self.expected_gap_percent is None or self.drop_percent is None:
            return ""
        ratio = (
            self.drop_percent / self.expected_gap_percent
            if self.expected_gap_percent
            else 0.0
        )
        return (
            f"drop {self.drop_percent:+.2f}% vs expected "
            f"{self.expected_gap_percent:.2f}% (ratio {ratio:.2f})"
        )

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "last_buy_date": self.last_buy_date.isoformat(),
            "ex_bar_open": self.ex_bar_open.isoformat() if self.ex_bar_open else None,
            "dividend_gross": self.dividend_gross,
            "expected_gap_percent": (
                round(self.expected_gap_percent, 4)
                if self.expected_gap_percent is not None
                else None
            ),
            "actual_gap_percent": (
                round(self.actual_gap_percent, 4)
                if self.actual_gap_percent is not None
                else None
            ),
            "drop_percent": (
                round(self.drop_percent, 4) if self.drop_percent is not None else None
            ),
            "status": self.status,
            "status_label": self.status_label,
            "weekend_ex_bar": self.weekend_ex_bar,
            "reason": self.reason,
            "source": self.source,
        }


def ordered_candles(candles: Sequence[Candle]) -> list[Candle]:
    """Candles sorted by open time; input order is not trusted."""
    return sorted(candles, key=lambda candle: candle.open_time)


def find_ex_bar(candles: Sequence[Candle], last_buy_date: date) -> tuple[Candle, Candle] | None:
    """First bar strictly after `last_buy_date` and the bar before it.

    The comparison is on the calendar date of `open_time` in the candle's own
    timezone (MSK for data loaded from PostgreSQL), so a 00:00 UTC bar is not
    pushed to the previous day. Weekend bars take part: the exchange trades on
    weekends since 2025-03 (T2-23).
    """
    ordered = ordered_candles(candles)
    for index in range(1, len(ordered)):
        if ordered[index].open_time.date() > last_buy_date:
            return ordered[index - 1], ordered[index]
    return None


def resolve_event(
    event: DividendEvent,
    candles: Sequence[Candle],
    *,
    gap_match_ratio: float = DEFAULT_GAP_MATCH_RATIO,
) -> ResolvedDividend:
    """Place one event on a bar and decide whether the price actually dropped."""
    if gap_match_ratio <= 0:
        raise ValueError("gap_match_ratio must be positive")

    ordered = ordered_candles(candles)
    if event.status == "not_paid":
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=NOT_PAID,
            source=event.source,
        )
    if not ordered:
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=NO_CANDLES,
            source=event.source,
        )
    # An event before the first loaded bar cannot be checked: comparing it with the
    # first bars would invent a gap that belongs to the start of the history.
    if event.last_buy_date < ordered[0].open_time.date():
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=OUT_OF_HISTORY,
            source=event.source,
        )
    # No bar after the ex-date yet: the event is known (it matters for live shorts)
    # but cannot be measured.
    if event.last_buy_date >= ordered[-1].open_time.date():
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=FUTURE,
            source=event.source,
        )

    found = find_ex_bar(candles, event.last_buy_date)
    if found is None:
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=NO_CANDLES,
            source=event.source,
        )

    previous, ex_bar = found
    if previous.close <= 0:
        return ResolvedDividend(
            ticker=event.ticker,
            last_buy_date=event.last_buy_date,
            dividend_gross=event.dividend_gross,
            status=NO_PREV_CLOSE,
            source=event.source,
            ex_bar_open=ex_bar.open_time,
        )

    expected = event.dividend_gross / previous.close * 100.0
    actual = (ex_bar.open - previous.close) / previous.close * 100.0
    # Only a fall can confirm an ex-dividend bar; a rise is never a match.
    drop = -actual
    status = APPLIED if drop >= gap_match_ratio * expected else NO_GAP_MATCH
    weekend = ex_bar.open_time.weekday() >= 5

    return ResolvedDividend(
        ticker=event.ticker,
        last_buy_date=event.last_buy_date,
        dividend_gross=event.dividend_gross,
        status=status,
        source=event.source,
        ex_bar_open=ex_bar.open_time,
        prev_close=previous.close,
        expected_gap_percent=expected,
        actual_gap_percent=actual,
        drop_percent=drop,
        weekend_ex_bar=weekend,
    )


def resolve_events(
    calendar: DividendCalendar,
    candles_by_ticker: Mapping[str, Sequence[Candle]],
    *,
    gap_match_ratio: float = DEFAULT_GAP_MATCH_RATIO,
) -> list[ResolvedDividend]:
    """Resolve every event that belongs to a ticker present in the run."""
    resolved: list[ResolvedDividend] = []
    for ticker, candles in candles_by_ticker.items():
        events = calendar.for_ticker(ticker)
        if not events:
            continue
        for event in events:
            resolved.append(
                resolve_event(event, candles, gap_match_ratio=gap_match_ratio)
            )
    return resolved


def applied_ex_bars(resolved: Iterable[ResolvedDividend]) -> dict[str, tuple[datetime, ...]]:
    """Ex-bar open times per ticker, for `applied` events only."""
    bars: dict[str, list[datetime]] = {}
    for item in resolved:
        if item.applied and item.ex_bar_open is not None:
            bars.setdefault(item.ticker, []).append(item.ex_bar_open)
    return {ticker: tuple(sorted(values)) for ticker, values in bars.items()}


# ── trade marking ──────────────────────────────────────────────────────────


def mark_dividend_affected(
    trades: Iterable[Trade], ex_bars: Sequence[datetime]
) -> list[bool]:
    """True when an ex-dividend bar falls inside the holding period.

    Same window rule as `mark_gap_affected`: entry is exclusive, exit is
    inclusive. An open trade has no realized result yet and is never marked.
    """
    moments = tuple(ex_bars)
    flags: list[bool] = []
    for trade in trades:
        if trade.exit_time is None:
            flags.append(False)
            continue
        flags.append(
            any(trade.entry_time < moment <= trade.exit_time for moment in moments)
        )
    return flags


# ── coverage ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UnexplainedGap:
    """A large gap that no applied dividend event accounts for."""

    ticker: str
    open_time: datetime
    gap_percent: float

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "open_time": self.open_time.isoformat(),
            "gap_percent": round(self.gap_percent, 4),
        }


@dataclass(frozen=True)
class DividendCoverage:
    """What the calendar explains and what it does not."""

    years: tuple[int, ...]
    tickers: tuple[str, ...]
    missing_years: dict[str, tuple[int, ...]]
    applied: int
    no_gap_match: int
    other: int
    unexplained_gaps: tuple[UnexplainedGap, ...]
    rejected_events: tuple[ResolvedDividend, ...] = ()
    future: int = 0
    out_of_history: int = 0
    not_paid: int = 0

    def describe(self) -> dict:
        return {
            "years": list(self.years),
            "missing_years": {
                ticker: list(years) for ticker, years in sorted(self.missing_years.items())
            },
            "applied": self.applied,
            "no_gap_match": self.no_gap_match,
            "other": self.other,
            "future": self.future,
            "out_of_history": self.out_of_history,
            "not_paid": self.not_paid,
            "rejected_events": [item.describe() for item in self.rejected_events],
            "unexplained_gaps": [gap.describe() for gap in self.unexplained_gaps],
        }


def years_from_candles(candles_by_ticker: Mapping[str, Sequence[Candle]]) -> tuple[int, ...]:
    """Calendar years covered by the loaded candles."""
    years = {
        candle.open_time.year for candles in candles_by_ticker.values() for candle in candles
    }
    return tuple(sorted(years))


def build_coverage(
    calendar: DividendCalendar,
    resolved: Sequence[ResolvedDividend],
    gaps_by_ticker: Mapping[str, Mapping[datetime, float]],
    *,
    tickers: Sequence[str],
    years: Sequence[int],
    gap_threshold_percent: float,
) -> DividendCoverage:
    """Coverage per ticker/year plus gaps above the threshold that stay unexplained.

    A gap is explained only by an `applied` event: a `no_gap_match` event is by
    definition a bar where the price did not fall as a dividend would.
    """
    if gap_threshold_percent <= 0:
        raise ValueError("gap_threshold_percent must be positive")

    known_years = tuple(sorted(set(years)))
    applied_bars = applied_ex_bars(resolved)

    missing: dict[str, tuple[int, ...]] = {}
    for ticker in tickers:
        with_events = {event.last_buy_date.year for event in calendar.for_ticker(ticker)}
        absent = tuple(year for year in known_years if year not in with_events)
        if absent:
            missing[ticker] = absent

    unexplained: list[UnexplainedGap] = []
    for ticker in tickers:
        explained = set(applied_bars.get(ticker, ()))
        for moment, gap in sorted(gaps_by_ticker.get(ticker, {}).items()):
            if abs(gap) <= gap_threshold_percent:
                continue
            if moment in explained:
                continue
            unexplained.append(
                UnexplainedGap(ticker=ticker, open_time=moment, gap_percent=gap)
            )

    applied = sum(1 for item in resolved if item.status == APPLIED)
    no_gap_match = sum(1 for item in resolved if item.status == NO_GAP_MATCH)
    other = len(resolved) - applied - no_gap_match
    future = sum(1 for item in resolved if item.status == FUTURE)
    out_of_history = sum(1 for item in resolved if item.status == OUT_OF_HISTORY)
    not_paid = sum(1 for item in resolved if item.status == NOT_PAID)

    return DividendCoverage(
        years=known_years,
        tickers=tuple(tickers),
        missing_years=missing,
        applied=applied,
        no_gap_match=no_gap_match,
        other=other,
        unexplained_gaps=tuple(unexplained),
        # `future` and `out_of_history` are reported separately: they were never
        # evaluated, so they are not "rejected" events.
        rejected_events=tuple(
            item
            for item in resolved
            if item.status in (NO_GAP_MATCH, NO_CANDLES, NO_PREV_CLOSE)
        ),
        future=future,
        out_of_history=out_of_history,
        not_paid=not_paid,
    )
