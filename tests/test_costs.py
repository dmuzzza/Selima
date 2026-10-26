"""Cost model: spread halves, per-side slippage, limit TP, config loading."""

import json
from pathlib import Path

import pytest

from config.commissions import COMMISSION_MODE, TRANSACTION_COMMISSION_PERCENT
from src.backtest.costs import (
    ZERO_COSTS,
    BpsCosts,
    TickCosts,
    build_costs,
    clear_costs_cache,
    load_costs,
)

# ── bps model ──────────────────────────────────────────────────────────────


def test_long_market_entry_and_exit_pay_half_spread_plus_slippage():
    costs = BpsCosts(spread_bps=2.0, slippage_bps=3.0)

    # 100 * (2/2 + 3) / 10000 = 0.04
    assert costs.entry_price(100.0, is_long=True) == pytest.approx(100.04)
    assert costs.exit_price(100.0, is_long=True, exit_type="SL") == pytest.approx(99.96)


def test_short_market_entry_and_exit_pay_half_spread_plus_slippage():
    costs = BpsCosts(spread_bps=2.0, slippage_bps=3.0)

    assert costs.entry_price(100.0, is_long=False) == pytest.approx(99.96)
    assert costs.exit_price(100.0, is_long=False, exit_type="SL") == pytest.approx(100.04)


def test_take_profit_is_a_limit_order_and_pays_nothing():
    costs = BpsCosts(spread_bps=10.0, slippage_bps=20.0)

    assert costs.exit_price(100.0, is_long=True, exit_type="TP") == 100.0
    assert costs.exit_price(100.0, is_long=False, exit_type="TP") == 100.0


def test_gap_exit_pays_market_costs():
    costs = BpsCosts(spread_bps=2.0, slippage_bps=3.0)

    assert costs.exit_price(90.0, is_long=True, exit_type="GAP") == pytest.approx(89.964)


def test_round_trip_is_one_spread_plus_two_slippages():
    costs = BpsCosts(spread_bps=2.0, slippage_bps=5.0)

    # For SBER-like numbers the trading cost is 12 bps per round trip.
    assert costs.trading_round_trip_bps == pytest.approx(12.0)
    assert costs.describe()["trading_round_trip_bps"] == pytest.approx(12.0)


def test_commission_is_reported_separately_from_trading_costs():
    """COMMISSION_MODE decides whether commission is charged on every trade."""
    described = BpsCosts(spread_bps=2.0, slippage_bps=3.0).describe()
    commission_bps = TRANSACTION_COMMISSION_PERCENT * 100.0

    assert described["commission_mode"] == COMMISSION_MODE
    assert described["commission_bps_per_side"] == pytest.approx(commission_bps)
    expected_total = described["trading_round_trip_bps"]
    if COMMISSION_MODE == "ALL":
        expected_total += 2 * commission_bps
    assert described["total_round_trip_bps"] == pytest.approx(expected_total)


def test_zero_costs_are_gross_and_not_tradable():
    described = ZERO_COSTS.describe()

    assert ZERO_COSTS.is_tradable is False
    assert described["label"] == "GROSS"
    assert described["tradable"] is False
    assert described["trading_round_trip_bps"] == 0.0


def test_non_zero_costs_are_net_and_tradable():
    described = BpsCosts(spread_bps=2.0, slippage_bps=3.0).describe()

    assert described["label"] == "NET"
    assert described["tradable"] is True
    assert described["mode"] == "bps"


def test_negative_costs_are_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        build_costs({"mode": "bps", "spread_bps": -1.0, "slippage_bps": 1.0})


# ── ticks model ────────────────────────────────────────────────────────────


def test_tick_costs_use_tick_size_for_the_offset():
    costs = TickCosts(tick_size=0.01, spread_ticks=1.0, slippage_ticks=2.0)

    # 0.01 * (1/2 + 2) = 0.025
    assert costs.entry_price(65.0, is_long=True) == pytest.approx(65.025)
    assert costs.exit_price(65.0, is_long=False, exit_type="SL") == pytest.approx(65.025)
    assert costs.exit_price(65.0, is_long=True, exit_type="TP") == pytest.approx(65.0)


