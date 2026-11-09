"""Tests for `evaluate`: power, outcome order, sealed period, report (T2-25)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from scripts.evaluate import JournalGitState, parse_numstat_added
from src.backtest.evaluate import (
    UNDERPOWERED,
    EvaluateReport,
    EvaluateThresholds,
    Outcome,
    adjusted_alpha,
    before_sealed,
    decide_outcome,
    load_evaluate_thresholds,
    mde_with_trials,
    minimum_detectable_effect,
    render_report,
    sealed_rows,
    sigma_bps,
    trades_needed,
)
from src.backtest.verdict import (
    FRAGILE,
    INCONCLUSIVE,
    REJECTED,
    WORKS,
    GateHit,
    IntervalResult,
    VerdictInputs,
    VerdictResult,
    VerdictThresholds,
)
from src.market_data.models import Candle


def interval(net: float, low: float, high: float, trades: int = 1711) -> IntervalResult:
    return IntervalResult(trades=trades, net_bps=net, ci_low=low, ci_high=high)


def interval_from_sigma(*, sigma: float, trades: int, net: float = 0.0) -> IntervalResult:
    """An interval that a bootstrap would produce for this sigma and sample size."""
    error = sigma / (trades**0.5)
    half = 1.959963984540054 * error
    return IntervalResult(
        trades=trades, net_bps=net, ci_low=net - half, ci_high=net + half
    )


def base_verdict(status: str, *, reason: str = "gate") -> VerdictResult:
    inputs = VerdictInputs(pool=interval(5.0, 1.0, 9.0))
    return VerdictResult(
        status=status,
        reasons=(GateHit("X", reason),) if status != WORKS else (),
        inputs=inputs,
        thresholds=VerdictThresholds(),
    )


# ── MDE and the design table ───────────────────────────────────────────────


def test_design_table_uses_50_percent_power():
    """docs/EVALUATE_DESIGN.md §5.1: 1711 trades, sigma 295 -> 14 bps at 50% power."""
    pool = interval_from_sigma(sigma=295.0, trades=1711)
    assert minimum_detectable_effect(pool, alpha=0.05, power=0.5) == pytest.approx(
        13.98, abs=0.05
    )


def test_design_table_with_twenty_trials_is_22_bps_at_50_percent_power():
    """The 22 bps in the design is Bonferroni (0.05/20) at 50% power, not 80%."""
    pool = interval_from_sigma(sigma=295.0, trades=1711)
    assert mde_with_trials(pool, alpha=0.05, power=0.5, n_trials=20) == pytest.approx(
        21.56, abs=0.1
    )


def test_eighty_percent_power_is_about_1_43_times_the_fifty_percent_mde():
    pool = interval_from_sigma(sigma=295.0, trades=1711)
    half = minimum_detectable_effect(pool, alpha=0.05, power=0.5)
    full = minimum_detectable_effect(pool, alpha=0.05, power=0.8)
    assert half is not None and full is not None
    assert full / half == pytest.approx(2.8016 / 1.95996, rel=1e-3)
    assert full == pytest.approx(19.98, abs=0.05)


def test_mde_grows_with_the_number_of_trials():
    pool = interval_from_sigma(sigma=295.0, trades=1711)
    one = mde_with_trials(pool, alpha=0.05, power=0.8, n_trials=1)
    twenty = mde_with_trials(pool, alpha=0.05, power=0.8, n_trials=20)
    assert one is not None and twenty is not None
    assert twenty > one


def test_sigma_is_recovered_from_the_interval_width():
    pool = interval_from_sigma(sigma=295.0, trades=1711)
    assert sigma_bps(pool, alpha=0.05) == pytest.approx(295.0, rel=1e-6)


def test_tiny_samples_have_no_mde():
    assert minimum_detectable_effect(interval(0.0, 0.0, 0.0, trades=1)) is None
    assert sigma_bps(interval(0.0, 0.0, 0.0, trades=0)) is None


def test_adjusted_alpha_is_bonferroni():
    assert adjusted_alpha(0.05, 20) == pytest.approx(0.0025)
    assert adjusted_alpha(0.05, 0) == pytest.approx(0.05)


def test_trades_needed_matches_the_design_estimate():
    """The design says ~3345 trades for 10 bps at 50% power without correction."""
    needed = trades_needed(
        target_bps=10.0, sigma=295.0, alpha=0.05, power=0.5, n_trials=1
    )
    assert needed == pytest.approx(3345, abs=2)


def test_trades_needed_grows_with_power_and_trials():
    base = trades_needed(target_bps=10.0, sigma=295.0, alpha=0.05, power=0.5, n_trials=1)
    stronger = trades_needed(target_bps=10.0, sigma=295.0, alpha=0.05, power=0.8, n_trials=1)
    adjusted = trades_needed(target_bps=10.0, sigma=295.0, alpha=0.05, power=0.8, n_trials=20)
    assert base is not None and stronger is not None and adjusted is not None
    assert base < stronger < adjusted


def test_trades_needed_is_none_without_a_sigma():
    assert trades_needed(target_bps=10.0, sigma=None) is None


# ── sealed period ──────────────────────────────────────────────────────────


def candle(day: date) -> Candle:
    moment = datetime(day.year, day.month, day.day, 10, 0, tzinfo=UTC)
    return Candle(
        open_time=moment,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10,
    )


def test_sealed_period_is_never_returned_by_the_loader():
    candles = [candle(date(2026, 9, 30)), candle(date(2026, 10, 1)), candle(date(2026, 11, 9))]
    kept = before_sealed(candles, date(2026, 10, 1))
    assert [item.open_time.date() for item in kept] == [date(2026, 9, 30)]


def test_sealed_cut_is_strict_on_the_boundary():
    kept = before_sealed([candle(date(2026, 10, 1))], date(2026, 10, 1))
    assert kept == []


def test_sealed_rows_applies_to_every_ticker():
    rows = {
        "SBER": [candle(date(2026, 9, 30)), candle(date(2026, 10, 2))],
        "GAZP": [candle(date(2026, 10, 5))],
    }
    kept = sealed_rows(rows, date(2026, 10, 1))
    assert len(kept["SBER"]) == 1
    assert kept["GAZP"] == []


# ── outcome order ──────────────────────────────────────────────────────────


def test_rejected_when_the_upper_bound_is_below_the_target():
    outcome = decide_outcome(
        pool=interval(-5.0, -12.0, 3.0),
        base=base_verdict(INCONCLUSIVE),
        mde_adjusted=20.0,
        target_edge_bps=10.0,
    )
    assert outcome.status == REJECTED
    assert "below the target" in outcome.reasons[0]


def test_rejected_keeps_the_base_gates():
    outcome = decide_outcome(
        pool=interval(5.0, 1.0, 9.0),
        base=base_verdict(REJECTED, reason="null test reports optimism"),
        mde_adjusted=5.0,
        target_edge_bps=10.0,
    )
    assert outcome.status == REJECTED
    assert "null test reports optimism" in outcome.reasons[0]


def test_underpowered_when_the_mde_exceeds_the_target_and_the_interval_straddles_it():
    outcome = decide_outcome(
        pool=interval(2.66, -10.21, 16.44),
        base=base_verdict(INCONCLUSIVE),
        mde_adjusted=27.0,
        target_edge_bps=10.0,
    )
    assert outcome.status == UNDERPOWERED
    assert "cannot see the edge" in outcome.reasons[0]


def test_underpowered_is_not_reported_when_the_interval_proves_the_target():
    outcome = decide_outcome(
        pool=interval(30.0, 12.0, 48.0),
        base=base_verdict(WORKS),
        mde_adjusted=27.0,
        target_edge_bps=10.0,
        sealed_confirmed=True,
    )
    assert outcome.status == WORKS


def test_inconclusive_when_power_is_enough_but_zero_is_not_excluded():
    outcome = decide_outcome(
        pool=interval(2.66, -10.21, 16.44),
        base=base_verdict(INCONCLUSIVE),
        mde_adjusted=8.0,
        target_edge_bps=10.0,
    )
    assert outcome.status == INCONCLUSIVE


def test_fragile_comes_after_inconclusive():
    outcome = decide_outcome(
        pool=interval(12.0, 1.0, 23.0),
        base=base_verdict(FRAGILE, reason="cost-sensitive"),
        mde_adjusted=8.0,
        target_edge_bps=10.0,
    )
    assert outcome.status == FRAGILE
    assert "cost-sensitive" in outcome.reasons[0]


def test_works_without_the_sealed_period_is_downgraded_to_fragile():
    outcome = decide_outcome(
        pool=interval(12.0, 1.0, 23.0),
        base=base_verdict(WORKS),
        mde_adjusted=8.0,
        target_edge_bps=10.0,
        sealed_confirmed=False,
    )
    assert outcome.status == FRAGILE
    assert "sealed period was not opened" in outcome.reasons[0]


def test_works_requires_the_sealed_confirmation():
    outcome = decide_outcome(
        pool=interval(12.0, 1.0, 23.0),
        base=base_verdict(WORKS),
        mde_adjusted=8.0,
        target_edge_bps=10.0,
        sealed_confirmed=True,
    )
    assert outcome.status == WORKS
    assert outcome.reasons == ()


def test_confirm_interval_below_the_target_is_rejected():
    outcome = decide_outcome(
        pool=interval(12.0, 1.0, 23.0),
        base=base_verdict(FRAGILE),
        mde_adjusted=8.0,
        target_edge_bps=10.0,
        confirm=interval(-2.0, -8.0, 4.0),
    )
    assert outcome.status == REJECTED


def test_unknown_base_status_is_an_error():
    with pytest.raises(ValueError, match="unknown base verdict status"):
        decide_outcome(
            pool=interval(12.0, 1.0, 23.0),
            base=base_verdict("bogus"),
            mde_adjusted=8.0,
            target_edge_bps=10.0,
        )


# ── report ─────────────────────────────────────────────────────────────────


def report(recorded: bool = True, warning: str | None = None) -> EvaluateReport:
    outcome = Outcome(
        status=UNDERPOWERED,
        reasons=("MDE 27.0 bps > target 10 bps; the sample cannot see the edge we need",),
        mde_bps=20.0,
        mde_adjusted_bps=27.0,
        sigma_bps=295.0,
        trades_needed=6832,
    )
    return EvaluateReport(
        strategy="dual_ma_crossover_after_testing",
        timeframe="4h",
        hypothesis="Momentum continues after the pullback",
        outcome=outcome,
        fit=interval(4.1, -6.0, 14.2),
        pool=interval(2.66, -10.21, 16.44),
        development=interval(-13.29, -42.45, 20.23, trades=402),
        costs_doubled=interval(-6.47, -19.53, 7.4),
        tp_penalty=interval(0.72, -12.08, 13.52),
        without_best=interval(-0.16, -13.77, 14.16, trades=1626),
        base_status=INCONCLUSIVE,
        null_test="ok",
        heatmap_status="no_plateau",
        heatmap_plateau=False,
        tradable=True,
        dividend_coverage={"confirmed": 20},
        ideas_used=0,
        ideas_limit=20,
        grid_cells=1,
        n_trials=80,
        target_edge_bps=10.0,
        power=0.8,
        confirm_alpha=0.05,
        screening_alpha=0.2,
        sealed_start=date(2026, 10, 1),
        sealed_accessed=False,
        recorded=recorded,
        journal_warning=warning,
    )


def test_report_has_every_mandatory_section():
    text = render_report(report())
    for heading in (
        "# Evaluate:",
        "## Verdict: underpowered",
        "## Numbers",
        "## Power",
        "## Checks",
        "## Assumptions",
        "## What would change the verdict",
        "## Biases",
    ):
        assert heading in text


def test_report_prints_the_sealed_period_as_not_accessed():
    text = render_report(report())
    assert "not accessed (starts 2026-10-01)" in text


def test_report_labels_the_pool_as_before_the_sealed_period():
    text = render_report(report())
    assert "pool (before sealed)" in text
    assert "scripts/verdict.py` runs the full history" in text


def test_dry_run_report_is_marked_as_not_recorded():
    text = render_report(report(recorded=False))
    assert "NOT RECORDED (dry run)" in text
    assert "this run adds 0" in text


def test_report_names_a_number_in_what_would_change_the_verdict():
    text = render_report(report())
    assert "~6832 trades" in text
    assert "10 bps target" in text


def test_journal_warning_is_printed():
    text = render_report(report(warning="journal: 1 uncommitted row(s)"))
    assert "1 uncommitted row(s)" in text


# ── journal git state ──────────────────────────────────────────────────────


def test_parse_numstat_counts_added_lines():
    assert parse_numstat_added("") == 0
    assert parse_numstat_added("3\t0\tjournal/hypotheses.jsonl\n") == 3
    assert parse_numstat_added("1\t0\ta.jsonl\n2\t0\tb.jsonl\n") == 3


def test_parse_numstat_skips_binary_rows():
    assert parse_numstat_added("-\t-\tfile.bin\n") == 0


def test_clean_journal_has_no_warning():
    assert JournalGitState(available=True, uncommitted_rows=0).warning() is None


def test_one_uncommitted_row_is_reported():
    warning = JournalGitState(available=True, uncommitted_rows=1).warning()
    assert warning is not None and "1 uncommitted row" in warning


def test_two_uncommitted_rows_are_reported():
    warning = JournalGitState(available=True, uncommitted_rows=2).warning()
    assert warning is not None and "2 uncommitted row" in warning


def test_missing_git_is_not_silent():
    warning = JournalGitState(available=False, uncommitted_rows=0).warning()
    assert warning == "git unavailable, check the journal manually"


# ── thresholds ─────────────────────────────────────────────────────────────


def test_thresholds_are_read_from_the_validation_file(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(
        '{"version": "6", "evaluate": {"target_edge_bps": 12.0, "power": 0.9,'
        ' "sealed_start": "2026-11-01", "budget": 10}}',
        encoding="utf-8",
    )
    thresholds = load_evaluate_thresholds(path)
    assert thresholds.target_edge_bps == 12.0
    assert thresholds.power == 0.9
    assert thresholds.sealed_start == date(2026, 11, 1)
    assert thresholds.budget == 10
    assert thresholds.fingerprint.startswith("6+")


def test_invalid_power_is_refused():
    with pytest.raises(ValueError, match="power"):
        EvaluateThresholds(power=1.5)


def test_invalid_target_is_refused():
    with pytest.raises(ValueError, match="target_edge_bps"):
        EvaluateThresholds(target_edge_bps=0.0)


# ── legacy estimate ────────────────────────────────────────────────────────


def test_legacy_estimate_is_the_sum_of_the_adr_sources():
    from scripts.evaluate import LEGACY_NOTES, LEGACY_VARIANTS

    assert LEGACY_VARIANTS == 4 + 42 + 1 + 25 + 2 + 3 + 2
    assert LEGACY_NOTES.startswith("lower bound")
    for source in (
        "ADR-015",
        "ADR-021",
        "ADR-022",
        "ADR-023",
        "ADR-028",
        "ADR-030",
        "ADR-031",
    ):
        assert source in LEGACY_NOTES
    assert "4h and 1d" in LEGACY_NOTES
