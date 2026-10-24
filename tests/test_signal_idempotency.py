"""Idempotency of live signals: one signal -> at most one trade."""

from datetime import datetime

from src.strategies.base_strategy import Signal
from src.trading.models import build_signal_key
from src.trading.orchestrator import TradingOrchestrator

CANDLE_OPEN = datetime(2026, 10, 24, 12, 0, 0)
FIGI = "BBG004730N88"


def test_signal_key_is_stable():
    first = build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=CANDLE_OPEN, direction="LONG"
    )
    second = build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=CANDLE_OPEN, direction="LONG"
    )
    assert first == second
    assert first == f"{FIGI}|4h|2026-10-24T12:00:00|LONG"


def test_signal_key_ignores_microseconds():
    with_micro = CANDLE_OPEN.replace(microsecond=123456)
    assert build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=with_micro, direction="LONG"
    ) == build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=CANDLE_OPEN, direction="LONG"
    )


def test_signal_key_differs_by_candle_timeframe_and_direction():
    base = build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=CANDLE_OPEN, direction="LONG"
    )
    assert base != build_signal_key(
        figi=FIGI, timeframe="1h", candle_open_time=CANDLE_OPEN, direction="LONG"
    )
    assert base != build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=CANDLE_OPEN, direction="SHORT"
    )
    assert base != build_signal_key(
        figi=FIGI, timeframe="4h", candle_open_time=datetime(2026, 10, 24, 16, 0), direction="LONG"
    )


class FakeTradeRepository:
    """In-memory stand-in for PgLiveTradeRepository (no database)."""

    def __init__(self):
        self.keys = set()
        self.attempts = 0
        self.created = []

    def get_open_trades_by_ticker(self, **kwargs):
        return []

    def create_pending_if_absent(self, trade) -> bool:
        self.attempts += 1
        if trade.signal_key in self.keys:
            return False
        self.keys.add(trade.signal_key)
        self.created.append(trade)
        return True

    def save(self, trade) -> bool:
        return True


class FakePositionManager:
    """Always reports that the position cannot be sized (no broker calls)."""

    def calculate_trade_parameters(self, **kwargs):
        return None


class FakeTradeExecutor:
    def execute_trade(self, params):  # pragma: no cover - never reached
        raise AssertionError("order must not be sent when signal is a duplicate")


def make_orchestrator(repository) -> TradingOrchestrator:
    orchestrator = TradingOrchestrator.__new__(TradingOrchestrator)
    orchestrator.token = "test-token"
    orchestrator.account_id = "2000123456"
    orchestrator.strategy_name = "Dual MA Crossover"
    orchestrator.timeframe = "4h"
    orchestrator.repository = repository
    orchestrator.position_manager = FakePositionManager()  # type: ignore[assignment]
    orchestrator.trade_executor = FakeTradeExecutor()  # type: ignore[assignment]
    return orchestrator


def make_signal() -> Signal:
    return Signal(
        timestamp=CANDLE_OPEN,
        candle_index=10,
        signal_type="BULLISH",
        price=100.0,
        metadata={},
    )


def test_duplicate_signal_creates_one_trade_record():
    repository = FakeTradeRepository()
    orchestrator = make_orchestrator(repository)

    first = orchestrator.process_signals([make_signal()], "SBER", FIGI, 1.5, 1.0)
    second = orchestrator.process_signals([make_signal()], "SBER", FIGI, 1.5, 1.0)

    assert repository.attempts == 2
    assert len(repository.created) == 1
    assert len(first) == 1
    assert second == []


def test_different_direction_is_a_different_signal():
    repository = FakeTradeRepository()
    orchestrator = make_orchestrator(repository)

    long_signal = make_signal()
    short_signal = Signal(
        timestamp=CANDLE_OPEN,
        candle_index=10,
        signal_type="BEARISH",
        price=100.0,
        metadata={},
    )

    orchestrator.process_signals([long_signal], "SBER", FIGI, 1.5, 1.0)
    orchestrator.process_signals([short_signal], "SBER", FIGI, 1.5, 1.0)

    assert len(repository.created) == 2
