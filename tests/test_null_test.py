"""Null test: random entries must lose the trading costs (T2-18)."""

import pytest

from config import commissions as commissions_config
from src.backtest.costs import BpsCosts
from src.backtest.null_test import (
    bootstrap_ci,
    classify,
    null_test,
    run_seed,
    synthetic_candles,
)
from src.strategies._null_entry.strategy import NullEntryStrategy
from src.strategies.strategy_registry import get_available_strategies

TP = 2.0
SL = 1.0
ENTRY_RATE = 0.05


def test_synthetic_candles_are_deterministic():
    first = synthetic_candles(bars=200, seed=7)
    second = synthetic_candles(bars=200, seed=7)

    assert [(c.open, c.high, c.low, c.close) for c in first] == [
        (c.open, c.high, c.low, c.close) for c in second
    ]


def test_synthetic_walk_has_no_drift():
    candles = synthetic_candles(bars=5_000, sigma=0.01, seed=1)
    moves = [c.close / c.open - 1.0 for c in candles]

    # Per-bar mean is ~0: the whole point is that price adds no edge.
    assert abs(sum(moves) / len(moves)) < 0.002


def test_high_and_low_bracket_open_and_close():
    for candle in synthetic_candles(bars=100, seed=3):
        assert candle.high >= max(candle.open, candle.close)
        assert candle.low <= min(candle.open, candle.close)


def test_null_strategy_is_not_registered():
    """A test instrument must never appear in the strategy registry."""
    assert "null_entry" not in get_available_strategies()


def test_null_entry_signals_are_reproducible():
    candles = synthetic_candles(bars=300, seed=5)
    config = {"seed": 11, "entry_rate": 0.2, "tp_percent": TP, "sl_percent": SL}
    strategy = NullEntryStrategy(timeframe="1h", config=config)

    first = strategy.detect_signals(candles)
    second = strategy.detect_signals(candles)

    assert [(s.candle_index, s.signal_type) for s in first] == [
        (s.candle_index, s.signal_type) for s in second
    ]
    assert first


def test_entry_rate_bounds_are_validated():
    with pytest.raises(ValueError, match="entry_rate"):
        NullEntryStrategy(
            timeframe="1h",
            config={"entry_rate": 0.0, "tp_percent": TP, "sl_percent": SL},
        )


def test_bootstrap_ci_is_reproducible_and_ordered():
    values = [1.0, -2.0, 3.0, -4.0, 5.0, -6.0]

    first = bootstrap_ci(values, iterations=500, seed=3)
    second = bootstrap_ci(values, iterations=500, seed=3)

    assert first == second
    assert first[0] <= first[1]


def test_classify_three_branches():
    assert classify(-10.0, -1.0) == "ok"
    assert classify(-1.0, 1.0) == "inconclusive"
    assert classify(1.0, 10.0) == "optimism"


def test_pnl_layers_add_up_to_net():
    """net = price + market costs - commission, with no unexplained remainder."""
    candles = synthetic_candles(bars=600, seed=2)
    trades = [
        t
        for t in run_seed(
            candles,
            seed=1,
            tp_percent=TP,
            sl_percent=SL,
            costs=BpsCosts(spread_bps=4.0, slippage_bps=5.0),
            entry_rate=ENTRY_RATE,
        )
        if not t.is_open()
    ]

    assert trades
    for trade in trades:
        expected = (trade.pnl_price or 0.0) + (trade.market_cost or 0.0)
        expected -= trade.commission_total
        assert trade.pnl_net == pytest.approx(expected, abs=1e-6)


def test_zero_costs_leave_no_edge(monkeypatch):
    """Control run: with no costs and no commission the interval contains zero."""
    monkeypatch.setattr(commissions_config, "COMMISSION_MODE", "NONE")
    candles = synthetic_candles(bars=800, seed=4)

    result = null_test(
        candles,
        seeds=6,
        tp_percent=TP,
        sl_percent=SL,
        costs=BpsCosts(),
        entry_rate=ENTRY_RATE,
        iterations=500,
    )

    assert result.trades > 50
    assert result.ci_low <= 0.0 <= result.ci_high
    assert result.verdict == "inconclusive"
    assert result.costs_per_trade == pytest.approx(0.0)


