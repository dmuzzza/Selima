"""Append-only hypothesis journal: every evaluation is a recorded attempt.

Why a journal instead of a `--trials` flag: a self-reported counter is easy to
understate, and a new strategy name would reset it. The correction for multiple
testing is only valid if the number of attempts comes from a file that is written
before the result is known (ADR-029).

One line per idea, JSONL, append-only. Two counters, deliberately separate:

- **ideas** — the quarterly budget is spent by entries, 20 per quarter: a heatmap
  of 20 cells is one idea and must not burn the whole quarter;
- **grid_cells** — how many parameter variants were looked at inside the idea;
  informational for the report and the input of the screening correction.

A `kind="legacy"` line records the attempts made before the journal existed: it
counts towards the multiplicity correction but does not spend the idea budget,
because those attempts are history, not a new question to the data.

Every line carries `prev_hash`, the SHA-256 of the previous canonical line, so a
silent rewrite of the file breaks the chain and `load_hypotheses()` refuses it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

DEFAULT_JOURNAL_PATH = Path(__file__).resolve().parents[2] / "journal" / "hypotheses.jsonl"

# Ideas per calendar quarter across all strategies. Twenty keeps the Bonferroni
# multiplier near 1.54 at 1711 trades (MDE ~22 bps), so a 10 bps edge stays
# measurable in principle (ADR-029, docs/EVALUATE_DESIGN.md §6).
# A heatmap is ONE idea here: `grid_cells` does not spend the budget, it feeds
# the screening correction instead.
TRIAL_BUDGET_PER_QUARTER = 20

# `prev_hash` of the first line: no previous line exists.
GENESIS_HASH = "0" * 64

UNIVERSE_SOURCES = ("portfolio", "manual")
# An idea spends the budget; a sealed-access mark records that the sealed period
# was opened and must not be counted as a new idea. A legacy line records the
# attempts made before the journal existed: it counts in `trial_count()` but is
# history, so it does not spend the budget either.
ENTRY_KINDS = ("idea", "sealed", "legacy")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class HypothesisLogError(ValueError):
    """The journal or an entry is not usable as evidence."""


class TrialBudgetExceeded(HypothesisLogError):
    """The quarterly idea budget is spent: close the quarter, do not keep trying."""


@dataclass(frozen=True)
class HypothesisEntry:
    """One recorded attempt."""

    recorded_at: datetime
    hypothesis: str
    strategy: str
    timeframe: str
    tickers: tuple[str, ...]
    universe_source: str
    config_sha256: str
    grid_cells: int = 1
    sealed_access: bool = False
    tag: str = ""
    notes: str = ""
    prev_hash: str = GENESIS_HASH
    kind: str = "idea"

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise HypothesisLogError("recorded_at must be timezone-aware (UTC)")
        for name in ("hypothesis", "strategy", "timeframe"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise HypothesisLogError(f"{name} must be a non-empty string")
        if not self.tickers:
            raise HypothesisLogError("tickers must not be empty")
        for ticker in self.tickers:
            if not isinstance(ticker, str) or not ticker.strip():
                raise HypothesisLogError("every ticker must be a non-empty string")
        if self.universe_source not in UNIVERSE_SOURCES:
            raise HypothesisLogError(
                f"universe_source must be one of {', '.join(UNIVERSE_SOURCES)}, "
                f"got {self.universe_source!r}"
            )
        if not isinstance(self.config_sha256, str) or not _SHA256_RE.match(
            self.config_sha256
        ):
            raise HypothesisLogError("config_sha256 must be a lowercase sha256 hex digest")
        if not isinstance(self.grid_cells, int) or self.grid_cells < 1:
            raise HypothesisLogError(f"grid_cells must be an int >= 1, got {self.grid_cells!r}")
        if not isinstance(self.prev_hash, str) or not _SHA256_RE.match(self.prev_hash):
            raise HypothesisLogError("prev_hash must be a lowercase sha256 hex digest")
        if self.kind not in ENTRY_KINDS:
            raise HypothesisLogError(
                f"kind must be one of {', '.join(ENTRY_KINDS)}, got {self.kind!r}"
            )

    def to_dict(self) -> dict:
        return {
            "recorded_at": self.recorded_at.astimezone(UTC).isoformat(),
            "kind": self.kind,
            "hypothesis": self.hypothesis,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "tickers": list(self.tickers),
            "universe_source": self.universe_source,
            "config_sha256": self.config_sha256,
            "grid_cells": self.grid_cells,
            "sealed_access": self.sealed_access,
            "tag": self.tag,
            "notes": self.notes,
            "prev_hash": self.prev_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> HypothesisEntry:
        try:
            recorded_at = datetime.fromisoformat(str(payload["recorded_at"]))
            tickers = payload["tickers"]
            if not isinstance(tickers, Sequence) or isinstance(tickers, (str, bytes)):
                raise HypothesisLogError("tickers must be a list of strings")
            raw_cells = payload.get("grid_cells", 1)
            if not isinstance(raw_cells, int) or isinstance(raw_cells, bool):
                raise HypothesisLogError("grid_cells must be an integer")
            return cls(
                recorded_at=recorded_at,
                hypothesis=str(payload["hypothesis"]),
                strategy=str(payload["strategy"]),
                timeframe=str(payload["timeframe"]),
                tickers=tuple(str(ticker) for ticker in tickers),
                universe_source=str(payload["universe_source"]),
                config_sha256=str(payload["config_sha256"]),
                grid_cells=raw_cells,
                sealed_access=bool(payload.get("sealed_access", False)),
                tag=str(payload.get("tag", "")),
                notes=str(payload.get("notes", "")),
                prev_hash=str(payload.get("prev_hash", GENESIS_HASH)),
                kind=str(payload.get("kind", "idea")),
            )
        except KeyError as exc:
            raise HypothesisLogError(f"missing field {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, HypothesisLogError):
                raise
            raise HypothesisLogError(f"invalid entry: {exc}") from exc


@dataclass(frozen=True)
class BudgetState:
    """Ideas spent in one calendar quarter. `grid_cells` does not spend it."""

    quarter_start: date
    quarter_end: date
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def canonical_json(payload: Mapping[str, object]) -> str:
    """One canonical form for hashing and for the lines written to the file."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def config_sha256(payload: Mapping[str, object]) -> str:
    """Stable digest of the run parameters, independent of key order."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def entry_hash(entry: HypothesisEntry) -> str:
    """SHA-256 of the canonical line, including its `prev_hash`."""
    return hashlib.sha256(canonical_json(entry.to_dict()).encode("utf-8")).hexdigest()


def quarter_bounds(day: date) -> tuple[date, date]:
    """First day of the quarter and the first day of the next one."""
    start_month = ((day.month - 1) // 3) * 3 + 1
    start = date(day.year, start_month, 1)
    end = (
        date(day.year + 1, 1, 1)
        if start_month == 10
        else date(day.year, start_month + 3, 1)
    )
    return start, end


def verify_chain(entries: Sequence[HypothesisEntry]) -> None:
    """Check that every line points at the hash of the previous one.

    The journal lives in git, where a silent rewrite is invisible. The chain makes
    it visible: editing, reordering or deleting a line breaks the link.
    """
    previous = GENESIS_HASH
    for number, entry in enumerate(entries, start=1):
        if entry.prev_hash != previous:
            raise HypothesisLogError(
                f"line {number}: broken hash chain (prev_hash {entry.prev_hash[:12]}..., "
                f"expected {previous[:12]}...)"
            )
        previous = entry_hash(entry)


def load_hypotheses(
    path: Path | str = DEFAULT_JOURNAL_PATH,
    *,
    verify: bool = True,
) -> list[HypothesisEntry]:
    """Read the journal in append order. A missing file is an empty journal."""
    journal = Path(path)
    if not journal.exists():
        return []
    entries: list[HypothesisEntry] = []
    for number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HypothesisLogError(f"{journal}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise HypothesisLogError(f"{journal}:{number}: entry must be a JSON object")
        try:
            entries.append(HypothesisEntry.from_dict(payload))
        except HypothesisLogError as exc:
            raise HypothesisLogError(f"{journal}:{number}: {exc}") from exc
    if verify:
        try:
            verify_chain(entries)
        except HypothesisLogError as exc:
            raise HypothesisLogError(f"{journal}: {exc}") from exc
    return entries


def _last_hash(entries: Sequence[HypothesisEntry]) -> str:
    return entry_hash(entries[-1]) if entries else GENESIS_HASH


def append_hypothesis(
    entry: HypothesisEntry,
    path: Path | str = DEFAULT_JOURNAL_PATH,
) -> HypothesisEntry:
    """Append one line, linked to the previous one. Never rewrites existing lines."""
    journal = Path(path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    entries = load_hypotheses(journal)
    linked = replace(entry, prev_hash=_last_hash(entries))
    line = canonical_json(linked.to_dict()) + "\n"
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return linked


def record_attempt(
    *,
    hypothesis: str,
    strategy: str,
    timeframe: str,
    tickers: Iterable[str],
    universe_source: str,
    config: Mapping[str, object],
    grid_cells: int = 1,
    sealed_access: bool = False,
    tag: str = "",
    notes: str = "",
    kind: str = "idea",
    path: Path | str = DEFAULT_JOURNAL_PATH,
    recorded_at: datetime | None = None,
    limit: int = TRIAL_BUDGET_PER_QUARTER,
) -> HypothesisEntry:
    """Check the quarterly idea budget, then append the attempt.

    One entry is one idea: `grid_cells` is recorded for the screening correction
    and the report but does not spend the budget. The budget is checked against
    the quarter of `recorded_at` before the entry is written.

    `kind="legacy"` records attempts made before the journal existed: it counts
    in `trial_count()` but does not spend the budget, so the check is skipped.
    """
    moment = recorded_at or datetime.now(UTC)
    entries = load_hypotheses(path)
    if kind == "idea":
        state = budget_state(entries, moment.date(), limit)
        if state.used + 1 > state.limit:
            raise TrialBudgetExceeded(
                f"quarterly idea budget exhausted: {state.used} of {state.limit} ideas "
                f"used, this run would be number {state.used + 1}"
            )
    entry = HypothesisEntry(
        recorded_at=moment,
        hypothesis=hypothesis,
        strategy=strategy,
        timeframe=timeframe,
        tickers=tuple(tickers),
        universe_source=universe_source,
        config_sha256=config_sha256(config),
        grid_cells=grid_cells,
        sealed_access=sealed_access,
        tag=tag,
        notes=notes,
        kind=kind,
    )
    return append_hypothesis(entry, path)


def record_legacy_baseline(
    *,
    variants: int,
    notes: str,
    hypothesis: str = "Attempts made before the journal existed",
    strategy: str = "all",
    timeframe: str = "mixed",
    tickers: Iterable[str] = ("ALL",),
    universe_source: str = "portfolio",
    path: Path | str = DEFAULT_JOURNAL_PATH,
    recorded_at: datetime | None = None,
) -> HypothesisEntry:
    """Record the pre-journal attempt count as one `legacy` line.

    `variants` is a lower bound: the grids and filter variants actually run, read
    from the ADRs (ADR-015, ADR-021, ADR-022, ADR-023, ADR-028, ADR-030,
    ADR-031). The line feeds the multiplicity correction and does not spend the
    idea budget.
    """
    if variants < 1:
        raise HypothesisLogError(f"variants must be >= 1, got {variants!r}")
    if not notes.strip():
        raise HypothesisLogError("notes must name the sources of the estimate")
    return record_attempt(
        hypothesis=hypothesis,
        strategy=strategy,
        timeframe=timeframe,
        tickers=tickers,
        universe_source=universe_source,
        config={"legacy_variants": variants, "sources": notes},
        grid_cells=variants,
        notes=notes,
        kind="legacy",
        path=path,
        recorded_at=recorded_at,
    )


def trial_count(entries: Iterable[HypothesisEntry]) -> int:
    """Parameter variants looked at, across every strategy and idea.

    This is the screening-correction input: a heatmap cell is a looked-at variant.
    It is not the budget counter — see `budget_state()`. Legacy entries (attempts
    made before the journal existed) are variants too, so they are counted here.
    """
    return sum(
        entry.grid_cells for entry in entries if entry.kind in ("idea", "legacy")
    )


def quarter_ideas(entries: Iterable[HypothesisEntry], day: date) -> int:
    """Recorded ideas inside the calendar quarter of `day`."""
    start, end = quarter_bounds(day)
    return sum(
        1
        for entry in entries
        if entry.kind == "idea" and start <= entry.recorded_at.date() < end
    )


def quarter_trials(entries: Iterable[HypothesisEntry], day: date) -> int:
    """Parameter variants inside the calendar quarter of `day`."""
    start, end = quarter_bounds(day)
    return sum(
        entry.grid_cells
        for entry in entries
        if entry.kind == "idea" and start <= entry.recorded_at.date() < end
    )


def sealed_access_count(entries: Iterable[HypothesisEntry]) -> int:
    """How many times the sealed out-of-sample period was opened."""
    return sum(1 for entry in entries if entry.sealed_access)


def budget_state(
    entries: Iterable[HypothesisEntry],
    day: date,
    limit: int = TRIAL_BUDGET_PER_QUARTER,
) -> BudgetState:
    """Ideas used in the quarter; `grid_cells` does not spend the budget."""
    start, end = quarter_bounds(day)
    return BudgetState(
        quarter_start=start,
        quarter_end=end,
        used=quarter_ideas(entries, day),
        limit=limit,
    )


def mark_sealed_access(
    entry: HypothesisEntry,
    path: Path | str = DEFAULT_JOURNAL_PATH,
) -> HypothesisEntry:
    """Record that the sealed period was opened; the entry itself stays immutable.

    The mark is a separate `kind="sealed"` line: it raises the confirmation
    penalty but does not spend the idea budget.
    """
    marked = replace(entry, sealed_access=True, kind="sealed")
    return append_hypothesis(marked, path)
