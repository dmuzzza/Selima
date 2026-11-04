"""pnl_baseline: skipped runs must not look like flat results.

A run without candles or without an exit policy carries `total_pnl = None` and
`status != "ok"`. Aggregates exclude it by status, not by a zero value, so a
per-run average cannot be pulled down by a run that never happened.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pnl_baseline", ROOT / "scripts" / "pnl_baseline.py")
assert SPEC and SPEC.loader
pnl_baseline = importlib.util.module_from_spec(SPEC)
# dataclasses resolve annotations through sys.modules, so register before exec.
sys.modules[SPEC.name] = pnl_baseline
SPEC.loader.exec_module(pnl_baseline)


def ok_run(ticker: str, timeframe: str, *, trades: int, total_pnl: float) -> dict:
    return {
        "figi": f"FIGI-{ticker}",
        "ticker": ticker,
        "timeframe": timeframe,
        "status": "ok",
        "trades": trades,
        "closed": trades,
        "total_pnl": total_pnl,
    }


def skipped_run(ticker: str, timeframe: str, status: str, reason: str) -> dict:
    return {
        "figi": f"FIGI-{ticker}",
        "ticker": ticker,
        "timeframe": timeframe,
        "status": status,
        "reason": reason,
        "trades": 0,
        "closed": 0,
        "total_pnl": None,
    }


def test_summarize_excludes_skipped_from_totals():
    runs = [
        ok_run("SBER", "4h", trades=57, total_pnl=14_882.96),
        ok_run("GAZP", "4h", trades=47, total_pnl=7_252.24),
        skipped_run("RTKM", "4h", "no_exit_policy", "no TP/SL for ticker 'RTKM'"),
        skipped_run("CNRU", "4h", "no_exit_policy", "no TP/SL for ticker 'CNRU'"),
        skipped_run("MSNG", "1d", "no_data", "no candles"),
    ]

    summary = pnl_baseline.summarize(runs)

    # Hand-computed: only the two ok runs contribute.
    assert summary["total_pnl"] == 14_882.96 + 7_252.24
    assert summary["total_trades"] == 57 + 47
    assert summary["runs"] == 5
    assert summary["runs_ok"] == 2
    assert summary["runs_skipped"] == 3
    assert summary["skipped_by_status"] == {"no_exit_policy": 2, "no_data": 1}


def test_summarize_lists_skipped_runs_with_reasons():
    runs = [
        ok_run("SBER", "4h", trades=1, total_pnl=10.0),
        skipped_run("RTKM", "4h", "no_exit_policy", "no TP/SL for ticker 'RTKM'"),
    ]

    skipped = pnl_baseline.summarize(runs)["skipped"]

    assert skipped == [
        {
            "ticker": "RTKM",
            "timeframe": "4h",
            "status": "no_exit_policy",
            "reason": "no TP/SL for ticker 'RTKM'",
        }
    ]


def test_summarize_all_skipped_gives_zero_totals_not_error():
    runs = [skipped_run("RTKM", "4h", "no_exit_policy", "no TP/SL")]

    summary = pnl_baseline.summarize(runs)

    assert summary["total_pnl"] == 0
    assert summary["total_trades"] == 0
    assert summary["runs_ok"] == 0


def test_summarize_keeps_real_zero_result():
    """A flat run is an ok run: it must stay in the counts and in the average."""
    runs = [
        ok_run("SBER", "4h", trades=3, total_pnl=0.0),
        ok_run("GAZP", "4h", trades=1, total_pnl=100.0),
    ]

    summary = pnl_baseline.summarize(runs)

    assert summary["runs_ok"] == 2
    assert summary["runs_skipped"] == 0
    assert summary["total_trades"] == 4
    # Per-run average over ok runs only: (0.0 + 100.0) / 2.
    assert summary["total_pnl"] / summary["runs_ok"] == 50.0


def test_compare_handles_skipped_runs_without_crashing(capsys):
    """A run that became skipped has no PnL: the diff prints n/a, not a traceback."""
    before = {
        "costs": {"label": "NET"},
        "results": [ok_run("RTKM", "4h", trades=5, total_pnl=500.0)],
        "total_pnl": 500.0,
        "runs_skipped": 0,
    }
    after = {
        "costs": {"label": "NET"},
        "results": [skipped_run("RTKM", "4h", "no_exit_policy", "no TP/SL")],
        "total_pnl": 0.0,
        "runs_skipped": 1,
    }

    changed = pnl_baseline.compare(before, after)

    out = capsys.readouterr().out
    assert changed == 1
    assert "n/a" in out
    assert "skipped 0 -> 1" in out
