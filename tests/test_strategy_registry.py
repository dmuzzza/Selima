"""Strategy registry: auto-discovery, duplicate protection, template contract."""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy
from src.strategies.strategy_registry import (
    STRATEGY_REGISTRY,
    get_available_strategies,
    get_strategy_class,
    register_strategy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_candles(prices: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100,
        )
        for i, price in enumerate(prices)
    ]


def test_discovery_finds_shipped_strategies():
    available = get_available_strategies()

    assert "sma_crossover" in available
    assert "dual_ma_crossover_after_testing" in available
    assert available["sma_crossover"] == "SMA Crossover"


def test_available_strategies_read_class_attributes():
    """No instantiation: names come from the class, so bad __init__ cannot break the UI."""
    for code, name in get_available_strategies().items():
        assert name == STRATEGY_REGISTRY[code].name


def test_unknown_strategy_raises():
    with pytest.raises(ValueError, match="Unknown strategy"):
        get_strategy_class("no_such_strategy")


def test_duplicate_code_rejected():
    class FirstProbe(BaseStrategy):
        code = "duplicate_probe"

        def detect_signals(self, candles):
            return []

    class SecondProbe(BaseStrategy):
        code = "duplicate_probe"

        def detect_signals(self, candles):
            return []

    register_strategy(FirstProbe)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_strategy(SecondProbe)
    finally:
        STRATEGY_REGISTRY.pop("duplicate_probe", None)


def test_register_is_idempotent_for_same_class():
    class Probe(BaseStrategy):
        code = "idempotent_probe"

        def detect_signals(self, candles):
            return []

    try:
        assert register_strategy(Probe) is Probe
        assert register_strategy(Probe) is Probe
    finally:
        STRATEGY_REGISTRY.pop("idempotent_probe", None)


def test_empty_code_rejected():
    class NoCode(BaseStrategy):
        def detect_signals(self, candles):
            return []

    with pytest.raises(ValueError, match="non-empty 'code'"):
        register_strategy(NoCode)


def test_template_is_not_discovered():
    """`_template` must stay out of the registry until it is copied and renamed."""
    script = (
        "from src.strategies.strategy_registry import get_available_strategies;"
        "print(','.join(sorted(get_available_strategies())))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    codes = result.stdout.strip().split(",")

    assert "sma_crossover" in codes
    assert "template" not in codes


def test_template_strategy_satisfies_contract():
    from src.strategies._template.strategy import TemplateStrategy

    strategy = TemplateStrategy(timeframe="1h", config={"sma_period": 3})
    candles = make_candles([100.0, 100.0, 100.0, 100.0, 110.0, 120.0, 130.0])

    signals = strategy.detect_signals(candles)

    assert signals, "rising market must cross the SMA upwards"
    assert signals[0].signal_type == "BULLISH"
    assert signals[0].candle_index == 4
    assert strategy.check_last_signal(candles) is None  # signal is not on the last candle

    result = strategy.run(candles)
    assert result["stats"].total_trades >= 0


def test_template_strategy_handles_insufficient_data():
    from src.strategies._template.strategy import TemplateStrategy

    strategy = TemplateStrategy(timeframe="1h", config={"sma_period": 50})

    assert strategy.detect_signals(make_candles([100.0] * 10)) == []
    assert strategy.signals == []
