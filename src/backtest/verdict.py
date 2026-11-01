"""Single verdict report: one place where every check is turned into a decision.

Previous tasks each produced a number and stopped there: net PnL and a block
bootstrap (T2-20), a leave-one-out check (T2-20a), a plateau map (T2-19), a
null test (T2-18), an out-of-sample period (T2-8). A person had to read five
reports and decide. This module turns those inputs into one of four states:

    rejected      measured negative, or the report is not valid at all
    inconclusive  the data cannot separate the result from zero
    fragile       positive, but it does not survive the checks
    works         positive on the pool, in the holdout and at doubled costs,
                  not dependent on one ticker, sitting on a plateau

The gates are ordered and the first group that fires decides the state; every
triggered gate of that group is reported, so the reason is never a single
opaque word. Order matters: an invalid report must not be called "fragile", and
"insufficient data" must not be called "works".

Deliberate asymmetry (ADR-023):

- doubled costs turning the interval fully negative is **fragile**, not
  rejected: the strategy was not measured negative at base costs, it is
  cost-sensitive;
- a plateau is checked **around the chosen TP/SL**, not around the best cell:
  the gate must measure what is actually traded;
- the out-of-sample period is a frozen calendar holdout, because a
  fixed-parameter walk-forward on the same history is not out-of-sample by
  parameters.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REJECTED = "rejected"
INCONCLUSIVE = "inconclusive"
FRAGILE = "fragile"
WORKS = "works"

NULL_OK = "ok"
NULL_INCONCLUSIVE = "inconclusive"
NULL_OPTIMISM = "optimism"
NULL_MISSING = "missing"

DEFAULT_MIN_TOTAL_TRADES = 200
DEFAULT_MIN_OOS_TRADES = 100
DEFAULT_COST_MULTIPLIER = 2.0
DEFAULT_TP_PENALTY_BPS = 4.0
DEFAULT_OOS_RATIO_MIN_PERCENT = 50.0


@dataclass(frozen=True)
class VerdictThresholds:
    """Verdict thresholds with provenance, so a decision can be reproduced."""

    min_total_trades: int = DEFAULT_MIN_TOTAL_TRADES
    min_oos_trades: int = DEFAULT_MIN_OOS_TRADES
    holdout_start: date | None = None
    holdout_end: date | None = None
    cost_multiplier: float = DEFAULT_COST_MULTIPLIER
    tp_penalty_bps: float = DEFAULT_TP_PENALTY_BPS
    oos_ratio_min_percent: float = DEFAULT_OOS_RATIO_MIN_PERCENT
    n_trials: int = 0
    source: str = "defaults"
    fingerprint: str = "defaults"

    def __post_init__(self) -> None:
        if self.min_total_trades < 1 or self.min_oos_trades < 1:
            raise ValueError("trade thresholds must be positive")
        if self.cost_multiplier <= 1.0:
            raise ValueError("cost_multiplier must be greater than 1")
        if self.tp_penalty_bps < 0:
            raise ValueError("tp_penalty_bps must be non-negative")
        if not 0.0 < self.oos_ratio_min_percent <= 100.0:
            raise ValueError("oos_ratio_min_percent must be in (0, 100]")
        if self.n_trials < 0:
            raise ValueError("n_trials must be non-negative")
        if self.holdout_start and self.holdout_end and self.holdout_start >= self.holdout_end:
            raise ValueError("holdout_start must be earlier than holdout_end")

    def describe(self) -> dict:
        return {
            "min_total_trades": self.min_total_trades,
            "min_oos_trades": self.min_oos_trades,
            "holdout_start": self.holdout_start.isoformat() if self.holdout_start else None,
            "holdout_end": self.holdout_end.isoformat() if self.holdout_end else None,
            "cost_multiplier": self.cost_multiplier,
            "tp_penalty_bps": self.tp_penalty_bps,
            "oos_ratio_min_percent": self.oos_ratio_min_percent,
            "n_trials": self.n_trials,
            "verdict_version": self.fingerprint,
            "verdict_source": self.source,
        }


def load_verdict_thresholds(path: str | Path) -> VerdictThresholds:
    """Read the `verdict` section of config/validation.json."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    section = payload.get("verdict")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: missing 'verdict' section")
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    return VerdictThresholds(
        min_total_trades=int(section["min_total_trades"]),
        min_oos_trades=int(section["min_oos_trades"]),
        holdout_start=_parse_date(section.get("holdout_start")),
        holdout_end=_parse_date(section.get("holdout_end")),
        cost_multiplier=float(section.get("cost_multiplier", DEFAULT_COST_MULTIPLIER)),
        tp_penalty_bps=float(section.get("tp_penalty_bps", DEFAULT_TP_PENALTY_BPS)),
        oos_ratio_min_percent=float(
            section.get("oos_ratio_min_percent", DEFAULT_OOS_RATIO_MIN_PERCENT)
        ),
        n_trials=int(section.get("n_trials", 0)),
        source=str(path),
        fingerprint=f"{version}+{hashlib.sha256(raw).hexdigest()[:12]}",
    )


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


