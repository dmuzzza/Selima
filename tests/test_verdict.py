"""Verdict gates: each condition, the order of the groups, flags, report."""

import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from src.backtest.verdict import (
    FRAGILE,
    INCONCLUSIVE,
    REJECTED,
    WORKS,
    IntervalResult,
    VerdictInputs,
    VerdictThresholds,
    evaluate_verdict,
    load_verdict_thresholds,
    render_markdown,
)


def interval(trades=500, net=20.0, low=5.0, high=35.0) -> IntervalResult:
    return IntervalResult(trades=trades, net_bps=net, ci_low=low, ci_high=high)


BASE_INPUTS = VerdictInputs(
    pool=interval(),
    oos=interval(trades=300, net=15.0, low=2.0, high=28.0),
    pool_without_best=interval(trades=480, net=18.0, low=3.0, high=33.0),
    costs_doubled=interval(trades=500, net=8.0, low=1.0, high=15.0),
    tp_penalty=interval(trades=500, net=14.0, low=2.0, high=26.0),
    null_test="ok",
    heatmap_status="plateau",
    heatmap_plateau=True,
    tradable=True,
    best_ticker="ALRS",
    best_contribution_percent=40.0,
)


def inputs(**overrides: Any) -> VerdictInputs:
    """Everything passes: the baseline for one-gate mutations."""
    return replace(BASE_INPUTS, **overrides)


THRESHOLDS = VerdictThresholds(
    min_total_trades=200,
    min_oos_trades=100,
    holdout_start=date(2025, 10, 1),
    holdout_end=date(2026, 10, 1),
    cost_multiplier=2.0,
    tp_penalty_bps=4.0,
    oos_ratio_min_percent=50.0,
)


def codes(result) -> list[str]:
    return [reason.code for reason in result.reasons]


# ── thresholds ─────────────────────────────────────────────────────────────


def test_baseline_passes_every_gate():
    result = evaluate_verdict(inputs(), THRESHOLDS)

    assert result.status == WORKS
    assert result.reasons == ()
    assert result.is_works


def test_load_thresholds_reads_section_and_fingerprint(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(
        json.dumps(
            {
                "version": "5",
                "verdict": {
                    "min_total_trades": 200,
                    "min_oos_trades": 100,
                    "holdout_start": "2025-10-01",
                    "holdout_end": "2026-10-01",
                    "cost_multiplier": 2.0,
                    "tp_penalty_bps": 4.0,
                    "oos_ratio_min_percent": 50.0,
                    "n_trials": 7,
                },
            }
        ),
        encoding="utf-8",
    )

    thresholds = load_verdict_thresholds(path)

    assert thresholds.min_oos_trades == 100
    assert thresholds.holdout_start == date(2025, 10, 1)
    assert thresholds.holdout_end == date(2026, 10, 1)
    assert thresholds.n_trials == 7
    assert thresholds.fingerprint.startswith("5+")


def test_load_thresholds_requires_section(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps({"version": "1"}), encoding="utf-8")

    with pytest.raises(ValueError, match="verdict"):
        load_verdict_thresholds(path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_total_trades": 0}, "trade thresholds"),
        ({"cost_multiplier": 1.0}, "cost_multiplier"),
        ({"tp_penalty_bps": -1.0}, "tp_penalty_bps"),
        ({"oos_ratio_min_percent": 0.0}, "oos_ratio_min_percent"),
        ({"n_trials": -1}, "n_trials"),
        (
            {"holdout_start": date(2026, 1, 1), "holdout_end": date(2026, 1, 1)},
            "holdout_start",
        ),
    ],
)
def test_thresholds_reject_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        VerdictThresholds(**kwargs)


def test_interval_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="ci_low"):
        IntervalResult(trades=10, net_bps=1.0, ci_low=5.0, ci_high=1.0)


# ── rejected ───────────────────────────────────────────────────────────────


def test_null_optimism_rejects_the_whole_report():
    result = evaluate_verdict(inputs(null_test="optimism"), THRESHOLDS)

    assert result.status == REJECTED
    assert codes(result) == ["A1"]


