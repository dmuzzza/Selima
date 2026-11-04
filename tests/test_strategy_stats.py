"""StrategyStats: two PnL percentages are computed from two different bases.

`total_pnl_percent` answers "how much did the account earn" (base:
`initial_balance`), `total_pnl_percent_engaged` answers "how much did the
capital that actually worked earn" (base: `initial_balance * size / 100`).
At `position_size_percent = 100` they are identical, so the difference is only
visible below full size.
"""

from datetime import datetime, timedelta

import pytest

from src.strategies.base_strategy import Trade
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy


def make_trade(trade_id: int, pnl_abs: float, pnl_percent: float) -> Trade:
    """Closed long trade with a fixed result."""
    entry = datetime(2025, 1, 1) + timedelta(days=trade_id * 3)
    return Trade(
        trade_id=trade_id,
        entry_time=entry,
        entry_index=trade_id * 10,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=102.0,
        sl_level=98.0,
        quantity=100_000.0,
        exit_time=entry + timedelta(days=2),
        exit_index=trade_id * 10 + 5,
        exit_price=100.0 + pnl_percent,
        exit_type="TP",
        exit_reason="TP",
        pnl_percent=pnl_percent,
        pnl_abs=pnl_abs,
        pnl_net=pnl_abs,
    )


INITIAL_BALANCE = 100_000.0


def make_strategy(position_size_percent: float) -> DualMACrossoverStrategy:
    # Balance is explicit: the strategy merges its own DEFAULT_CONFIG, where
    # initial_balance is 50 000, so a test relying on defaults would drift.
    return DualMACrossoverStrategy(
        timeframe="4h",
        config={
            "ticker": "SBER",
            "initial_balance": INITIAL_BALANCE,
            "position_size_percent": position_size_percent,
        },
    )


def test_full_size_makes_both_percentages_equal():
    strategy = make_strategy(100.0)
    strategy.trades = [make_trade(1, 5_000.0, 5.0), make_trade(2, -1_000.0, -1.0)]

    stats = strategy.calculate_stats()

    # Hand-computed: 4 000 / 100 000 = 4%.
    assert stats.total_pnl == pytest.approx(4_000.0)
    assert stats.total_pnl_percent == pytest.approx(4.0)
    assert stats.total_pnl_percent_engaged == pytest.approx(4.0)


def test_half_size_doubles_the_engaged_percentage():
    strategy = make_strategy(50.0)
    strategy.trades = [make_trade(1, 5_000.0, 5.0), make_trade(2, -1_000.0, -1.0)]

    stats = strategy.calculate_stats()

    # 4 000 / 100 000 = 4% of the account; 4 000 / 50 000 = 8% of engaged capital.
    assert stats.total_pnl_percent == pytest.approx(4.0)
    assert stats.total_pnl_percent_engaged == pytest.approx(8.0)


def test_engaged_percentage_uses_the_configured_share():
    strategy = make_strategy(25.0)
    strategy.trades = [make_trade(1, 3_000.0, 3.0)]

    stats = strategy.calculate_stats()

    # 3 000 / 100 000 = 3%; 3 000 / 25 000 = 12%.
    assert stats.total_pnl_percent == pytest.approx(3.0)
    assert stats.total_pnl_percent_engaged == pytest.approx(12.0)


def test_zero_position_size_does_not_divide_by_zero():
    strategy = make_strategy(0.0)
    strategy.trades = [make_trade(1, 1_000.0, 1.0)]

    stats = strategy.calculate_stats()

    assert stats.total_pnl_percent_engaged == 0.0


def test_config_share_reaches_the_strategy():
    """The share must come from the run config, not from a hard-coded 100."""
    assert make_strategy(50.0).position_size_percent == 50.0
    assert make_strategy(100.0).position_size_percent == 100.0
