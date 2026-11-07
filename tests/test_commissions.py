"""Broker margin tariff: borrowed amount, calendar days, inclusive upper bounds (ADR-018)."""

from datetime import datetime, timedelta, timezone

import pytest

from config import commissions
from config.commissions import (
    MARGIN_FEE_BRACKETS,
    MarginFeeBracket,
    calculate_daily_margin_fee,
    calculate_margin_fee,
    calculate_trade_commissions,
    check_bracket_table,
    margin_nights,
)

MSK = timezone(timedelta(hours=3))


# ═══════════════════════════════════════════════════════════════════════════
# ПЕРЕНОС: КАЛЕНДАРНЫЕ ДНИ, ВКЛЮЧАЯ ВЫХОДНЫЕ И ПРАЗДНИКИ
# ═══════════════════════════════════════════════════════════════════════════


def test_intraday_trade_pays_no_margin_fee():
    """Closed before the end of the trading day: no transfer, no fee."""
    entry = datetime(2026, 1, 5, 10, 0, tzinfo=MSK)
    exit_ = datetime(2026, 1, 5, 22, 0, tzinfo=MSK)

    assert margin_nights(entry, exit_) == 0
    assert calculate_margin_fee(60_000, 0, margin_nights(entry, exit_), is_short=True) == 0.0


def test_monday_to_tuesday_is_one_day():
    entry = datetime(2026, 1, 5, 22, 0, tzinfo=MSK)
    exit_ = datetime(2026, 1, 6, 10, 0, tzinfo=MSK)

    assert margin_nights(entry, exit_) == 1
    assert calculate_margin_fee(60_000, 0, 1, is_short=True) == 75.0


def test_friday_to_monday_is_three_days():
    """Weekend days are charged as calendar days (2026-01-09 is a Friday)."""
    entry = datetime(2026, 1, 9, 22, 0, tzinfo=MSK)
    exit_ = datetime(2026, 1, 12, 10, 0, tzinfo=MSK)

    assert margin_nights(entry, exit_) == 3
    assert calculate_margin_fee(60_000, 0, 3, is_short=True) == 225.0


def test_holidays_are_charged_as_calendar_days():
    """No holiday calendar: a New Year carry is the same number of dates."""
    entry = datetime(2026, 12, 31, 12, 0, tzinfo=MSK)
    exit_ = datetime(2027, 1, 3, 12, 0, tzinfo=MSK)

    assert margin_nights(entry, exit_) == 3


# ═══════════════════════════════════════════════════════════════════════════
# БАЗА: ЗАНЯТАЯ СУММА, А НЕ РАЗМЕР ПОЗИЦИИ
# ═══════════════════════════════════════════════════════════════════════════


def test_position_covered_by_own_funds_pays_nothing():
    """Uncovered <= 0 is a valid state, not an error: 100% sizing is covered."""
    assert calculate_margin_fee(position_amount=50_000, own_funds=50_000, nights=5) == 0.0
    assert calculate_margin_fee(position_amount=49_000, own_funds=50_000, nights=5) == 0.0
    assert calculate_margin_fee(position_amount=0, own_funds=0, nights=5) == 0.0


def test_bracket_is_chosen_by_the_borrowed_amount_not_the_position():
    # Position 300 000, own funds 290 000 -> 10 000 borrowed -> 40 RUB per day.
    assert calculate_margin_fee(position_amount=300_000, own_funds=290_000, nights=1) == 40.0
    # Position 300 000, own funds 0 -> 300 000 borrowed -> 350 RUB per day.
    assert calculate_margin_fee(position_amount=300_000, own_funds=0, nights=1) == 350.0


def test_short_is_charged_in_full_as_a_securities_loan():
    """The broker lends the securities, so cash on the account does not cover it."""
    assert calculate_margin_fee(50_000, 50_000, 3, is_short=True) == 120.0
    assert calculate_margin_fee(50_000, 50_000, 3, is_short=False) == 0.0


def test_legacy_override_replays_the_old_covered_short_model():
    """Only snapshots written before the tariff revision need `short_uncovered=False`."""
    assert calculate_margin_fee(50_000, 50_000, 2, is_short=True, short_uncovered=False) == 0.0
    assert calculate_margin_fee(50_000, 50_000, 2, is_short=True, short_uncovered=True) == 80.0


def test_position_size_is_fixed_for_the_whole_trade():
    """No averaging in the engine: every night uses the same bracket (T3-5d)."""
    one_night = calculate_margin_fee(60_000, 0, 1, is_short=True)
    assert calculate_margin_fee(60_000, 0, 4, is_short=True) == pytest.approx(4 * one_night)