def test_gross_report_is_rejected():
    result = evaluate_verdict(inputs(tradable=False), THRESHOLDS)

    assert result.status == REJECTED
    assert "A2" in codes(result)


def test_negative_pool_is_rejected():
    result = evaluate_verdict(
        inputs(pool=interval(net=-10.0, low=-20.0, high=-1.0)), THRESHOLDS
    )

    assert result.status == REJECTED
    assert "A3" in codes(result)


def test_negative_holdout_is_rejected():
    result = evaluate_verdict(
        inputs(oos=interval(trades=300, net=-5.0, low=-12.0, high=-1.0)), THRESHOLDS
    )

    assert result.status == REJECTED
    assert "A4" in codes(result)


def test_all_rejected_reasons_are_reported_together():
    result = evaluate_verdict(
        inputs(
            null_test="optimism",
            tradable=False,
            pool=interval(net=-10.0, low=-20.0, high=-1.0),
        ),
        THRESHOLDS,
    )

    assert result.status == REJECTED
    assert codes(result) == ["A1", "A2", "A3"]


# ── inconclusive ───────────────────────────────────────────────────────────


def test_too_few_pool_trades_is_inconclusive():
    result = evaluate_verdict(inputs(pool=interval(trades=150)), THRESHOLDS)

    assert result.status == INCONCLUSIVE
    assert "B1" in codes(result)


def test_missing_holdout_is_inconclusive():
    result = evaluate_verdict(inputs(oos=None), THRESHOLDS)

    assert result.status == INCONCLUSIVE
    assert "B2" in codes(result)


def test_thin_holdout_is_inconclusive():
    result = evaluate_verdict(inputs(oos=interval(trades=60)), THRESHOLDS)

    assert result.status == INCONCLUSIVE
    assert "B2" in codes(result)


def test_pool_interval_containing_zero_is_inconclusive():
    result = evaluate_verdict(
        inputs(pool=interval(net=2.0, low=-5.0, high=9.0)), THRESHOLDS
    )

    assert result.status == INCONCLUSIVE
    assert "B3" in codes(result)


def test_holdout_interval_containing_zero_is_inconclusive():
    result = evaluate_verdict(
        inputs(oos=interval(trades=300, net=2.0, low=-3.0, high=7.0)), THRESHOLDS
    )

    assert result.status == INCONCLUSIVE
    assert "B4" in codes(result)


@pytest.mark.parametrize("null_state", ["inconclusive", "missing"])
def test_unconfirmed_null_test_is_inconclusive(null_state):
    result = evaluate_verdict(inputs(null_test=null_state), THRESHOLDS)

    assert result.status == INCONCLUSIVE
    assert "B5" in codes(result)


def test_inconclusive_beats_fragile():
    result = evaluate_verdict(
        inputs(
            pool=interval(net=2.0, low=-5.0, high=9.0),
            heatmap_plateau=False,
            heatmap_status="no_plateau",
        ),
        THRESHOLDS,
    )

    assert result.status == INCONCLUSIVE
    assert codes(result) == ["B3"]


# ── fragile ────────────────────────────────────────────────────────────────


def test_result_depending_on_one_ticker_is_fragile():
    result = evaluate_verdict(
        inputs(
            pool_without_best=interval(trades=480, net=0.5, low=-2.0, high=3.0)
        ),
        THRESHOLDS,
    )

    assert result.status == FRAGILE
    assert "C1" in codes(result)
    assert "ALRS" in result.reasons[0].message


def test_missing_leave_one_out_is_fragile():
    result = evaluate_verdict(inputs(pool_without_best=None), THRESHOLDS)

    assert result.status == FRAGILE
    assert "C1" in codes(result)


@pytest.mark.parametrize(
    "status", ["no_plateau", "insufficient_neighbours", "anchor_thin", "missing"]
)
def test_no_plateau_at_the_chosen_cell_is_fragile(status):
    result = evaluate_verdict(
        inputs(heatmap_status=status, heatmap_plateau=False), THRESHOLDS
    )

    assert result.status == FRAGILE
    assert "C2" in codes(result)


