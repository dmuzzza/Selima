"""Backtest and live must agree on allow_short.

When allow_short=False a strategy must not open SHORT positions in the backtest,
must not report a BEARISH last signal for live trading, and must not send
BEARISH signals to the orchestrator.
"""

from datetime import datetime, timedelta

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal
from src.strategies.strategy_registry import get_strategy_class


class _BearishOnlyStrategy(BaseStrategy):
    """Emits a BEARISH signal on every candle: simplest allow_short probe."""

    @property
    def name(self) -> str:
        return "Bearish Only"

    @property
    def description(self) -> str:
        return "Test strategy: BEARISH signal on every candle"

    def detect_signals(self, candles: list[Candle]) -> list[Signal]:
        signals = [
            Signal(
                timestamp=candle.open_time,
                candle_index=index,
                signal_type="BEARISH",
                price=candle.close,
                metadata={},
            )
            for index, candle in enumerate(candles)
        ]
        self.signals = signals
        return signals


def make_candles(count: int = 6) -> list[Candle]:
    start = datetime(2026, 1, 1, 12, 0, 0)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100,
            is_complete=True,
        )
        for i in range(count)
    ]


def make_strategy(allow_short: bool) -> _BearishOnlyStrategy:
    return _BearishOnlyStrategy(
        timeframe="1h",
        config={"allow_short": allow_short, "tp_percent": 50.0, "sl_percent": 50.0},
    )


class FakeOrchestrator:
    def __init__(self):
        self.received = None

    def process_signals(self, **kwargs):
        self.received = kwargs["signals"]
        return []


def test_backtest_skips_short_when_not_allowed():
    strategy = make_strategy(allow_short=False)
    trades = strategy.run(make_candles())["trades"]

    assert trades == []


def test_backtest_opens_short_when_allowed():
    strategy = make_strategy(allow_short=True)
    trades = strategy.run(make_candles())["trades"]

    assert len(trades) == 1
    assert trades[0].is_short()


def test_last_signal_ignores_bearish_when_short_not_allowed():
    strategy = make_strategy(allow_short=False)

    assert strategy.check_last_signal(make_candles()) is None


def test_last_signal_returns_bearish_when_short_allowed():
    strategy = make_strategy(allow_short=True)
    signal = strategy.check_last_signal(make_candles())

    assert signal is not None
    assert signal.signal_type == "BEARISH"


def test_live_execution_drops_bearish_when_short_not_allowed():
    strategy = make_strategy(allow_short=False)
    strategy.signals = strategy.detect_signals(make_candles())
    orchestrator = FakeOrchestrator()

    strategy.execute_live_trades(orchestrator, ticker="TEST", figi="TESTFIGI")

    # No executable signals -> orchestrator is never called
    assert orchestrator.received is None


def test_live_execution_passes_bearish_when_short_allowed():
    strategy = make_strategy(allow_short=True)
    strategy.signals = strategy.detect_signals(make_candles())
    orchestrator = FakeOrchestrator()

    strategy.execute_live_trades(orchestrator, ticker="TEST", figi="TESTFIGI")

    assert orchestrator.received is not None
    assert len(orchestrator.received) == len(make_candles())


def test_sma_crossover_does_not_trade_shorts():
    """sma_crossover ships with allow_short=False in its default config."""
    strategy = get_strategy_class("sma_crossover")(timeframe="4h")

    assert strategy.allow_short is False