# ═══════════════════════════════════════════════════════════════════════════
# ТАРИФНАЯ ТАБЛИЦА: ГРАНИЦЫ, ДИАПАЗОНЫ, ПРОЦЕНТЫ
# ═══════════════════════════════════════════════════════════════════════════


def test_lowest_bracket_is_paid_at_the_flat_rate():
    """Up to 5 000 RUB is free on three accounts; the model charges 5 RUB."""
    assert commissions.MARGIN_FEE_UP_TO_5K == 5.0
    assert calculate_daily_margin_fee(1.0) == 5.0
    assert calculate_daily_margin_fee(5_000) == 5.0
    assert calculate_daily_margin_fee(5_001) == 40.0


def test_upper_bound_belongs_to_the_lower_bracket():
    """`до` includes its upper bound: 50 000 -> 40 RUB, 50 001 -> 75 RUB."""
    assert calculate_daily_margin_fee(50_000) == 40.0
    assert calculate_daily_margin_fee(50_001) == 75.0
    assert calculate_daily_margin_fee(100_000) == 75.0
    assert calculate_daily_margin_fee(100_001) == 180.0


def test_fixed_brackets_follow_the_broker_table():
    assert calculate_daily_margin_fee(250_000) == 180.0
    assert calculate_daily_margin_fee(500_000) == 350.0
    assert calculate_daily_margin_fee(1_000_000) == 700.0
    assert calculate_daily_margin_fee(2_500_000) == 1_750.0
    assert calculate_daily_margin_fee(5_000_000) == 3_500.0
    assert calculate_daily_margin_fee(5_000_001) == 6_900.0
    assert calculate_daily_margin_fee(10_000_000) == 6_900.0


def test_percent_brackets_are_daily_not_annual():
    assert calculate_daily_margin_fee(10_000_001) == pytest.approx(6_800.00068)
    assert calculate_daily_margin_fee(25_000_000) == pytest.approx(17_000.0)
    assert calculate_daily_margin_fee(25_000_001) == pytest.approx(16_250.00065)
    assert calculate_daily_margin_fee(50_000_000) == pytest.approx(32_500.0)
    assert calculate_daily_margin_fee(50_000_001) == pytest.approx(28_500.00057)


def test_amounts_above_the_table_are_not_clipped():
    """The last bracket is a percentage up to infinity."""
    assert calculate_daily_margin_fee(100_000_000) == pytest.approx(57_000.0)
    assert calculate_daily_margin_fee(1_000_000_000) == pytest.approx(570_000.0)


def test_fee_may_drop_on_a_percent_boundary():
    """Fixed -> percent junctions are not monotonic; the test pins that fact."""
    assert calculate_daily_margin_fee(10_000_000) == 6_900.0
    assert calculate_daily_margin_fee(10_000_001) < 6_900.0
    assert calculate_daily_margin_fee(50_000_000) == pytest.approx(32_500.0)
    assert calculate_daily_margin_fee(50_000_001) < 32_500.0


def test_table_covers_every_amount_without_gaps_or_overlaps():
    check_bracket_table(MARGIN_FEE_BRACKETS)
    assert MARGIN_FEE_BRACKETS[0].min_amount == 0.0
    assert MARGIN_FEE_BRACKETS[-1].max_amount == float("inf")
    for lower, upper in zip(MARGIN_FEE_BRACKETS, MARGIN_FEE_BRACKETS[1:], strict=False):
        assert lower.max_amount == upper.min_amount


def test_table_validation_rejects_a_gap():
    broken = [
        MarginFeeBracket(min_amount=0, max_amount=5_000, daily_fee=5),
        MarginFeeBracket(min_amount=6_000, max_amount=float("inf"), daily_fee=40),
    ]
    with pytest.raises(ValueError, match="gap or overlap"):
        check_bracket_table(broken)


def test_table_validation_rejects_an_open_end():
    broken = [MarginFeeBracket(min_amount=0, max_amount=50_000, daily_fee=40)]
    with pytest.raises(ValueError, match="silently clipped"):
        check_bracket_table(broken)


def test_table_validation_rejects_mixed_rate_types():
    broken = [
        MarginFeeBracket(min_amount=0, max_amount=float("inf"), daily_fee=40, daily_percent=0.1)
    ]
    with pytest.raises(ValueError, match="either rubles or percent"):
        check_bracket_table(broken)


