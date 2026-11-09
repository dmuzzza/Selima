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


@dataclass(frozen=True)
class EvaluateThresholds:
    """Thresholds fixed before the first run, with a fingerprint for the report."""

    target_edge_bps: float = DEFAULT_TARGET_EDGE_BPS
    power: float = DEFAULT_POWER
    screening_alpha: float = DEFAULT_SCREENING_ALPHA
    confirm_alpha: float = DEFAULT_CONFIRM_ALPHA
    sealed_start: date = DEFAULT_SEALED_START
    budget: int = DEFAULT_BUDGET
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
    return EvaluateThresholds(
        target_edge_bps=float(section.get("target_edge_bps", DEFAULT_TARGET_EDGE_BPS)),
        power=float(section.get("power", DEFAULT_POWER)),
        screening_alpha=float(section.get("screening_alpha", DEFAULT_SCREENING_ALPHA)),
        confirm_alpha=float(section.get("confirm_alpha", DEFAULT_CONFIRM_ALPHA)),
        sealed_start=(
            date.fromisoformat(str(sealed)) if sealed is not None else DEFAULT_SEALED_START
        ),
        budget=int(section.get("budget", DEFAULT_BUDGET)),
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
