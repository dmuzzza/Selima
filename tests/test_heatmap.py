"""Heatmap: cell scoring, plateau decision, train-window isolation, reports."""

import csv
import json
from datetime import datetime, timedelta

import pytest

from src.backtest.heatmap import (
    HeatmapThresholds,
    analyze_plateau,
    build_grid,
    collect_full_history_trades,
    collect_train_trades,
    load_thresholds,
    render_markdown,
    score_cell,
    score_cells,
    write_cells_csv,
)
from src.backtest.walk_forward import WalkForwardWindow
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

DAY = timedelta(days=1)
START = datetime(2024, 1, 1)


def make_trade(
    entry_time: datetime = START,
    pnl: float = 100.0,
    *,
    quantity: float = 10_000.0,
    entry_index: int = 0,
    signal_type: str = "BULLISH",
    open_trade: bool = False,
) -> Trade:
    return Trade(
        trade_id=1,
        entry_time=entry_time,
        entry_index=entry_index,
        entry_price=100.0,
        signal_type=signal_type,
        tp_level=101.0,
        sl_level=99.0,
        quantity=quantity,
        exit_time=None if open_trade else entry_time + DAY,
        exit_index=None if open_trade else entry_index + 1,
        exit_price=None if open_trade else 101.0,
        exit_type=None if open_trade else "TP",
        pnl_abs=None if open_trade else pnl,
        pnl_net=None if open_trade else pnl,
    )


def make_candles(days: int) -> list[Candle]:
    return [
        Candle(
            open_time=START + i * DAY,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100,
        )
        for i in range(days)
    ]


def cell(tp: float, sl: float, bps: float, trades: int = 40):
    """Cell built from synthetic trades: `bps` per trade on a 10 000 RUB position."""
    return score_cell(
        tp,
        sl,
        [make_trade(pnl=bps * 1.0) for _ in range(trades)],
    )


# ── thresholds config ──────────────────────────────────────────────────────


