"""Exit policy: config-driven TP/SL, no silent defaults, versioned fingerprint."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.backtest.costs import BpsCosts
from src.backtest.exit_policy import (
    DEFAULT_EXIT_POLICY_PATH,
    ExitPolicyConfig,
    MissingExitPolicyError,
    PercentExitPolicy,
    clear_exit_policy_cache,
    load_exit_policies,
    resolve_exit_policy,
)
from src.backtest.simulator import SizingRules, TradeSimulator
from src.market_data.models import Candle
from src.strategies.base_strategy import Signal

START = datetime(2026, 1, 1, 12, 0, 0)


def write_config(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_exit_policy_cache()
    return path


def test_percent_policy_levels_are_symmetric():
    policy = PercentExitPolicy(tp_percent=2.0, sl_percent=1.0)

    assert policy.levels(100.0, is_long=True) == (pytest.approx(102.0), pytest.approx(99.0))
    assert policy.levels(100.0, is_long=False) == (pytest.approx(98.0), pytest.approx(101.0))


def test_non_positive_percentages_are_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        PercentExitPolicy(tp_percent=0.0, sl_percent=1.0)


def test_shipped_config_resolves_dual_ma_per_ticker():
    policy = resolve_exit_policy(
        strategy_code="dual_ma_crossover_after_testing", timeframe="4h", ticker="SBER"
    )

    assert policy.tp_percent == pytest.approx(2.3)
    assert policy.sl_percent == pytest.approx(2.2)
    assert policy.source == "tp_sl.json:4h/SBER"
    assert policy.fingerprint is not None


def test_shipped_config_resolves_sma_per_timeframe():
    policy = resolve_exit_policy(strategy_code="sma_crossover", timeframe="1d", ticker=None)

    assert policy.tp_percent == pytest.approx(3.5)
    assert policy.sl_percent == pytest.approx(2.6)


def test_unknown_ticker_is_an_error_not_a_default():
    with pytest.raises(MissingExitPolicyError, match="no TP/SL for ticker 'NOPE'"):
        resolve_exit_policy(
            strategy_code="dual_ma_crossover_after_testing", timeframe="4h", ticker="NOPE"
        )


def test_unknown_timeframe_is_an_error():
    with pytest.raises(MissingExitPolicyError, match="timeframe '1h'"):
        resolve_exit_policy(
            strategy_code="dual_ma_crossover_after_testing", timeframe="1h", ticker="SBER"
        )


def test_unknown_strategy_is_an_error():
    with pytest.raises(MissingExitPolicyError, match="no TP/SL block for strategy"):
        resolve_exit_policy(strategy_code="no_such_strategy", timeframe="4h", ticker="SBER")


def test_per_ticker_table_requires_a_ticker():
    with pytest.raises(MissingExitPolicyError, match="needs a ticker"):
        resolve_exit_policy(
            strategy_code="dual_ma_crossover_after_testing", timeframe="4h", ticker=None
        )


def test_explicit_config_wins_over_the_file():
    policy = resolve_exit_policy(
        strategy_code="dual_ma_crossover_after_testing",
        timeframe="4h",
        ticker="SBER",
        config={"tp_percent": 9.0, "sl_percent": 4.0},
    )

    assert (policy.tp_percent, policy.sl_percent) == (9.0, 4.0)
    assert policy.source == "config"
    assert policy.fingerprint is None


def test_half_a_pair_is_rejected():
    with pytest.raises(ValueError, match="must be set together"):
        resolve_exit_policy(
            strategy_code="sma_crossover",
            timeframe="1h",
            config={"tp_percent": 2.0},
        )


def test_fingerprint_ignores_formatting_but_not_values(tmp_path):
    payload: dict[str, Any] = {
        "version": "7",
        "strategies": {"s": {"mode": "by_timeframe", "by_timeframe": {"1h": {"tp_percent": 1.0, "sl_percent": 0.5}}}},
    }
    first = load_exit_policies(write_config(tmp_path / "a.json", payload))
    reformatted = write_config(tmp_path / "b.json", payload)
    second = load_exit_policies(reformatted)

    payload["strategies"]["s"]["by_timeframe"]["1h"]["tp_percent"] = 1.1
    changed = load_exit_policies(write_config(tmp_path / "c.json", payload))

    assert first.sha256 == second.sha256
    assert first.sha256 != changed.sha256
    assert first.fingerprint.startswith("7+")


def test_missing_strategies_block_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"version": "1"}', encoding="utf-8")
    clear_exit_policy_cache()

    with pytest.raises(ValueError, match="non-empty 'strategies'"):
        load_exit_policies(path)


def test_unknown_mode_is_rejected(tmp_path):
    path = write_config(
        tmp_path / "mode.json",
        {"version": "1", "strategies": {"s": {"mode": "atr", "by_timeframe": {}}}},
    )
    config: ExitPolicyConfig = load_exit_policies(path)

    with pytest.raises(ValueError, match="unknown mode"):
        config.for_strategy("s", "1h", "SBER")


def test_describe_carries_the_fingerprint():
    description = load_exit_policies(DEFAULT_EXIT_POLICY_PATH).describe(
        "sma_crossover", "1h", "SBER"
    )

    assert description["tp_sl_version"] == load_exit_policies().fingerprint
    assert description["tp_percent"] == pytest.approx(1.2)


def test_policy_levels_stay_on_raw_open_with_costs():
    """The file-backed policy must not move levels to the cost-adjusted fill (ADR-017)."""
    candles = [
        Candle(open_time=START, open=100.0, high=100.0, low=100.0, close=100.0, volume=100),
        Candle(
            open_time=START + timedelta(hours=1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100,
        ),
    ]
    signal = Signal(
        timestamp=START, candle_index=0, signal_type="BULLISH", price=100.0, metadata={}
    )
    policy = resolve_exit_policy(
        strategy_code="dual_ma_crossover_after_testing", timeframe="4h", ticker="SBER"
    )
    simulator = TradeSimulator(
        exit_policy=policy,
        sizing=SizingRules(initial_balance=1000.0, position_size_percent=100.0),
        on_close=lambda trade, balance: None,
        costs=BpsCosts(spread_bps=2.0, slippage_bps=3.0),
    )
    trades = simulator.run(candles, [signal])

    assert trades[0].entry_price == pytest.approx(100.04)
    assert trades[0].tp_level == pytest.approx(100.0 * 1.023)
    assert trades[0].sl_level == pytest.approx(100.0 * 0.978)