# ── inputs ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntervalResult:
    """A net expectancy with its bootstrap interval, in bps per trade."""

    trades: int
    net_bps: float
    ci_low: float
    ci_high: float

    def __post_init__(self) -> None:
        if self.trades < 0:
            raise ValueError("trades must be non-negative")
        if self.ci_low > self.ci_high:
            raise ValueError("ci_low must not exceed ci_high")

    @property
    def positive(self) -> bool:
        return self.ci_low > 0.0

    @property
    def negative(self) -> bool:
        return self.ci_high < 0.0

    @property
    def contains_zero(self) -> bool:
        return not self.positive and not self.negative

    def describe(self) -> dict:
        return {
            "trades": self.trades,
            "net_bps": round(self.net_bps, 2),
            "ci95": [round(self.ci_low, 2), round(self.ci_high, 2)],
            "positive": self.positive,
            "negative": self.negative,
        }


@dataclass(frozen=True)
class VerdictInputs:
    """Everything the gates read. Missing optional inputs are `None`, never zeros.

    `None` is meaningful: a missing holdout must not look like a holdout with a
    zero result. The gates treat it as a failure to establish the condition.
    """

    pool: IntervalResult
    oos: IntervalResult | None = None
    pool_without_best: IntervalResult | None = None
    costs_doubled: IntervalResult | None = None
    tp_penalty: IntervalResult | None = None
    null_test: str = NULL_MISSING
    heatmap_status: str = "missing"
    heatmap_plateau: bool | None = None
    tradable: bool = True
    gap_verdict_changed: bool = False
    best_ticker: str | None = None
    best_contribution_percent: float | None = None
    n_trials: int | None = None
    extra_meta: Mapping[str, object] = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "pool": self.pool.describe(),
            "oos": self.oos.describe() if self.oos is not None else None,
            "pool_without_best": (
                self.pool_without_best.describe()
                if self.pool_without_best is not None
                else None
            ),
            "costs_doubled": (
                self.costs_doubled.describe() if self.costs_doubled is not None else None
            ),
            "tp_penalty": self.tp_penalty.describe() if self.tp_penalty is not None else None,
            "null_test": self.null_test,
            "heatmap_status": self.heatmap_status,
            "heatmap_plateau": self.heatmap_plateau,
            "tradable": self.tradable,
            "gap_verdict_changed": self.gap_verdict_changed,
            "best_ticker": self.best_ticker,
            "best_contribution_percent": (
                round(self.best_contribution_percent, 2)
                if self.best_contribution_percent is not None
                else None
            ),
            "n_trials": self.n_trials,
            **self.extra_meta,
        }


