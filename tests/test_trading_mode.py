"""Tests for trading mode resolution (T4-5)."""

import pytest

from config.trading_mode import TradingMode, TradingModeError, resolve_trading_mode


def test_default_is_dry_run():
    assert resolve_trading_mode({}) is TradingMode.DRY_RUN


def test_empty_value_falls_back_to_dry_run():
    assert resolve_trading_mode({"TRADING_MODE": "  "}) is TradingMode.DRY_RUN


def test_dry_run_explicit():
    assert resolve_trading_mode({"TRADING_MODE": "DRY_RUN"}) is TradingMode.DRY_RUN


def test_live_requires_confirmation():
    with pytest.raises(TradingModeError, match="LIVE_CONFIRM"):
        resolve_trading_mode({"TRADING_MODE": "live"})


def test_live_with_wrong_confirmation_is_rejected():
    with pytest.raises(TradingModeError, match="LIVE_CONFIRM"):
        resolve_trading_mode({"TRADING_MODE": "live", "LIVE_CONFIRM": "no"})


def test_live_with_confirmation():
    mode = resolve_trading_mode({"TRADING_MODE": "live", "LIVE_CONFIRM": "yes"})
    assert mode is TradingMode.LIVE
    assert mode.is_live


def test_unknown_mode_is_rejected():
    with pytest.raises(TradingModeError, match="Unknown TRADING_MODE"):
        resolve_trading_mode({"TRADING_MODE": "turbo"})


def test_dry_run_is_not_live():
    assert not resolve_trading_mode({}).is_live
