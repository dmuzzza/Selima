"""Margin fee model: uncovered part only, broker threshold, transfer per day (ADR-018)."""

from datetime import datetime, timedelta, timezone

import pytest

from config import commissions
from config.commissions import calculate_margin_fee, margin_nights

MSK = timezone(timedelta(hours=3))


def test_intraday_trade_pays_no_margin_fee():
    """Closed before the end of the trading day: no transfer, no fee."""
    entry = datetime(2026, 1, 5, 10, 0, tzinfo=MSK)
    exit_ = datetime(2026, 1, 5, 22, 0, tzinfo=MSK)

    assert margin_nights(entry, exit_) == 0
    assert calculate_margin_fee(50_000, 50_000, margin_nights(entry, exit_)) == 0.0


def test_overnight_trade_counts_one_night_per_date_change():
    entry = datetime(2026, 1, 5, 22, 0, tzinfo=MSK)

    assert margin_nights(entry, datetime(2026, 1, 6, 10, 0, tzinfo=MSK)) == 1
    assert margin_nights(entry, datetime(2026, 1, 8, 10, 0, tzinfo=MSK)) == 3


def test_position_covered_by_own_funds_pays_nothing():
    """The broker charges the uncovered part only: 100% sizing is fully covered."""
    assert calculate_margin_fee(position_amount=50_000, own_funds=50_000, nights=5) == 0.0
    assert calculate_margin_fee(position_amount=49_000, own_funds=50_000, nights=5) == 0.0


def test_uncovered_part_below_threshold_pays_nothing():
    """Less than 5 000 RUB borrowed: the broker does not charge."""
    assert calculate_margin_fee(position_amount=54_000, own_funds=50_000, nights=3) == 0.0


def test_uncovered_part_above_threshold_uses_the_bracket():
    # 60 000 uncovered falls into the 50 000-100 000 bracket: 75 RUB per day.
    assert calculate_margin_fee(position_amount=110_000, own_funds=50_000, nights=1) == 75.0
    assert calculate_margin_fee(position_amount=110_000, own_funds=50_000, nights=3) == 225.0


def test_bracket_is_chosen_by_the_uncovered_amount_not_the_position():
    # Position 300 000, own funds 290 000 -> 10 000 uncovered -> first bracket (40).
    assert calculate_margin_fee(position_amount=300_000, own_funds=290_000, nights=1) == 40.0


def test_short_is_uncovered_by_default():
    """Conservative default: borrowed securities are charged in full (ADR-018)."""
    assert commissions.MARGIN_SHORT_UNCOVERED is True
    # 50 000 borrowed securities -> first bracket (40 RUB) per night.
    assert calculate_margin_fee(50_000, 50_000, 3, is_short=True) == 120.0
    # A long of the same size stays covered by own funds.
    assert calculate_margin_fee(50_000, 50_000, 3, is_short=False) == 0.0


def test_short_can_be_treated_as_covered(monkeypatch):
    """Sensitivity mode: cash collateral is assumed to cover the short."""
    monkeypatch.setattr(commissions, "MARGIN_SHORT_UNCOVERED", False)
    assert calculate_margin_fee(50_000, 50_000, 2, is_short=True) == 0.0


def test_short_below_threshold_pays_nothing_even_when_uncovered():
    assert calculate_margin_fee(4_000, 0, 3, is_short=True) == 0.0


def test_explicit_override_wins_over_the_module_default():
    """The audit replays a snapshot with the model it was produced with."""
    # Module default is True; the snapshot may say otherwise.
    assert calculate_margin_fee(50_000, 50_000, 2, is_short=True, short_uncovered=False) == 0.0
    assert calculate_margin_fee(50_000, 50_000, 2, is_short=True, short_uncovered=True) == 80.0


@pytest.mark.parametrize("mode", ["ALL", "LONG_ONLY", "SHORT_ONLY", "NONE"])
def test_commission_mode_constant_is_valid(mode, monkeypatch):
    monkeypatch.setenv("COMMISSION_MODE", mode)
    assert commissions._read_commission_mode() == mode


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("", False)],
)
def test_margin_short_uncovered_is_read_from_env(raw, expected, monkeypatch):
    """Both modes are reachable without touching the code."""
    monkeypatch.setenv("MARGIN_SHORT_UNCOVERED", raw)
    assert commissions._read_margin_short_uncovered() is expected


def test_default_margin_short_uncovered_is_conservative(monkeypatch):
    monkeypatch.delenv("MARGIN_SHORT_UNCOVERED", raising=False)
    assert commissions._read_margin_short_uncovered() is True


def test_report_describes_the_margin_flag_next_to_costs():
    """The snapshot must say which margin model produced its PnL."""
    described = commissions.describe_commissions()

    assert described["mode"] in commissions.COMMISSION_MODES
    assert described["margin_short_uncovered"] is commissions.MARGIN_SHORT_UNCOVERED
    assert described["margin_min_uncovered"] == commissions.MARGIN_MIN_UNCOVERED