# ── gates ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateHit:
    """One triggered condition, with a code a reader can cite."""

    code: str
    message: str

    def describe(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class VerdictResult:
    """The decision plus every gate that produced it."""

    status: str
    reasons: tuple[GateHit, ...]
    inputs: VerdictInputs
    thresholds: VerdictThresholds
    flags: dict = field(default_factory=dict)

    @property
    def is_works(self) -> bool:
        return self.status == WORKS

    def describe(self) -> dict:
        return {
            **self.thresholds.describe(),
            "status": self.status,
            "reasons": [reason.describe() for reason in self.reasons],
            "flags": self.flags,
            "inputs": self.inputs.describe(),
        }


def evaluate_verdict(
    inputs: VerdictInputs, thresholds: VerdictThresholds
) -> VerdictResult:
    """Run the gates in order and return the first group that fires.

    Gates are grouped `rejected` → `inconclusive` → `fragile` → `works`. Inside a
    group every triggered gate is reported: a reader must see all the reasons,
    not only the first one that happened to be checked.
    """
    rejected = _rejected_gates(inputs)
    if rejected:
        return _result(REJECTED, rejected, inputs, thresholds)

    inconclusive = _inconclusive_gates(inputs, thresholds)
    if inconclusive:
        return _result(INCONCLUSIVE, inconclusive, inputs, thresholds)

    fragile = _fragile_gates(inputs, thresholds)
    if fragile:
        return _result(FRAGILE, fragile, inputs, thresholds)

    return _result(WORKS, (), inputs, thresholds)


def _rejected_gates(inputs: VerdictInputs) -> list[GateHit]:
    """Measured negative, or the report cannot be used at all."""
    hits: list[GateHit] = []
    if inputs.null_test == NULL_OPTIMISM:
        hits.append(
            GateHit(
                "A1",
                "null test reports optimism: the engine makes profit without a signal, "
                "so every other number in this report is invalid",
            )
        )
    if not inputs.tradable:
        hits.append(
            GateHit("A2", "report is GROSS (zero-cost model): not tradable (ADR-016)")
        )
    if inputs.pool.negative:
        hits.append(
            GateHit(
                "A3",
                f"pool interval is fully below zero: "
                f"[{inputs.pool.ci_low:+.2f}; {inputs.pool.ci_high:+.2f}] bps",
            )
        )
    if inputs.oos is not None and inputs.oos.negative:
        hits.append(
            GateHit(
                "A4",
                f"holdout interval is fully below zero: "
                f"[{inputs.oos.ci_low:+.2f}; {inputs.oos.ci_high:+.2f}] bps",
            )
        )
    return hits


def _inconclusive_gates(
    inputs: VerdictInputs, thresholds: VerdictThresholds
) -> list[GateHit]:
    """The data cannot separate the result from zero."""
    hits: list[GateHit] = []
    if inputs.pool.trades < thresholds.min_total_trades:
        hits.append(
            GateHit(
                "B1",
                f"{inputs.pool.trades} pool trades, need {thresholds.min_total_trades}",
            )
        )
    if inputs.oos is None:
        hits.append(GateHit("B2", "holdout result is missing"))
    elif inputs.oos.trades < thresholds.min_oos_trades:
        hits.append(
            GateHit(
                "B2",
                f"{inputs.oos.trades} holdout trades, need {thresholds.min_oos_trades}",
            )
        )
    if inputs.pool.contains_zero:
        hits.append(
            GateHit(
                "B3",
                f"pool interval contains zero: "
                f"[{inputs.pool.ci_low:+.2f}; {inputs.pool.ci_high:+.2f}] bps",
            )
        )
    if inputs.oos is not None and inputs.oos.contains_zero:
        hits.append(
            GateHit(
                "B4",
                f"holdout interval contains zero: "
                f"[{inputs.oos.ci_low:+.2f}; {inputs.oos.ci_high:+.2f}] bps",
            )
        )
    if inputs.null_test in (NULL_INCONCLUSIVE, NULL_MISSING):
        hits.append(
            GateHit(
                "B5",
                f"null test is `{inputs.null_test}`: the engine is not confirmed clean",
            )
        )
    return hits


def _fragile_gates(
    inputs: VerdictInputs, thresholds: VerdictThresholds
) -> list[GateHit]:
    """Positive, but the result does not survive the checks."""
    hits: list[GateHit] = []
    if inputs.pool_without_best is None:
        hits.append(GateHit("C1", "leave-one-out check is missing"))
    elif not inputs.pool_without_best.positive:
        hits.append(
            GateHit(
                "C1",
                f"without {inputs.best_ticker or 'the best ticker'} the interval is not "
                f"positive: [{inputs.pool_without_best.ci_low:+.2f}; "
                f"{inputs.pool_without_best.ci_high:+.2f}] bps — the result rests on one "
                "instrument",
            )
        )
    if inputs.heatmap_plateau is None:
        hits.append(GateHit("C2", "heatmap status is missing"))
    elif not inputs.heatmap_plateau:
        hits.append(
            GateHit(
                "C2",
                f"chosen TP/SL does not sit on a plateau (`{inputs.heatmap_status}`): "
                "the result depends on the exact parameters",
            )
        )
    if inputs.costs_doubled is None:
        hits.append(GateHit("C3", "doubled-cost run is missing"))
    elif not inputs.costs_doubled.positive:
        hits.append(
            GateHit(
                "C3",
                f"at {thresholds.cost_multiplier:g}x costs the interval is not positive: "
                f"[{inputs.costs_doubled.ci_low:+.2f}; "
                f"{inputs.costs_doubled.ci_high:+.2f}] bps — cost-sensitive",
            )
        )
    if inputs.oos is not None:
        floor = inputs.pool.net_bps * thresholds.oos_ratio_min_percent / 100.0
        if inputs.oos.net_bps < floor:
            hits.append(
                GateHit(
                    "C4",
                    f"holdout net {inputs.oos.net_bps:+.2f} bps is below "
                    f"{thresholds.oos_ratio_min_percent:g}% of the pool net "
                    f"({floor:+.2f} bps): the result does not carry over",
                )
            )
    if inputs.tp_penalty is None:
        hits.append(GateHit("C5", "tp_penalty run is missing"))
    elif not inputs.tp_penalty.positive:
        hits.append(
            GateHit(
                "C5",
                f"with TP penalty {thresholds.tp_penalty_bps:g} bps the interval is not "
                f"positive: [{inputs.tp_penalty.ci_low:+.2f}; "
                f"{inputs.tp_penalty.ci_high:+.2f}] bps — TP fills are load-bearing",
            )
        )
    return hits


def _result(
    status: str,
    reasons: Sequence[GateHit],
    inputs: VerdictInputs,
    thresholds: VerdictThresholds,
) -> VerdictResult:
    return VerdictResult(
        status=status,
        reasons=tuple(reasons),
        inputs=inputs,
        thresholds=thresholds,
        flags=_flags(inputs, thresholds),
    )


def _flags(inputs: VerdictInputs, thresholds: VerdictThresholds) -> dict:
    """Informational fields: they never change the status by themselves."""
    return {
        "gap_verdict_changed": inputs.gap_verdict_changed,
        "best_ticker": inputs.best_ticker,
        "best_contribution_percent": (
            round(inputs.best_contribution_percent, 2)
            if inputs.best_contribution_percent is not None
            else None
        ),
        "oos_net_bps": round(inputs.oos.net_bps, 2) if inputs.oos is not None else None,
        "oos_ratio_percent": (
            round(inputs.oos.net_bps / inputs.pool.net_bps * 100.0, 2)
            if inputs.oos is not None and inputs.pool.net_bps
            else None
        ),
        "heatmap_status": inputs.heatmap_status,
        "tp_penalty_net_bps": (
            round(inputs.tp_penalty.net_bps, 2) if inputs.tp_penalty is not None else None
        ),
        "costs_doubled_net_bps": (
            round(inputs.costs_doubled.net_bps, 2)
            if inputs.costs_doubled is not None
            else None
        ),
        # Informational until a multiple-testing correction exists (ADR-023).
        "n_trials": (
            inputs.n_trials if inputs.n_trials is not None else thresholds.n_trials
        ),
    }


# ── report ─────────────────────────────────────────────────────────────────


STATUS_TITLES = {
    REJECTED: "REJECTED — measured negative or invalid report",
    INCONCLUSIVE: "INCONCLUSIVE — the data cannot separate the result from zero",
    FRAGILE: "FRAGILE — positive, but does not survive the checks",
    WORKS: "WORKS — positive on the pool, in the holdout and at doubled costs",
}


def render_markdown(result: VerdictResult) -> str:
    """Markdown verdict: decision first, then inputs and every triggered gate."""
    inputs = result.inputs
    thresholds = result.thresholds
    lines: list[str] = ["# Verdict", ""]
    lines.append(f"## {STATUS_TITLES.get(result.status, result.status)}")
    lines.append("")

    if result.reasons:
        lines.append("Triggered gates:")
        lines.append("")
        for reason in result.reasons:
            lines.append(f"- **{reason.code}** — {reason.message}")
    else:
        lines.append("All gates passed.")
    lines.append("")

    lines.append("## Inputs")
    lines.append("")
    lines.append("| input | trades | net bps | CI95 |")
    lines.append("|---|---|---|---|")
    for label, value in (
        ("pool", inputs.pool),
        ("holdout", inputs.oos),
        ("without best ticker", inputs.pool_without_best),
        (f"costs x{thresholds.cost_multiplier:g}", inputs.costs_doubled),
        (f"TP penalty {thresholds.tp_penalty_bps:g} bps", inputs.tp_penalty),
    ):
        if value is None:
            lines.append(f"| {label} | — | — | missing |")
        else:
            lines.append(
                f"| {label} | {value.trades} | {value.net_bps:+.2f} | "
                f"[{value.ci_low:+.2f}; {value.ci_high:+.2f}] |"
            )
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    lines.append(f"- **null test**: `{inputs.null_test}`")
    lines.append(f"- **heatmap at the chosen TP/SL**: `{inputs.heatmap_status}`")
    lines.append(f"- **tradable**: {inputs.tradable}")
    for label, value in result.flags.items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")

    lines.append("## Thresholds")
    lines.append("")
    for label, value in thresholds.describe().items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")
    lines.append(
        "> `n_trials` is informational: a multiple-testing correction is not applied "
        "yet (ADR-023). A `works` state with many tried variants is still optimistic."
    )
    lines.append("")
    return "\n".join(lines)