def test_tick_costs_require_positive_tick_size():
    with pytest.raises(ValueError, match="tick_size"):
        build_costs({"mode": "ticks", "tick_size": 0, "spread_ticks": 1.0})


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="unknown cost mode"):
        build_costs({"mode": "percent"})


# ── config ─────────────────────────────────────────────────────────────────


@pytest.fixture
def costs_file(tmp_path: Path) -> Path:
    payload = {
        "version": "9",
        "default": {"mode": "bps", "spread_bps": 4.0, "slippage_bps": 5.0},
        "instruments": {
            "SBER": {"spread_bps": 2.0, "slippage_bps": 3.0},
            "CRU6": {
                "mode": "ticks",
                "tick_size": 0.01,
                "spread_ticks": 1.0,
                "slippage_ticks": 2.0,
            },
            "PARTIAL": {"spread_bps": 20.0},
        },
    }
    path = tmp_path / "costs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_costs_applies_instrument_override(costs_file: Path):
    clear_costs_cache()
    config = load_costs(costs_file)

    assert config.for_ticker("SBER") == BpsCosts(spread_bps=2.0, slippage_bps=3.0)
    assert config.for_ticker("sber") == BpsCosts(spread_bps=2.0, slippage_bps=3.0)


def test_unknown_ticker_falls_back_to_default(costs_file: Path):
    clear_costs_cache()
    config = load_costs(costs_file)

    assert config.for_ticker("UNKNOWN") == BpsCosts(spread_bps=4.0, slippage_bps=5.0)
    assert config.for_ticker(None) == BpsCosts(spread_bps=4.0, slippage_bps=5.0)


def test_partial_override_keeps_default_slippage(costs_file: Path):
    clear_costs_cache()
    config = load_costs(costs_file)

    assert config.for_ticker("PARTIAL") == BpsCosts(spread_bps=20.0, slippage_bps=5.0)


def test_tick_instrument_is_built_from_config(costs_file: Path):
    clear_costs_cache()
    model = load_costs(costs_file).for_ticker("CRU6")

    assert isinstance(model, TickCosts)
    assert model.describe()["mode"] == "ticks"


def test_describe_carries_version_and_fingerprint(costs_file: Path):
    clear_costs_cache()
    described = load_costs(costs_file).describe("SBER")

    assert described["costs_version"].startswith("9+")
    assert len(described["costs_version"].split("+")[1]) == 12
    assert described["ticker"] == "SBER"


def test_fingerprint_is_stable_and_changes_with_parameters(costs_file: Path, tmp_path: Path):
    clear_costs_cache()
    first = load_costs(costs_file).sha256

    # Reformatting the same payload must not change the fingerprint.
    reformatted = tmp_path / "reformatted.json"
    payload = json.loads(costs_file.read_text(encoding="utf-8"))
    reformatted.write_text(json.dumps(payload, indent=4), encoding="utf-8")
    clear_costs_cache()
    assert load_costs(reformatted).sha256 == first

    payload["default"]["slippage_bps"] = 6.0
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    clear_costs_cache()
    assert load_costs(changed).sha256 != first


def test_config_without_default_is_rejected(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"version": "1"}), encoding="utf-8")
    clear_costs_cache()

    with pytest.raises(ValueError, match="default"):
        load_costs(path)


def test_repository_costs_config_is_valid():
    clear_costs_cache()
    config = load_costs()

    assert config.for_ticker("SBER").is_tradable is True
    assert config.for_ticker("CRU6").describe()["mode"] == "ticks"
    # The shipped default must not be zero, otherwise every report is GROSS.
    assert config.for_ticker("UNKNOWN").is_tradable is True