def test_costs_push_the_interval_below_zero(monkeypatch):
    """With real costs random entries must lose: the interval stays negative."""
    monkeypatch.setattr(commissions_config, "COMMISSION_MODE", "NONE")
    candles = synthetic_candles(bars=800, seed=4)

    result = null_test(
        candles,
        seeds=6,
        tp_percent=TP,
        sl_percent=SL,
        costs=BpsCosts(spread_bps=20.0, slippage_bps=20.0),
        entry_rate=ENTRY_RATE,
        iterations=500,
    )

    assert result.trades > 50
    assert result.ci_high < 0.0
    assert result.verdict == "ok"
    assert result.costs_per_trade < 0.0
    assert result.market_costs_per_trade < 0.0


def test_report_contains_the_expected_fields():
    candles = synthetic_candles(bars=400, seed=6)
    payload = null_test(
        candles, seeds=2, tp_percent=TP, sl_percent=SL, entry_rate=ENTRY_RATE
    ).to_dict()

    assert set(payload) >= {
        "seeds",
        "trades",
        "expectancy_price",
        "expectancy_net",
        "market_costs_per_trade",
        "commission_per_trade",
        "costs_per_trade",
        "market_costs_bps",
        "commission_bps",
        "costs_bps",
        "ci95",
        "price_ci95",
        "verdict",
    }


def test_costs_are_reported_in_bps_of_the_position(monkeypatch):
    """Rubles depend on sizing, bps do not: both must be in the report.

    One market side pays ½ spread (4/2 bps) + slippage (5 bps) = 7 bps. A take
    profit is a limit order and pays nothing on exit, so the market layer is
    -7 bps for TP exits and -14 bps for market exits (ADR-016).
    """
    monkeypatch.setattr(commissions_config, "COMMISSION_MODE", "NONE")
    candles = synthetic_candles(bars=600, seed=8)
    costs = BpsCosts(spread_bps=4.0, slippage_bps=5.0)

    trades = [
        t
        for t in run_seed(
            candles, seed=1, tp_percent=TP, sl_percent=SL, costs=costs, entry_rate=ENTRY_RATE
        )
        if not t.is_open()
    ]
    by_exit: dict[str, list[float]] = {}
    for trade in trades:
        trade_bps = (trade.market_cost or 0.0) / trade.quantity * 10_000.0
        by_exit.setdefault(trade.exit_type or "TIME", []).append(trade_bps)

    assert by_exit["TP"], "the sample must contain take-profit exits"
    assert by_exit["SL"], "the sample must contain stop-loss exits"
    # The offset is computed from each side's price, so bps of the position size
    # vary a little around the nominal 7/14.
    assert sum(by_exit["TP"]) / len(by_exit["TP"]) == pytest.approx(-7.0, abs=0.5)
    assert sum(by_exit["SL"]) / len(by_exit["SL"]) == pytest.approx(-14.0, abs=0.5)

    result = null_test(
        candles,
        seeds=3,
        tp_percent=TP,
        sl_percent=SL,
        costs=costs,
        entry_rate=ENTRY_RATE,
        iterations=300,
    )

    # The aggregate sits between the two exit types, weighted by their mix.
    assert -14.0 <= result.market_costs_bps <= -7.0
    assert result.costs_bps == pytest.approx(result.market_costs_bps, abs=1e-9)
    assert result.costs_per_trade < 0.0


def test_no_trades_returns_inconclusive():
    result = null_test(
        synthetic_candles(bars=10, seed=0),
        seeds=1,
        entry_rate=0.0001,
        iterations=100,
    )

    assert result.trades == 0
    assert result.verdict == "inconclusive"