# ═══════════════════════════════════════════════════════════════════════════
# ОШИБКИ ВХОДА И «НЕТ ЗАЁМА»
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), "50000", None])
def test_invalid_borrowed_amount_raises(bad):
    with pytest.raises(ValueError):
        calculate_daily_margin_fee(bad)


def test_negative_position_raises():
    with pytest.raises(ValueError, match="position_amount"):
        calculate_margin_fee(-1.0, 0.0, 1)


def test_covered_position_is_not_an_error():
    """The engine must not fail on fully covered trades: they simply pay 0."""
    assert calculate_margin_fee(100_000, 100_000, 10) == 0.0
    assert calculate_margin_fee(100_000, 150_000, 10) == 0.0


def test_floating_point_remainder_is_not_a_loan():
    """`balance * 100 / 100` may leave 1e-14; that must not trigger the 5 RUB rate."""
    position = 47_572.32757131345
    own = 47_572.32757131344
    assert position > own
    assert calculate_margin_fee(position, own, 5) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# ОБЩИЙ РАСЧЁТ СДЕЛКИ (бэктест и аудит издержек)
# ═══════════════════════════════════════════════════════════════════════════


def test_intraday_short_pays_transaction_fees_only():
    fees = calculate_trade_commissions(
        quantity=100_000,
        entry_price=100.0,
        exit_price=95.0,
        entry_time=datetime(2026, 1, 5, 10, 0, tzinfo=MSK),
        exit_time=datetime(2026, 1, 5, 18, 0, tzinfo=MSK),
        is_long=False,
        mode="ALL",
    )

    assert fees.open == pytest.approx(50.0)
    assert fees.close == pytest.approx(47.5)
    assert fees.margin == 0.0
    assert fees.total == pytest.approx(97.5)


def test_overnight_short_pays_the_margin_bracket_per_day():
    fees = calculate_trade_commissions(
        quantity=100_000,
        entry_price=100.0,
        exit_price=95.0,
        entry_time=datetime(2026, 1, 5, 10, 0, tzinfo=MSK),
        exit_time=datetime(2026, 1, 7, 10, 0, tzinfo=MSK),
        is_long=False,
        own_funds=100_000,
        mode="ALL",
    )

    # 100 000 borrowed -> 75 RUB per day (upper bound included), two days.
    assert fees.margin == 150.0


@pytest.mark.parametrize(
    ("mode", "is_long", "charged"),
    [
        ("NONE", True, False),
        ("NONE", False, False),
        ("LONG_ONLY", True, True),
        ("LONG_ONLY", False, False),
        ("SHORT_ONLY", True, False),
        ("SHORT_ONLY", False, True),
        ("ALL", True, True),
        ("ALL", False, True),
    ],
)
def test_commission_mode_selects_the_charged_direction(mode, is_long, charged):
    fees = calculate_trade_commissions(
        quantity=100_000,
        entry_price=100.0,
        exit_price=100.0,
        entry_time=datetime(2026, 1, 5, 10, 0, tzinfo=MSK),
        exit_time=datetime(2026, 1, 5, 12, 0, tzinfo=MSK),
        is_long=is_long,
        mode=mode,
    )
    assert (fees.total > 0) is charged


def test_unknown_commission_mode_raises():
    with pytest.raises(ValueError, match="commission mode"):
        calculate_trade_commissions(
            quantity=1.0,
            entry_price=1.0,
            exit_price=None,
            entry_time=datetime(2026, 1, 5, 10, 0, tzinfo=MSK),
            exit_time=None,
            is_long=True,
            mode="SOMETIMES",
        )


@pytest.mark.parametrize("mode", ["ALL", "LONG_ONLY", "SHORT_ONLY", "NONE"])
def test_commission_mode_constant_is_valid(mode, monkeypatch):
    monkeypatch.setenv("COMMISSION_MODE", mode)
    assert commissions._read_commission_mode() == mode


def test_report_describes_the_tariff_table():
    """The snapshot must say which margin model produced its PnL."""
    described = commissions.describe_commissions()

    assert described["mode"] in commissions.COMMISSION_MODES
    assert described["margin_table_version"] == commissions.MARGIN_TABLE_VERSION
    assert described["margin_short_base"] == "notional"
    assert "margin_short_uncovered" not in described
    assert "margin_min_uncovered" not in described
    brackets = described["margin_brackets"]
    assert isinstance(brackets, list)
    assert len(brackets) == len(MARGIN_FEE_BRACKETS)
    assert brackets[-1]["max_amount"] is None