def test_missing_heatmap_is_fragile():
    result = evaluate_verdict(
        inputs(heatmap_status="missing", heatmap_plateau=None), THRESHOLDS
    )

    assert result.status == FRAGILE
    assert "C2" in codes(result)


def test_costs_doubled_turning_negative_is_fragile_not_rejected():
    result = evaluate_verdict(
        inputs(costs_doubled=interval(net=-3.0, low=-8.0, high=-0.5)), THRESHOLDS
    )

    assert result.status == FRAGILE
    assert codes(result) == ["C3"]


def test_holdout_below_the_ratio_floor_is_fragile():
    # Pool 20 bps, floor 50% -> 10 bps; holdout gives 8 bps.
    result = evaluate_verdict(
        inputs(oos=interval(trades=300, net=8.0, low=1.0, high=15.0)), THRESHOLDS
    )

    assert result.status == FRAGILE
    assert "C4" in codes(result)


def test_tp_penalty_containing_zero_is_fragile():
    result = evaluate_verdict(
        inputs(tp_penalty=interval(net=2.0, low=-1.0, high=5.0)), THRESHOLDS
    )

    assert result.status == FRAGILE
    assert codes(result) == ["C5"]


def test_missing_sensitivity_runs_are_fragile():
    result = evaluate_verdict(inputs(costs_doubled=None, tp_penalty=None), THRESHOLDS)

    assert result.status == FRAGILE
    assert codes(result) == ["C3", "C5"]


def test_fragile_reports_every_triggered_gate():
    result = evaluate_verdict(
        inputs(
            pool_without_best=interval(trades=480, net=0.5, low=-2.0, high=3.0),
            heatmap_plateau=False,
            heatmap_status="no_plateau",
            costs_doubled=interval(net=-3.0, low=-8.0, high=-0.5),
            tp_penalty=interval(net=1.0, low=-2.0, high=4.0),
        ),
        THRESHOLDS,
    )

    assert result.status == FRAGILE
    assert codes(result) == ["C1", "C2", "C3", "C5"]


# ── flags and report ───────────────────────────────────────────────────────


def test_flags_do_not_change_the_status():
    result = evaluate_verdict(
        inputs(gap_verdict_changed=True, n_trials=25), THRESHOLDS
    )

    assert result.status == WORKS
    assert result.flags["gap_verdict_changed"] is True
    assert result.flags["n_trials"] == 25


def test_n_trials_falls_back_to_the_config_value():
    result = evaluate_verdict(inputs(n_trials=None), VerdictThresholds(n_trials=11))

    assert result.flags["n_trials"] == 11


def test_flags_include_oos_ratio():
    result = evaluate_verdict(inputs(), THRESHOLDS)

    assert result.flags["oos_ratio_percent"] == pytest.approx(75.0)


def test_markdown_shows_status_and_reasons():
    result = evaluate_verdict(
        inputs(heatmap_plateau=False, heatmap_status="no_plateau"), THRESHOLDS
    )

    text = render_markdown(result)

    assert "FRAGILE" in text
    assert "**C2**" in text
    assert "n_trials" in text
    assert "verdict_version" in text


def test_markdown_marks_missing_inputs():
    result = evaluate_verdict(inputs(oos=None, costs_doubled=None), THRESHOLDS)

    text = render_markdown(result)

    assert "| holdout | — | — | missing |" in text


def test_markdown_lists_known_assumptions():
    """A positive verdict must still say which costs are unverified (T2-17a)."""
    text = render_markdown(evaluate_verdict(inputs(), THRESHOLDS))

    assert "## Assumptions" in text
    assert "T2-17a" in text
    assert "ex-dividend" in text


def test_describe_is_json_serialisable():
    result = evaluate_verdict(inputs(), THRESHOLDS)

    payload = json.loads(json.dumps(result.describe()))

    assert payload["status"] == WORKS
    assert payload["inputs"]["pool"]["trades"] == 500
    assert payload["flags"]["best_ticker"] == "ALRS"
