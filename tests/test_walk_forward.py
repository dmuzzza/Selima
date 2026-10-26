"""Walk-forward validation: windows, parameter isolation, warmup, aggregates."""

from datetime import datetime, timedelta

import pytest

from src.backtest.walk_forward import (
    WalkForwardWindow,
    generate_windows,
    run_walk_forward,
)
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

DAY = timedelta(days=1)


def make_candles(start: datetime, days: int) -> list[Candle]:
    return [
        Candle(
            open_time=start + i * DAY,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100,
        )
        for i in range(days)
    ]


def make_trade(entry_time: datetime, pnl: float | None = 10.0) -> Trade:
    return Trade(
        trade_id=1,
        entry_time=entry_time,
        entry_index=0,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=101.0,
        sl_level=99.0,
        quantity=1000.0,
        exit_time=entry_time + DAY if pnl is not None else None,
        exit_index=1 if pnl is not None else None,
        exit_price=101.0 if pnl is not None else None,
        exit_type="TP" if pnl is not None else None,
        pnl_abs=pnl,
    )


# ── windows ────────────────────────────────────────────────────────────────


def test_generate_windows_builds_expected_periods():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2021, 1, 1), train_days=180, test_days=90
    )

    assert len(windows) == 2
    assert windows[0].train_start == datetime(2020, 1, 1)
    assert windows[0].test_start == datetime(2020, 1, 1) + timedelta(days=180)
    assert windows[1].train_start == datetime(2020, 1, 1) + timedelta(days=90)
    assert all(w.test_end <= datetime(2021, 1, 1) for w in windows)


def test_generate_windows_keeps_test_periods_adjacent():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2021, 1, 1), train_days=90, test_days=30
    )

    assert len(windows) > 2
    for previous, current in zip(windows, windows[1:], strict=False):
        assert previous.test_end == current.test_start
        assert previous.train_end == previous.test_start


def test_generate_windows_expands_train_with_step():
    windows = generate_windows(
        datetime(2020, 1, 1),
        datetime(2021, 1, 1),
        train_days=90,
        test_days=30,
        step_days=30,
    )

    first, second = windows[0], windows[1]
    assert second.train_start - first.train_start == timedelta(days=30)
    assert second.test_start - first.test_start == timedelta(days=30)


def test_generate_windows_returns_empty_when_history_is_short():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 3, 1), train_days=90, test_days=30
    )

    assert windows == []


@pytest.mark.parametrize(
    ("train_days", "test_days", "step_days"),
    [(0, 30, None), (30, 0, None), (30, 30, 0), (30, 30, -5)],
)
def test_generate_windows_rejects_non_positive_lengths(train_days, test_days, step_days):
    with pytest.raises(ValueError):
        generate_windows(
            datetime(2020, 1, 1),
            datetime(2021, 1, 1),
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
        )


def test_generate_windows_rejects_reversed_range():
    with pytest.raises(ValueError):
        generate_windows(
            datetime(2021, 1, 1), datetime(2020, 1, 1), train_days=30, test_days=30
        )


def test_window_rejects_gap_between_train_and_test():
    with pytest.raises(ValueError):
        WalkForwardWindow(
            train_start=datetime(2020, 1, 1),
            train_end=datetime(2020, 2, 1),
            test_start=datetime(2020, 2, 2),
            test_end=datetime(2020, 3, 1),
        )


# ── parameter selection ────────────────────────────────────────────────────


def test_best_train_config_is_replayed_on_test():
    """Parameters must come from train and be reused unchanged on test."""
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 7, 1), train_days=90, test_days=30
    )
    calls: list[tuple[dict, datetime, datetime]] = []

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        calls.append((config, candles[0].open_time, candles[-1].open_time))
        # Profit grows with tp: the grid winner is deterministic.
        pnl = float(config["tp_percent"]) * 100.0
        return [make_trade(candles[len(candles) // 2].open_time, pnl)]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 200),
        windows,
        runner=runner,
        grid=[{"tp_percent": 1.0}, {"tp_percent": 3.0}, {"tp_percent": 2.0}],
    )

    assert result.windows
    assert all(w.best_config == {"tp_percent": 3.0} for w in result.windows)

    # Per window: one call per grid point on train, then one call on test.
    per_window = len(calls) // len(windows)
    assert per_window == 4
    for index, window in enumerate(windows):
        window_calls = calls[index * per_window:(index + 1) * per_window]
        test_config, test_start, test_end = window_calls[-1]
        assert test_config == {"tp_percent": 3.0}
        assert test_start == window.test_start
        assert test_end == window.test_end - DAY


def test_train_pnl_is_reported_per_window():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 5, 1), train_days=60, test_days=30
    )

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        return [make_trade(candles[0].open_time, float(config["tp_percent"]))]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 150),
        windows,
        runner=runner,
        grid=[{"tp_percent": 2.0}],
    )

    assert result.in_sample_pnl == pytest.approx(2.0 * len(result.windows))
    assert result.oos_pnl == pytest.approx(2.0 * len(result.windows))


# ── warmup ─────────────────────────────────────────────────────────────────


