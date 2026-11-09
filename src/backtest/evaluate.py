"""`evaluate`: one command from an idea to a verdict.

The verdict engine (`verdict.py`) decides from inputs; this module adds what the
verdict lacks (ADR-032):

- the number of attempts comes from the journal, not from a flag;
- a minimum detectable effect (MDE) with an explicit power, so the report can
  say `underpowered` instead of blaming the idea for a small sample;
- a sealed out-of-sample period that the loader does not read at all until it is
  opened on purpose.

The report is Markdown; the script (`scripts/evaluate.py`) supplies the numbers
and writes the journal line before printing, so a crash still counts the attempt.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import NormalDist

from src.backtest.event_study import SigmaEstimate, WindowScan
from src.backtest.hypothesis_log import canonical_json
from src.backtest.verdict import (
    FRAGILE,
    INCONCLUSIVE,
    KNOWN_ASSUMPTIONS,
    REJECTED,
    WORKS,
    IntervalResult,
    VerdictResult,
)
from src.market_data.models import Candle

# The fifth state: the data cannot see the edge the target requires. It is not a
# failure of the idea, and the report must say exactly that (ADR-032).
UNDERPOWERED = "underpowered"

DEFAULT_TARGET_EDGE_BPS = 10.0
DEFAULT_POWER = 0.80
DEFAULT_SCREENING_ALPHA = 0.20
DEFAULT_CONFIRM_ALPHA = 0.05
DEFAULT_BUDGET = 20
DEFAULT_SEALED_START = date(2026, 10, 1)
# ADR-034: the target of an idea comes from its own trade count, not from a
# single number for every strategy. `live_retention` is the share of the backtest
# edge expected to survive execution.
DEFAULT_SHARPE_TARGET = 1.0
DEFAULT_LIVE_RETENTION = 0.6
DEFAULT_COST_FLOOR_BPS = 10.0


@dataclass(frozen=True)
class TargetModel:
    """Parameters of the trade-count-dependent target (ADR-034)."""

    sharpe_target: float = DEFAULT_SHARPE_TARGET
    live_retention: float = DEFAULT_LIVE_RETENTION
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS

    def __post_init__(self) -> None:
        if self.sharpe_target <= 0:
            raise ValueError("sharpe_target must be positive")
        if not 0.0 < self.live_retention <= 1.0:
            raise ValueError("live_retention must be in (0, 1]")
        if self.cost_floor_bps < 0:
            raise ValueError("cost_floor_bps must be non-negative")

    def describe(self) -> dict:
        return {
            "sharpe_target": self.sharpe_target,
            "live_retention": self.live_retention,
            "cost_floor_bps": self.cost_floor_bps,
        }


@dataclass(frozen=True)
class EvaluateThresholds:
    """Thresholds fixed before the first run, with a fingerprint for the report."""

    target_edge_bps: float = DEFAULT_TARGET_EDGE_BPS
    power: float = DEFAULT_POWER
    screening_alpha: float = DEFAULT_SCREENING_ALPHA
    confirm_alpha: float = DEFAULT_CONFIRM_ALPHA
    sealed_start: date = DEFAULT_SEALED_START
    budget: int = DEFAULT_BUDGET
    target_model: TargetModel = TargetModel()
    source: str = "defaults"
    fingerprint: str = "defaults"

    def __post_init__(self) -> None:
        if self.target_edge_bps <= 0:
            raise ValueError("target_edge_bps must be positive")
        if not 0.0 < self.power < 1.0:
            raise ValueError("power must be in (0, 1)")
        if not 0.0 < self.screening_alpha < 1.0:
            raise ValueError("screening_alpha must be in (0, 1)")
        if not 0.0 < self.confirm_alpha < 1.0:
            raise ValueError("confirm_alpha must be in (0, 1)")
        if self.budget < 1:
            raise ValueError("budget must be at least 1")

    def describe(self) -> dict:
        return {
            "target_edge_bps": self.target_edge_bps,
            "power": self.power,
            "screening_alpha": self.screening_alpha,
            "confirm_alpha": self.confirm_alpha,
            "sealed_start": self.sealed_start.isoformat(),
            "budget": self.budget,
            "target_model": self.target_model.describe(),
            "evaluate_version": self.fingerprint,
        }


def load_evaluate_thresholds(path: str | Path) -> EvaluateThresholds:
    """Read the `evaluate` section of config/validation.json."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    section = payload.get("evaluate")
    if not isinstance(section, Mapping):
        raise ValueError(f"{path}: missing 'evaluate' section")
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    sealed = section.get("sealed_start")
    model_raw = section.get("target_model") or {}
    if not isinstance(model_raw, Mapping):
        raise ValueError(f"{path}: 'evaluate.target_model' must be an object")
    return EvaluateThresholds(
        target_edge_bps=float(section.get("target_edge_bps", DEFAULT_TARGET_EDGE_BPS)),
        power=float(section.get("power", DEFAULT_POWER)),
        screening_alpha=float(section.get("screening_alpha", DEFAULT_SCREENING_ALPHA)),
        confirm_alpha=float(section.get("confirm_alpha", DEFAULT_CONFIRM_ALPHA)),
        sealed_start=(
            date.fromisoformat(str(sealed)) if sealed is not None else DEFAULT_SEALED_START
        ),
        budget=int(section.get("budget", DEFAULT_BUDGET)),
        target_model=TargetModel(
            sharpe_target=float(model_raw.get("sharpe_target", DEFAULT_SHARPE_TARGET)),
            live_retention=float(model_raw.get("live_retention", DEFAULT_LIVE_RETENTION)),
            cost_floor_bps=float(model_raw.get("cost_floor_bps", DEFAULT_COST_FLOOR_BPS)),
        ),
        source=str(path),
        fingerprint=f"{version}+{_digest(section)[:12]}",
    )


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ── sealed period ──────────────────────────────────────────────────────────


