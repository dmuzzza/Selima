"""Sharpe and Z-score: realization order, window, and minimum sample size.

A trade result belongs to the moment it is realized, so both metrics use
`(exit_time, entry_time)` order. Sharpe also needs an honest window: earliest
entry to latest exit, not the exit of the trade opened last. Below the minimum
number of trades the ratio is noise, so the field is None and a reason is set.
"""

import math
from datetime import datetime, timedelta

import pytest

from src.strategies.base_strategy import Trade
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy

START = datetime(2024, 1, 1)


def make_trade(
    trade_id: int,
    *,
    entry_day: int,
    exit_day: int,
    pnl_percent: float,
) -> Trade:
    """Closed long trade at fixed days from START with a fixed result."""
    entry = START + timedelta(days=entry_day)
    return Trade(
        trade_id=trade_id,
        entry_time=entry,
        entry_index=entry_day,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=102.0,
        sl_level=98.0,
        quantity=100_000.0,
        exit_time=START + timedelta(days=exit_day),
        exit_index=exit_day,
        exit_price=100.0 + pnl_percent,
        exit_type="TP" if pnl_percent > 0 else "SL",
        exit_reason="TP" if pnl_percent > 0 else "SL",
        pnl_percent=pnl_percent,
        pnl_abs=pnl_percent * 1_000.0,
        pnl_net=pnl_percent * 1_000.0,
    )


def make_strategy() -> DualMACrossoverStrategy:
    return DualMACrossoverStrategy(
        timeframe="4h",
        config={"ticker": "SBER", "initial_balance": 100_000.0},
    )


def stats_for(trades: list[Trade]):
    strategy = make_strategy()
    strategy.trades = trades
    return strategy.calculate_stats()


def sharpe_trades() -> list[Trade]:
    """10 trades: 5 wins of +3, 5 losses of -1, window exactly 365 days.

    Hand computation: mean = (15 - 5) / 10 = 1.0; sample stdev of five +3 and
    five -1 is sqrt(40 / 9) = 2.10819; mean / stdev = 0.474342; trades per year
    = 10 / 1.0 = 10; Sharpe = 0.474342 * sqrt(10) = 1.5 exactly.
    """
    trades = [
        make_trade(
            trade_id=index,
            entry_day=index * 30,
            exit_day=index * 30 + 1,
            pnl_percent=3.0 if index < 5 else -1.0,
        )
        for index in range(10)
    ]
    # Last trade exits on day 365, so the window is exactly one year.
    trades[-1] = make_trade(trade_id=9, entry_day=300, exit_day=365, pnl_percent=-1.0)
    return trades


def test_sharpe_matches_hand_computation():
    stats = stats_for(sharpe_trades())

    assert stats.sharpe_ratio == pytest.approx(1.5)
    assert stats.sharpe_ratio_reason is None


def test_sharpe_does_not_depend_on_list_order():
    """The same trades in a different list order must give the same Sharpe."""
    trades = sharpe_trades()

    forward = stats_for(trades)
    backward = stats_for(list(reversed(trades)))

    assert forward.sharpe_ratio == pytest.approx(1.5)
    assert backward.sharpe_ratio == pytest.approx(forward.sharpe_ratio)


def test_sharpe_window_spans_latest_exit_not_last_opened_trade():
    """A trade opened late but closing early must not define the window.

    The old rule took closed_trades[-1].exit_time: with a trade opened last and
    closed immediately, the window collapsed and the annual multiplier grew.
    """
    trades = [
        make_trade(trade_id=index, entry_day=index, exit_day=index + 1, pnl_percent=1.0)
        for index in range(9)
    ]
    # Opens after all others and closes on the same day.
    trades.append(make_trade(trade_id=9, entry_day=20, exit_day=21, pnl_percent=-1.0))
    # An earlier trade closes far later than everything else.
    trades.append(make_trade(trade_id=10, entry_day=0, exit_day=365, pnl_percent=2.0))

    stats = stats_for(trades)

    # Window: min entry day 0 -> max exit day 365 = 1 year, 11 trades.
    returns = [t.pnl_percent for t in trades if t.pnl_percent is not None]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    expected = (mean / math.sqrt(variance)) * math.sqrt(len(returns) / 1.0)

    assert stats.sharpe_ratio == pytest.approx(expected)


def test_sharpe_is_none_below_minimum_with_reason():
    trades = [
        make_trade(trade_id=index, entry_day=index, exit_day=index + 1, pnl_percent=1.0)
        for index in range(9)
    ]

    stats = stats_for(trades)

    assert stats.sharpe_ratio is None
    assert stats.sharpe_ratio_reason == "9 closed trades, minimum 10"


def test_sharpe_is_none_on_zero_dispersion():
    trades = [
        make_trade(trade_id=index, entry_day=index, exit_day=index + 1, pnl_percent=2.0)
        for index in range(10)
    ]

    stats = stats_for(trades)

    assert stats.sharpe_ratio is None
    assert stats.sharpe_ratio_reason == "zero dispersion of trade returns"


def test_z_score_uses_realization_order():
    """Signs alternate by entry but are grouped by exit: the runs test must see
    the grouped sequence (R = 2), not the alternating one (R = 30).

    Hand computation with N = 30, W = L = 15, R = 2, X = 2*15*15 = 450:
    numerator = 30 * (2 - 0.5) - 450 = -405
    denominator = sqrt(450 * 449 / 29) = 83.4700
    z = -405 / 83.4700 = -4.8521
    """
    trades: list[Trade] = []
    for index in range(30):
        is_win = index % 2 == 0
        # Entry order alternates win/loss; exit order groups all wins first.
        exit_day = 100 + index if is_win else 200 + index
        trades.append(
            make_trade(
                trade_id=index,
                entry_day=index,
                exit_day=exit_day,
                pnl_percent=1.0 if is_win else -1.0,
            )
        )

    stats = stats_for(trades)

    expected = -405 / math.sqrt(450 * 449 / 29)
    assert stats.z_score == pytest.approx(expected, rel=1e-12)
    # The alternating reading would be +5.21, a different sign.
    assert stats.z_score < 0


def test_z_score_is_none_below_minimum_with_reason():
    trades = [
        make_trade(trade_id=index, entry_day=index, exit_day=index + 1, pnl_percent=1.0)
        for index in range(29)
    ]
    trades[-1] = make_trade(trade_id=28, entry_day=28, exit_day=29, pnl_percent=-1.0)

    stats = stats_for(trades)

    assert stats.z_score is None
    assert stats.z_score_reason == "29 closed trades, minimum 30"


def test_z_score_is_none_when_all_trades_win():
    trades = [
        make_trade(trade_id=index, entry_day=index, exit_day=index + 1, pnl_percent=1.0)
        for index in range(30)
    ]

    stats = stats_for(trades)

    assert stats.z_score is None
    assert stats.z_score_reason == "all trades have the same sign"