def test_warmup_candles_are_passed_but_their_trades_are_dropped():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 5, 1), train_days=60, test_days=30
    )
    window = windows[0]
    seen_lengths: list[int] = []

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        seen_lengths.append(len(candles))
        # First trade sits inside warmup, second one inside the test period.
        return [
            make_trade(window.test_start - 10 * DAY, 999.0),
            make_trade(window.test_start + 5 * DAY, 50.0),
        ]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 150),
        [window],
        runner=runner,
        grid=[{"tp_percent": 1.0}],
        warmup_bars=10,
    )

    # 30 test days + 10 warmup candles.
    assert seen_lengths[-1] == 40
    assert result.windows[0].test_pnl == 50.0
    assert [t.entry_time for t in result.oos_trades] == [window.test_start + 5 * DAY]


def test_warmup_is_capped_by_available_history():
    window = WalkForwardWindow(
        train_start=datetime(2020, 1, 1),
        train_end=datetime(2020, 1, 11),
        test_start=datetime(2020, 1, 11),
        test_end=datetime(2020, 1, 21),
    )
    seen: list[int] = []

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        seen.append(len(candles))
        return []

    run_walk_forward(
        make_candles(datetime(2020, 1, 1), 20),
        [window],
        runner=runner,
        grid=[{"tp_percent": 1.0}],
        warmup_bars=500,
        min_train_trades=0,
    )

    # Train is 10 candles, test 10; warmup cannot reach before the first candle.
    assert seen[-1] == 20


# ── skipped windows ────────────────────────────────────────────────────────


def test_window_is_skipped_when_train_has_too_few_trades():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 5, 1), train_days=60, test_days=30
    )
    test_calls = 0

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        nonlocal test_calls
        if candles[0].open_time >= windows[0].test_start:
            test_calls += 1
        return [make_trade(candles[0].open_time, 1.0)]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 150),
        windows,
        runner=runner,
        grid=[{"tp_percent": 1.0}],
        min_train_trades=5,
    )

    assert len(result.skipped_windows) == len(windows)
    assert result.evaluated_windows == []
    assert test_calls == 0
    assert result.oos_pnl == 0.0
    assert result.skipped_windows[0].skipped_reason is not None


# ── aggregates ─────────────────────────────────────────────────────────────


def test_result_aggregates_oos_trades_and_win_rate():
    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 7, 1), train_days=60, test_days=30
    )
    # Train windows of later folds overlap test periods of earlier ones, so the
    # phase is identified by exact boundaries, not by the first candle.
    phases = {(w.train_start, w.train_end): "train" for w in windows}
    phases.update({(w.test_start, w.test_end): "test" for w in windows})
    counter = {"n": 0}

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        phase = phases[(candles[0].open_time, candles[-1].open_time + DAY)]
        if phase == "test":
            counter["n"] += 1
            # Alternate a winner and a loser in every test period.
            pnl = 100.0 if counter["n"] % 2 else -50.0
            return [make_trade(candles[0].open_time, pnl)]
        return [make_trade(candles[0].open_time, 10.0)]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 200),
        windows,
        runner=runner,
        grid=[{"tp_percent": 1.0}],
    )

    assert len(result.oos_trades) == len(windows)
    assert result.oos_pnl == pytest.approx(sum(t.pnl_abs or 0.0 for t in result.oos_trades))
    assert result.oos_win_rate == pytest.approx(
        sum(1 for t in result.oos_trades if (t.pnl_abs or 0.0) > 0)
        / len(result.oos_trades)
        * 100
    )
    assert result.profitable_windows == sum(1 for w in result.windows if w.test_pnl > 0)
    assert result.in_sample_pnl == pytest.approx(10.0 * len(windows))


def test_degradation_is_none_without_positive_train_pnl():
    window = WalkForwardWindow(
        train_start=datetime(2020, 1, 1),
        train_end=datetime(2020, 2, 1),
        test_start=datetime(2020, 2, 1),
        test_end=datetime(2020, 3, 1),
    )

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        return [make_trade(candles[0].open_time, -10.0)]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 60),
        [window],
        runner=runner,
        grid=[{"tp_percent": 1.0}],
    )

    assert result.degradation is None
    assert result.to_dict()["degradation_percent"] is None


def test_to_dict_is_json_ready():
    import json

    windows = generate_windows(
        datetime(2020, 1, 1), datetime(2020, 5, 1), train_days=60, test_days=30
    )

    def runner(config: dict, candles: list[Candle]) -> list[Trade]:
        return [make_trade(candles[0].open_time, 5.0)]

    result = run_walk_forward(
        make_candles(datetime(2020, 1, 1), 150),
        windows,
        runner=runner,
        grid=[{"tp_percent": 1.5, "sl_percent": 1.0}],
    )

    payload = result.to_dict()
    json.dumps(payload)  # must not raise

    assert payload["evaluated_windows"] == len(windows)
    assert payload["windows"][0]["best_config"] == {"tp_percent": 1.5, "sl_percent": 1.0}
    assert payload["windows"][0]["train_start"] == "2020-01-01T00:00:00"


def test_empty_grid_is_rejected():
    window = WalkForwardWindow(
        train_start=datetime(2020, 1, 1),
        train_end=datetime(2020, 2, 1),
        test_start=datetime(2020, 2, 1),
        test_end=datetime(2020, 3, 1),
    )

    with pytest.raises(ValueError, match="grid"):
        run_walk_forward(
            make_candles(datetime(2020, 1, 1), 60),
            [window],
            runner=lambda config, candles: [],
            grid=[],
        )


def test_negative_warmup_is_rejected():
    with pytest.raises(ValueError, match="warmup_bars"):
        run_walk_forward([], [], runner=lambda config, candles: [], grid=[{}], warmup_bars=-1)
