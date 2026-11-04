"""scheduler: one signal lookup per instrument cycle.

`_process_instrument` used to call `check_last_signal()` before the TEST_MODE
branch and again in the production branch, so `detect_signals()` ran twice on
every tick. Only the second value was ever used.
"""

from types import SimpleNamespace

import pytest

from src.backtest.null_test import synthetic_candles


class FakeTradeRepo:
    def has_open_trade_on_timeframe_accounts(self, **kwargs) -> bool:
        return False


class FakeAccountSelector:
    def get_all_accounts(self) -> list[str]:
        return ["acc-1"]

    def get_account_id(self, direction: str) -> str:
        return "acc-1234"


def make_scheduler_module():
    """Import scheduler.py; skip when the heavy module-level setup is unavailable."""
    return pytest.importorskip("scheduler")


def make_fake_scheduler(candles) -> SimpleNamespace:
    return SimpleNamespace(
        history_manager=SimpleNamespace(get_candles=lambda figi, timeframe: candles),
        account_selector=FakeAccountSelector(),
        _trade_repositories=lambda: (FakeTradeRepo(), None),
    )


def test_process_instrument_calls_detect_signals_once(monkeypatch):
    module = make_scheduler_module()
    # 250 bars: above the strategy's 201-candle minimum, so the call is a real
    # lookup rather than an early "not enough candles" return.
    candles = synthetic_candles(bars=250, seed=1)
    calls: list[int] = []

    original = module.DualMACrossoverStrategy.detect_signals

    def spy(self, candles_arg):
        calls.append(1)
        return original(self, candles_arg)

    monkeypatch.setattr(module.DualMACrossoverStrategy, "detect_signals", spy)

    fake = make_fake_scheduler(candles)
    instrument = SimpleNamespace(ticker="SBER", figi="BBG004730N88")

    result = module.TradingScheduler._process_instrument(fake, instrument, "4h", "acc-1234")

    # Exactly one lookup per cycle, whatever the branch returns afterwards.
    assert len(calls) == 1
    assert set(result) == {"signals_count", "trades_count"}