def before_sealed(candles: Sequence[Candle], sealed_start: date) -> list[Candle]:
    """Candles strictly before the sealed start.

    The sealed period is a technical guarantee, not a convention: the loader for
    `evaluate` never sees a bar at or after `sealed_start`, so no number in the
    report can be influenced by it until the period is opened explicitly.
    """
    return [candle for candle in candles if candle.open_time.date() < sealed_start]


def sealed_rows(
    rows: Mapping[str, Sequence[Candle]], sealed_start: date
) -> dict[str, list[Candle]]:
    """Apply `before_sealed()` to every ticker."""
    return {ticker: before_sealed(candles, sealed_start) for ticker, candles in rows.items()}


# ── power ──────────────────────────────────────────────────────────────────


def _z(alpha: float) -> float:
    """Two-sided normal quantile for the given alpha."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


def standard_error(interval: IntervalResult, alpha: float = 0.05) -> float | None:
    """Per-trade SE recovered from the reported interval; None for tiny samples."""
    if interval.trades < 2 or interval.ci_low == interval.ci_high:
        return None
    return (interval.ci_high - interval.ci_low) / (2.0 * _z(alpha))


def sigma_bps(interval: IntervalResult, alpha: float = 0.05) -> float | None:
    """Per-trade standard deviation, recovered from the interval width."""
    error = standard_error(interval, alpha)
    if error is None:
        return None
    return error * math.sqrt(interval.trades)


def minimum_detectable_effect(
    interval: IntervalResult,
    *,
    alpha: float = 0.05,
    power: float = DEFAULT_POWER,
) -> float | None:
    """Smallest effect detectable with `power` at this sample size.

    `MDE = (z(1 - alpha/2) + z(power)) * SE`. With `power = 0.5` the second term
    vanishes, which is what docs/EVALUATE_DESIGN.md §5.1 used; the implementation
    uses 80% so that "not detected" means "the sample is too small", not "the
    effect is a coin flip" (ADR-032).
    """
    error = standard_error(interval, alpha)
    if error is None:
        return None
    return (_z(alpha) + NormalDist().inv_cdf(power)) * error


def adjusted_alpha(alpha: float, n_trials: int) -> float:
    """Bonferroni level for `n_trials` looked-at variants."""
    return alpha / max(1, n_trials)


def mde_with_trials(
    interval: IntervalResult,
    *,
    alpha: float = 0.05,
    power: float = DEFAULT_POWER,
    n_trials: int = 1,
) -> float | None:
    """MDE at the multiplicity-adjusted level.

    The interval is built at `alpha`, so the SE must be recovered at that level;
    only the quantile is replaced by the adjusted one. Recovering the SE at the
    adjusted level would apply the correction twice.
    """
    error = standard_error(interval, alpha)
    if error is None:
        return None
    level = adjusted_alpha(alpha, n_trials)
    return (_z(level) + NormalDist().inv_cdf(power)) * error


def trades_needed(
    *,
    target_bps: float,
    sigma: float | None,
    alpha: float = 0.05,
    power: float = DEFAULT_POWER,
    n_trials: int = 1,
) -> int | None:
    """Sample size that would make `target_bps` detectable at this power."""
    if target_bps <= 0 or sigma is None or sigma <= 0:
        return None
    z_total = _z(adjusted_alpha(alpha, n_trials)) + NormalDist().inv_cdf(power)
    return math.ceil((z_total * sigma / target_bps) ** 2)


def mde_from_sigma(
    sigma: float | None,
    trades: int,
    *,
    alpha: float = 0.05,
    power: float = DEFAULT_POWER,
    n_trials: int = 1,
) -> float | None:
    """MDE from a known sigma and an event count, without any strategy result.

    This is the pre-check used by `evaluate --power-only`: an event idea has a
    countable number of events, and the dispersion of the outcome variable can be
    measured unconditionally, so the question "can this sample see the target"
    is answered before the strategy exists.
    """
    if sigma is None or sigma <= 0 or trades < 2:
        return None
    z_total = _z(adjusted_alpha(alpha, n_trials)) + NormalDist().inv_cdf(power)
    return z_total * sigma / math.sqrt(trades)



# ── outcomes ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """The evaluate state plus the numbers that produced it."""

    status: str
    reasons: tuple[str, ...] = ()
    mde_bps: float | None = None
    mde_adjusted_bps: float | None = None
    sigma_bps: float | None = None
    trades_needed: int | None = None

    def describe(self) -> dict:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "mde_bps": _round(self.mde_bps),
            "mde_adjusted_bps": _round(self.mde_adjusted_bps),
            "sigma_bps": _round(self.sigma_bps),
            "trades_needed": self.trades_needed,
        }


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def decide_outcome(
    *,
    pool: IntervalResult,
    base: VerdictResult,
    mde_adjusted: float | None,
    target_edge_bps: float,
    confirm: IntervalResult | None = None,
    sealed_confirmed: bool = False,
    sigma: float | None = None,
    mde: float | None = None,
    trades_needed_for_target: int | None = None,
) -> Outcome:
    """Order is fixed: rejected → underpowered → inconclusive → fragile → works.

    `rejected` means the interval proves the effect is BELOW the target, not that
    zero is not excluded. `underpowered` means the sample cannot separate the
    target from zero, so the idea is not judged.
    """

    def make(status: str, reasons: tuple[str, ...]) -> Outcome:
        return Outcome(
            status,
            reasons,
            mde_bps=mde,
            mde_adjusted_bps=mde_adjusted,
            sigma_bps=sigma,
            trades_needed=trades_needed_for_target,
        )

    if base.status == REJECTED:
        return make(REJECTED, tuple(f"{hit.code}: {hit.message}" for hit in base.reasons))

    if pool.ci_high < target_edge_bps:
        return make(
            REJECTED,
            (
                f"pool interval is below the target: upper bound "
                f"{pool.ci_high:+.2f} bps < {target_edge_bps:g} bps",
            ),
        )

    if confirm is not None and confirm.ci_high < target_edge_bps:
        return make(
            REJECTED,
            (
                f"confirmation interval is below the target: upper bound "
                f"{confirm.ci_high:+.2f} bps < {target_edge_bps:g} bps",
            ),
        )

    if (
        mde_adjusted is not None
        and mde_adjusted > target_edge_bps
        and pool.ci_low < target_edge_bps
    ):
        return make(
            UNDERPOWERED,
            (
                f"MDE {mde_adjusted:.1f} bps > target {target_edge_bps:g} bps; "
                "the sample cannot see the edge we need",
            ),
        )

    if base.status == INCONCLUSIVE:
        return make(
            INCONCLUSIVE, tuple(f"{hit.code}: {hit.message}" for hit in base.reasons)
        )

    if base.status == FRAGILE:
        return make(FRAGILE, tuple(f"{hit.code}: {hit.message}" for hit in base.reasons))

    if base.status == WORKS:
        if not sealed_confirmed:
            return make(
                FRAGILE,
                (
                    "positive on the fit window and in the development holdout, but "
                    "the sealed period was not opened: `works` needs confirmation "
                    "on data no parameter was fitted on",
                ),
            )
        return make(WORKS, ())

    raise ValueError(f"unknown base verdict status: {base.status!r}")


# ── report ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvaluateReport:
    """Everything the Markdown report prints."""

    strategy: str
    timeframe: str
    hypothesis: str
    outcome: Outcome
    fit: IntervalResult
    pool: IntervalResult
    development: IntervalResult
    costs_doubled: IntervalResult
    tp_penalty: IntervalResult
    without_best: IntervalResult | None
    base_status: str
    null_test: str
    heatmap_status: str
    heatmap_plateau: bool | None
    tradable: bool
    dividend_coverage: Mapping[str, object] | None
    ideas_used: int
    ideas_limit: int
    grid_cells: int
    n_trials: int
    target_edge_bps: float
    power: float
    confirm_alpha: float
    screening_alpha: float
    sealed_start: date
    sealed_accessed: bool
    recorded: bool
    sealed_bars_cut: int = 0
    journal_warning: str | None = None
    portfolio_fingerprint: str = "defaults"
    evaluate_fingerprint: str = "defaults"


def _line(label: str, value: IntervalResult, level: str = "CI95") -> str:
    return (
        f"{label:<18} {value.trades:>5} trades  {value.net_bps:+7.2f} bps  "
        f"{level} [{value.ci_low:+.2f}; {value.ci_high:+.2f}]"
    )


def render_report(report: EvaluateReport) -> str:
    """Markdown in the fixed order of docs/EVALUATE_DESIGN.md §8."""
    outcome = report.outcome
    lines: list[str] = [
        f"# Evaluate: {report.strategy} / {report.timeframe}",
        f"Hypothesis: {report.hypothesis}",
        (
            f"Ideas this quarter: {report.ideas_used} of {report.ideas_limit} "
            f"(this run adds {1 if report.recorded else 0}, grid_cells "
            f"{report.grid_cells})"
        ),
        "",
    ]
    if not report.recorded:
        lines.append("> **NOT RECORDED (dry run):** the journal and the budget were not touched.")
        lines.append("")

    lines.append(f"## Verdict: {outcome.status}")
    if outcome.reasons:
        lines.append("Reason: " + "; ".join(outcome.reasons))
    else:
        lines.append("Reason: all checks passed and the sealed period confirmed the result.")
    lines.append("")

    lines.append("## Numbers")
    lines.append("")
    lines.append("```")
    lines.append(
        _line(
            "fit (screening)",
            report.fit,
            level=f"CI{100 * (1 - report.screening_alpha):.0f}",
        )
    )
    lines.append(_line("pool (before sealed)", report.pool))
    lines.append(_line("development", report.development))
    lines.append(_line("costs x2", report.costs_doubled))
    lines.append(_line("TP penalty", report.tp_penalty))
    if report.without_best is not None:
        lines.append(_line("without best", report.without_best))
    if report.sealed_accessed:
        lines.append(_line("sealed OOS", report.development))
    else:
        lines.append(f"sealed OOS        not accessed (starts {report.sealed_start.isoformat()})")
    lines.append("```")
    lines.append("")
    lines.append(
        f"note: the pool stops before {report.sealed_start.isoformat()} "
        f"({report.sealed_bars_cut} bars cut). `scripts/verdict.py` runs the full "
        "history, so its pool is larger by the sealed part; compare pools only "
        "within one tool or after the sealed period is opened."
    )
    lines.append("")

    lines.append("## Power")
    lines.append("")
    sigma = "n/a" if outcome.sigma_bps is None else f"{outcome.sigma_bps:.0f} bps"
    mde = "n/a" if outcome.mde_bps is None else f"{outcome.mde_bps:.0f} bps"
    adjusted = (
        "n/a" if outcome.mde_adjusted_bps is None else f"{outcome.mde_adjusted_bps:.0f} bps"
    )
    needed = "n/a" if outcome.trades_needed is None else f"~{outcome.trades_needed} trades"
    lines.append(
        f"sigma {sigma} | MDE {mde} | MDE with {report.n_trials} trials {adjusted} "
        f"| target {report.target_edge_bps:g} bps | power {report.power:.2f}"
    )
    lines.append(f"trades needed for target: {needed}")
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    plateau = (
        "n/a"
        if report.heatmap_plateau is None
        else f"{report.heatmap_status} ({'plateau' if report.heatmap_plateau else 'no plateau'})"
    )
    lines.append(
        f"null {report.null_test} | plateau {plateau} | tradable {report.tradable} "
        f"| base verdict {report.base_status}"
    )
    if report.dividend_coverage is not None:
        coverage = report.dividend_coverage
        parts = [
            f"{key}={coverage[key]}"
            for key in ("applied", "no_gap_match", "future", "out_of_history", "not_paid")
            if key in coverage
        ]
        missing = coverage.get("missing_years")
        if isinstance(missing, Mapping):
            parts.append(f"unconfirmed tickers={len(missing)}")
        lines.append("dividend coverage: " + ", ".join(parts))
    lines.append("")

    lines.append("## Assumptions")
    lines.append("")
    for item in KNOWN_ASSUMPTIONS:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## What would change the verdict")
    lines.append("")
    lines.append(
        f"- the pool must reach {needed} for a {report.target_edge_bps:g} bps target "
        f"at {report.n_trials} trials"
    )
    lines.append(
        f"- the sealed OOS must exclude zero at alpha {report.confirm_alpha:g}/k "
        "after screening"
    )
    lines.append("- x2 costs must keep the lower bound above zero")
    lines.append("")

    lines.append("## Biases")
    lines.append("")
    lines.append(
        "The 20-ticker universe was chosen by today's liquidity: survivorship bias. "
        "Manual --tickers runs are recorded separately and count as a new trial."
    )
    lines.append("")
    lines.append(
        f"portfolio {report.portfolio_fingerprint} | evaluate {report.evaluate_fingerprint}"
    )
    lines.append("")
    if report.journal_warning:
        lines.append(f"> {report.journal_warning}")
        lines.append("")
    return "\n".join(lines)


# ── power-only pre-check ───────────────────────────────────────────────────


def target_for_trades(
    *,
    sigma_bps: float | None,
    trades_per_year: float | None,
    sharpe_target: float = 1.0,
    live_retention: float = 0.6,
    cost_floor_bps: float = 10.0,
) -> float | None:
    """Per-trade edge an idea must show, from its own trade count (ADR-034).

    `target = sharpe_target * sigma / sqrt(trades a year) / live_retention`,
    floored at the cost of a round trip. The number comes from the unconditional
    sigma and the number of events, never from the strategy result, so it can be
    fixed before the run and stored next to the hypothesis.

    `live_retention` is the share of the backtest edge expected to survive
    execution: a backtest edge that exactly reaches the Sharpe target leaves no
    room for the part that live loses.
    """
    if sigma_bps is None or sigma_bps <= 0:
        return None
    if trades_per_year is None or trades_per_year <= 0:
        return None
    if not 0.0 < live_retention <= 1.0:
        raise ValueError("live_retention must be in (0, 1]")
    if sharpe_target <= 0:
        raise ValueError("sharpe_target must be positive")
    raw = sharpe_target * sigma_bps / math.sqrt(trades_per_year) / live_retention
    return max(raw, cost_floor_bps)


@dataclass(frozen=True)
class PowerRow:
    """One window length of the pre-check: events, clusters, sigma, MDE, target.

    No mean return is stored or printed: the pre-check answers "can the sample
    see the target", not "what did the windows return". A mean seen before the
    journal entry cannot be unseen and would bias the decision (ADR-033).
    """

    window_sessions: int
    events: int
    clusters: int
    sigma_bps: float | None
    mde_bps: float | None
    mde_adjusted_bps: float | None
    mde_cluster_bps: float | None
    target_idea_bps: float | None
    trades_per_year: float | None

    def describe(self) -> dict:
        return {
            "window_sessions": self.window_sessions,
            "events": self.events,
            "clusters": self.clusters,
            "sigma_bps": _round(self.sigma_bps),
            "mde_bps": _round(self.mde_bps),
            "mde_adjusted_bps": _round(self.mde_adjusted_bps),
            "mde_cluster_bps": _round(self.mde_cluster_bps),
            "target_idea_bps": _round(self.target_idea_bps),
            "trades_per_year": _round(self.trades_per_year),
        }


@dataclass(frozen=True)
class PowerReport:
    """Result of `evaluate --power-only`: a feasibility check, not a verdict."""

    label: str
    timeframe: str
    tickers: tuple[str, ...]
    total_events: int
    events: int
    per_ticker: Mapping[str, int]
    sigma_bps: float | None
    median_sigma_bps: float | None
    sigma_windows: int
    target_edge_bps: float
    power: float
    alpha: float
    n_trials: int
    grid_cells: int
    main_window: int
    sealed_start: date
    sigma_mode: str = "raw"
    cluster_by: str = "quarter"
    rows: tuple[PowerRow, ...] = ()
    out_of_history: int = 0
    future: int = 0
    sealed: int = 0
    declared_after_entry: int = 0
    missing_declared_date: int = 0
    journal_warning: str | None = None
    evaluate_fingerprint: str = "defaults"
    span_years: float | None = None
    events_per_year: float | None = None
    sharpe_target: float = 1.0
    live_retention: float = 0.6
    cost_floor_bps: float = 10.0

    @property
    def main_row(self) -> PowerRow | None:
        return next(
            (row for row in self.rows if row.window_sessions == self.main_window), None
        )

    @property
    def measurable(self) -> bool | None:
        """Whether the idea's own target is detectable on the clustered sample.

        The decision uses the cluster MDE, not the iid one: events cluster by
        season, so 129 events are fewer than 129 independent observations.
        """
        row = self.main_row
        if row is None or row.mde_cluster_bps is None or row.target_idea_bps is None:
            return None
        return row.mde_cluster_bps <= row.target_idea_bps


def build_power_row(
    *,
    window_sessions: int,
    scan: WindowScan,
    sigma: SigmaEstimate,
    clusters: int,
    target_edge_bps: float,
    target_idea_bps: float | None,
    trades_per_year: float | None,
    alpha: float,
    power: float,
    n_trials: int,
) -> PowerRow:
    return PowerRow(
        window_sessions=window_sessions,
        events=scan.events,
        clusters=clusters,
        sigma_bps=sigma.sigma_bps,
        mde_bps=mde_from_sigma(
            sigma.sigma_bps, scan.events, alpha=alpha, power=power, n_trials=1
        ),
        mde_adjusted_bps=mde_from_sigma(
            sigma.sigma_bps, scan.events, alpha=alpha, power=power, n_trials=n_trials
        ),
        mde_cluster_bps=mde_from_sigma(
            sigma.sigma_bps, clusters, alpha=alpha, power=power, n_trials=n_trials
        ),
        target_idea_bps=target_idea_bps,
        trades_per_year=trades_per_year,
    )


def render_power_report(report: PowerReport) -> str:
    """Markdown for the pre-check; explicitly not a verdict."""
    lines: list[str] = [
        f"# Power only: {report.label} / {report.timeframe}",
        (
            f"Tickers: {len(report.tickers)} | events: {report.events} of "
            f"{report.total_events} in the calendar"
        ),
        (
            f"Ideas this quarter: not read, not spent — this is a pre-check, "
            f"the journal is untouched (grid_cells {report.grid_cells})"
        ),
        "",
        "> **NOT A VERDICT.** No strategy ran, no trade was simulated and no PnL "
        "was computed. The question is only whether this sample could separate "
        f"the idea target ({_bps(report.main_row.target_idea_bps) if report.main_row else 'n/a'}"
        f" bps) from zero at power {report.power:.2f}. No mean return is printed: "
        "a number seen before the journal entry would bias the decision.",
        "",
        "## Events",
        "",
        "```",
        f"eligible            {report.events:>4}",
        f"out of history      {report.out_of_history:>4}",
        f"future              {report.future:>4}",
        f"sealed period       {report.sealed:>4}",
        f"declared after entry{report.declared_after_entry:>4}",
        f"declaration missing {report.missing_declared_date:>4}",
        "```",
        "",
    ]
    if report.per_ticker:
        per_ticker = ", ".join(f"{key}={value}" for key, value in report.per_ticker.items())
        lines.append(f"per ticker: {per_ticker}")
        lines.append("")

    lines.append("## Power")
    lines.append("")
    lines.append("```")
    lines.append(
        f"{'window':>7} {'events':>7} {'clusters':>9} {'sigma':>8} "
        f"{'MDE':>7} {'MDE@trials':>11} {'MDE clust':>10} {'target':>8}"
    )
    for row in report.rows:
        lines.append(
            f"{row.window_sessions:>7} {row.events:>7} {row.clusters:>9} "
            f"{_bps(row.sigma_bps):>8} {_bps(row.mde_bps):>7} "
            f"{_bps(row.mde_adjusted_bps):>11} {_bps(row.mde_cluster_bps):>10} "
            f"{_bps(row.target_idea_bps):>8}"
        )
    lines.append("```")
    lines.append("")
    median = _bps(report.median_sigma_bps)
    measured = (
        "excess return to the equal-weight basket, excluding the traded stock"
        if report.sigma_mode == "excess"
        else "raw return"
    )
    lines.append(
        f"sigma is unconditional and measured on the {measured}: standard deviation "
        f"of all {report.sigma_windows} rolling window returns, not of the event "
        f"windows. median per-ticker sigma {median}."
    )
    lines.append(
        f"alpha {report.alpha:g} | trials {report.n_trials} (journal + grid) | "
        f"power {report.power:.2f} | clusters by {report.cluster_by}"
    )
    lines.append("")
    lines.append(
        f"`MDE` uses the event count as if the events were independent; "
        f"`MDE clust` uses the number of {report.cluster_by}s instead. Events cluster "
        "by season (most issuers pay in June-July), so the clustered number is the "
        "honest one and the decision uses it (ADR-033)."
    )
    lines.append("")
    measurable = report.measurable
    if measurable is None:
        lines.append("**Cannot say:** too few events for an MDE.")
    elif measurable:
        lines.append(
            "**Measurable:** the clustered MDE is at or below the idea target, so "
            "the idea can be tested without changing the design."
        )
    else:
        main = report.main_row
        lines.append(
            f"**Not measurable:** the clustered MDE ({_bps(main.mde_cluster_bps) if main else 'n/a'} "
            f"bps) is above the idea target ({_bps(main.target_idea_bps) if main else 'n/a'} bps). "
            "Running the idea would spend a slot on a sample that cannot answer the "
            "question; widen the universe or change the design first."
        )
    lines.append("")

    lines.append("## Economic scale")
    lines.append("")
    if report.events_per_year is None or report.span_years is None:
        lines.append("not available: too few events to place them on a timeline.")
    else:
        lines.append(
            f"{report.events} events over {report.span_years:.1f} years = "
            f"{report.events_per_year:.1f} trades a year."
        )
        lines.append("")
        lines.append("```")
        lines.append(
            f"idea target = {report.sharpe_target:g} * sigma / sqrt(trades a year) "
            f"/ {report.live_retention:g}"
        )
        lines.append(f"cost floor                  {report.cost_floor_bps:>7.0f} bps")
        main = report.main_row
        if main is not None:
            lines.append(f"target for this idea        {_bps(main.target_idea_bps):>7} bps")
        lines.append("```")
        lines.append("")
        lines.append(
            "The target is computed from the unconditional sigma and the event "
            "count, before the strategy exists, and is stored next to the "
            "hypothesis (ADR-034). `live_retention` is the share of the backtest "
            "edge expected to survive execution; a target that exactly reaches the "
            "Sharpe goal leaves no room for that loss."
        )
    lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append(
        "- events of one ticker can overlap, and events cluster by season; the "
        "clustered MDE counts each season once, which is a lower bound on the "
        "effective sample size, so the true MDE is not higher than printed."
    )
    if report.missing_declared_date:
        lines.append(
            f"- `declared_date` is missing for {report.missing_declared_date} events "
            "(calendar v3 was fetched before the field was stored). The "
            "no-look-ahead filter is not applied to them; the next fetch writes it."
        )
    if report.sigma_mode == "excess":
        lines.append(
            "- the basket is equal-weight, excludes the traded stock and rebalances "
            "to the tickers that have a bar on a date; it removes the market move, "
            "not sector or size effects."
        )
    lines.append(
        "- `not_paid` events are kept: they were announced before the entry, and "
        "dropping them would be survivorship bias."
    )
    lines.append(
        f"- the sealed period starting {report.sealed_start.isoformat()} is excluded "
        "from the pre-check as well."
    )
    lines.append("")
    if report.journal_warning:
        lines.append(f"> {report.journal_warning}")
        lines.append("")
    lines.append(f"evaluate {report.evaluate_fingerprint}")
    lines.append("")
    return "\n".join(lines)


def _bps(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