def write_thresholds(path, **overrides):
    payload = {
        "version": "7",
        "heatmap": {
            "min_trades_per_cell": 30,
            "plateau_margin_percent": 20.0,
            "plateau_share": 0.65,
            "min_neighbours_evaluated": 4,
            **overrides,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_thresholds_reads_values_and_fingerprint(tmp_path):
    path = write_thresholds(tmp_path / "validation.json", min_trades_per_cell=25)

    thresholds = load_thresholds(path)

    assert thresholds.min_trades_per_cell == 25
    assert thresholds.plateau_margin_percent == 20.0
    assert thresholds.plateau_share == 0.65
    assert thresholds.fingerprint.startswith("7+")
    assert len(thresholds.fingerprint.split("+")[1]) == 12


def test_load_thresholds_requires_heatmap_section(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps({"version": "1"}), encoding="utf-8")

    with pytest.raises(ValueError, match="heatmap"):
        load_thresholds(path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_trades_per_cell": 0}, "min_trades_per_cell"),
        ({"plateau_margin_percent": 100.0}, "plateau_margin_percent"),
        ({"plateau_share": 0.0}, "plateau_share"),
        ({"min_neighbours_evaluated": 0}, "min_neighbours_evaluated"),
    ],
)
def test_thresholds_reject_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        HeatmapThresholds(**kwargs)


# ── cell scoring ───────────────────────────────────────────────────────────


def test_score_cell_reports_net_expectancy_in_bps():
    trades = [make_trade(pnl=100.0), make_trade(pnl=-50.0)]

    result = score_cell(2.0, 1.0, trades)

    # 100 RUB / 10 000 RUB = 100 bps; -50 RUB = -50 bps; mean = 25 bps.
    assert result.expectancy_bps == pytest.approx(25.0)
    assert result.pnl_net == pytest.approx(50.0)
    assert result.trades == 2
    assert result.win_rate == pytest.approx(50.0)


def test_score_cell_ignores_open_trades():
    trades = [make_trade(pnl=100.0), make_trade(pnl=0.0, open_trade=True)]

    result = score_cell(2.0, 1.0, trades)

    assert result.trades == 1
    assert result.expectancy_bps == pytest.approx(100.0)


def test_score_cell_skips_zero_quantity():
    trades = [make_trade(pnl=100.0, quantity=0.0), make_trade(pnl=200.0)]

    result = score_cell(2.0, 1.0, trades)

    assert result.trades == 2
    assert result.expectancy_bps == pytest.approx(200.0)


def test_score_cells_is_sorted_by_tp_then_sl():
    cells = score_cells(
        {
            (3.0, 1.0): [make_trade()],
            (1.0, 2.0): [make_trade()],
            (1.0, 1.0): [make_trade()],
        }
    )

    assert [(c.tp, c.sl) for c in cells] == [(1.0, 1.0), (1.0, 2.0), (3.0, 1.0)]


# ── plateau decision ───────────────────────────────────────────────────────

TP = [1.0, 2.0, 3.0]
SL = [0.5, 1.0, 2.0]


def grid_cells(center_bps: float, other_bps: float, *, trades: int = 40):
    return [
        cell(tp, sl, center_bps if (tp, sl) == (2.0, 1.0) else other_bps, trades)
        for tp in TP
        for sl in SL
    ]


def test_plateau_when_center_and_neighbours_pass():
    thresholds = HeatmapThresholds()

    verdict = analyze_plateau(grid_cells(100.0, 90.0), TP, SL, thresholds)

    assert verdict.status == "plateau"
    assert verdict.best == cell(2.0, 1.0, 100.0)
    assert verdict.evaluated_neighbours == 8
    assert verdict.passing_neighbours == 8
    assert verdict.threshold_bps == pytest.approx(80.0)


def test_no_plateau_when_half_of_neighbours_fail():
    thresholds = HeatmapThresholds()
    cells = []
    for tp in TP:
        for sl in SL:
            if (tp, sl) == (2.0, 1.0):
                cells.append(cell(tp, sl, 100.0))
            elif tp < 2.0:
                cells.append(cell(tp, sl, 90.0))
            else:
                cells.append(cell(tp, sl, 50.0))

    verdict = analyze_plateau(cells, TP, SL, thresholds)

    assert verdict.status == "no_plateau"
    assert verdict.share == pytest.approx(3 / 8)


def test_no_positive_cell_blocks_plateau_even_if_neighbours_are_close():
    thresholds = HeatmapThresholds()

    verdict = analyze_plateau(grid_cells(-10.0, -12.0), TP, SL, thresholds)

    assert verdict.status == "no_positive_cell"
    assert verdict.is_plateau is False


def test_thin_neighbours_are_excluded_from_the_share():
    thresholds = HeatmapThresholds(min_trades_per_cell=30, min_neighbours_evaluated=4)
    cells = [
        cell(tp, sl, 100.0 if (tp, sl) == (2.0, 1.0) else 90.0, trades=5 if tp == 3.0 else 40)
        for tp in TP
        for sl in SL
    ]

    verdict = analyze_plateau(cells, TP, SL, thresholds)

    # All three cells with tp=3.0 are thin and drop out; 5 neighbours remain.
    assert verdict.status == "plateau"
    assert verdict.evaluated_neighbours == 5
    assert verdict.passing_neighbours == 5


def test_insufficient_neighbours_is_inconclusive_not_plateau():
    thresholds = HeatmapThresholds(min_trades_per_cell=30, min_neighbours_evaluated=6)
    cells = [
        cell(tp, sl, 100.0 if (tp, sl) == (2.0, 1.0) else 90.0, trades=5 if tp == 3.0 else 40)
        for tp in TP
        for sl in SL
    ]

    verdict = analyze_plateau(cells, TP, SL, thresholds)

    assert verdict.status == "insufficient_neighbours"
    assert verdict.is_plateau is False
    assert verdict.evaluated_neighbours == 5


def test_no_eligible_cells():
    verdict = analyze_plateau(grid_cells(100.0, 90.0, trades=2), TP, SL, HeatmapThresholds())

    assert verdict.status == "no_eligible_cells"
    assert verdict.best is None


def test_edge_cell_has_only_existing_neighbours():
    thresholds = HeatmapThresholds(min_neighbours_evaluated=3)
    cells = [
        cell(tp, sl, 100.0 if (tp, sl) == (1.0, 0.5) else 90.0)
        for tp in TP
        for sl in SL
    ]

    verdict = analyze_plateau(cells, TP, SL, thresholds)

    assert verdict.best is not None and verdict.best.tp == 1.0
    assert len(verdict.neighbours) == 3
    assert verdict.status == "plateau"


def test_best_on_edge_is_flagged():
    thresholds = HeatmapThresholds(min_neighbours_evaluated=3)
    cells = [
        cell(tp, sl, 100.0 if (tp, sl) == (3.0, 2.0) else 90.0)
        for tp in TP
        for sl in SL
    ]

    verdict = analyze_plateau(cells, TP, SL, thresholds)

    assert verdict.best_on_edge is True


def test_center_best_is_not_flagged_as_edge():
    verdict = analyze_plateau(grid_cells(100.0, 90.0), TP, SL, HeatmapThresholds())

    assert verdict.best_on_edge is False


# ── train-window collection ────────────────────────────────────────────────


def test_collect_train_trades_uses_train_period_only():
    candles = make_candles(100)
    window = WalkForwardWindow(
        train_start=START,
        train_end=START + 40 * DAY,
        test_start=START + 40 * DAY,
        test_end=START + 60 * DAY,
    )

    def runner(config, candles_slice):
        return [make_trade(candles_slice[-1].open_time, float(config["tp_percent"]))]

    result = collect_train_trades(candles, [window], runner=runner, grid=build_grid([1.0], [1.0]))

    trade = result[(1.0, 1.0)][0]
    assert trade.entry_time < window.train_end
    assert trade.entry_time >= window.train_start


def test_collect_train_trades_dedupes_overlapping_windows():
    candles = make_candles(100)
    windows = [
        WalkForwardWindow(START, START + 40 * DAY, START + 40 * DAY, START + 60 * DAY),
        WalkForwardWindow(START + 20 * DAY, START + 60 * DAY, START + 60 * DAY, START + 80 * DAY),
    ]

    def runner(config, candles_slice):
        return [make_trade(START + 30 * DAY, 10.0, entry_index=30)]

    grid = build_grid([1.0], [1.0])
    deduped = collect_train_trades(candles, windows, runner=runner, grid=grid)
    raw = collect_train_trades(candles, windows, runner=runner, grid=grid, dedupe=False)

    assert len(deduped[(1.0, 1.0)]) == 1
    assert len(raw[(1.0, 1.0)]) == 2


def test_collect_train_trades_drops_warmup_trades():
    candles = make_candles(100)
    window = WalkForwardWindow(
        START + 20 * DAY, START + 60 * DAY, START + 60 * DAY, START + 80 * DAY
    )

    def runner(config, candles_slice):
        # First candle of the slice is warmup data, before train_start.
        return [make_trade(candles_slice[0].open_time, 10.0)]

    result = collect_train_trades(
        candles,
        [window],
        runner=runner,
        grid=build_grid([1.0], [1.0]),
        warmup_bars=10,
    )

    assert result[(1.0, 1.0)] == []


def test_collect_full_history_trades_scores_every_closed_trade():
    candles = make_candles(30)

    def runner(config, candles_slice):
        return [
            make_trade(candles_slice[0].open_time, 10.0),
            make_trade(candles_slice[1].open_time, 0.0, open_trade=True),
        ]

    result = collect_full_history_trades(candles, runner=runner, grid=build_grid([1.0], [1.0]))

    assert len(result[(1.0, 1.0)]) == 1


# ── reports ────────────────────────────────────────────────────────────────


def test_render_markdown_prints_thresholds_and_marks_thin_cells():
    thresholds = HeatmapThresholds(min_trades_per_cell=30)
    cells = [cell(1.0, 0.5, 12.5, trades=5), cell(2.0, 1.0, 40.0, trades=40)]
    verdict = analyze_plateau(cells, [1.0, 2.0], [0.5, 1.0], thresholds)

    text = render_markdown(
        title="Heatmap",
        cells=cells,
        tp_values=[1.0, 2.0],
        sl_values=[0.5, 1.0],
        thresholds=thresholds,
        verdict=verdict,
        meta={"block": "TRAIN"},
    )

    assert "min_trades_per_cell" in text
    assert "thresholds_version" in text
    assert "12.50 / 5*" in text
    assert "diagnostics" not in text.lower()
    assert "does not prove an edge" in text


def test_write_cells_csv_flags_best_and_eligible(tmp_path):
    thresholds = HeatmapThresholds(min_trades_per_cell=30)
    cells = [cell(1.0, 0.5, 5.0, trades=5), cell(2.0, 1.0, 40.0, trades=40)]
    verdict = analyze_plateau(cells, [1.0, 2.0], [0.5, 1.0], thresholds)

    path = write_cells_csv(
        tmp_path / "cells.csv", cells=cells, thresholds=thresholds, verdict=verdict
    )

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    by_key = {(row["tp_percent"], row["sl_percent"]): row for row in rows}
    assert by_key[("1", "0.5")]["eligible"] == "0"
    assert by_key[("2", "1")]["is_best"] == "1"
    assert by_key[("2", "1")]["eligible"] == "1"
    assert rows[0]["thresholds_version"] == thresholds.fingerprint
