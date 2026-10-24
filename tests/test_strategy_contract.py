"""Contract tests for BaseStrategy default implementations."""

from datetime import datetime, timedelta

import pytest

from src.market_data.models import Candle
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy
from src.strategies.sma_crossover import SMACrossoverStrategy


def make_candles(prices: list[float]) -> list[Candle]:
    """Build synthetic hourly candles from a list of close prices."""
    start = datetime(2025, 1, 1)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=price,
            high=price * 1.001,
            low=price * 0.999,
            close=price,
            volume=1000,
        )
        for i, price in enumerate(prices)
    ]


@pytest.fixture
def dual_ma() -> DualMACrossoverStrategy:
    return DualMACrossoverStrategy(timeframe="4h")


@pytest.fixture
def sma() -> SMACrossoverStrategy:
    return SMACrossoverStrategy(timeframe="1h")


def test_base_provides_live_methods(dual_ma):
    """check_last_signal/execute_live_trades come from BaseStrategy, not subclasses."""
    assert callable(getattr(dual_ma, "check_last_signal", None))
    assert callable(getattr(dual_ma, "execute_live_trades", None))
    assert "check_last_signal" not in DualMACrossoverStrategy.__dict__
    assert "execute_live_trades" not in DualMACrossoverStrategy.__dict__


def test_subclasses_do_not_duplicate_live_methods(sma):
    """SMA strategy must use the shared implementation after T0-3."""
    assert "check_last_signal" not in SMACrossoverStrategy.__dict__
    assert "execute_live_trades" not in SMACrossoverStrategy.__dict__


def test_tp_sl_defaults_come_from_config(dual_ma):
    assert isinstance(dual_ma.tp_percent, float)
    assert isinstance(dual_ma.sl_percent, float)


def test_check_last_signal_handles_empty(dual_ma):
    assert dual_ma.check_last_signal([]) is None


def test_check_last_signal_handles_insufficient_data(dual_ma, sma):
    assert dual_ma.check_last_signal(make_candles([100.0] * 10)) is None
    assert sma.check_last_signal(make_candles([100.0] * 5)) is None


def test_check_last_signal_matches_detect_signals(dual_ma):
    """Live check must agree with the backtest detector on the last candle."""
    # Flat market, then a single up candle: MA Adaptive crosses SMA50 exactly there.
    candles = make_candles([100.0] * 257 + [110.0])

    signal = dual_ma.check_last_signal(candles)
    all_signals = dual_ma.detect_signals(candles)

    expected = bool(all_signals) and all_signals[-1].candle_index == len(candles) - 1
    assert expected is True, "synthetic data must produce a signal on the last candle"
    assert signal is not None
    assert signal.candle_index == len(candles) - 1
    assert signal.signal_type == all_signals[-1].signal_type
