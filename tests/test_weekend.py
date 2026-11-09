"""Weekend audit: bar classification, trade groups, breaks, schedule mismatches.

Pure functions only: synthetic candles and trades, no database and no network.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from src.backtest.portfolio import PooledTrade
from src.backtest.weekend import (
    CANDLES_WITHOUT_TRADING_DAY,
    TRADING_DAY_WITHOUT_CANDLES,
    BarCalendar,
    ScheduleDay,
    WeekendThresholds,
    audit_trades,
    bar_calendar,
    bar_stats,
    entry_is_weekend,
    exit_is_weekend,
    holding_spans_weekend,
    is_weekend_bar,
    load_weekend_thresholds,
    schedule_mismatches,
    session_structure,
    structural_rule,
    ticker_microstructure,
    ticker_window_start,
    weekend_breaks,
)
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

H4 = timedelta(hours=4)
# 2025-03-07 is a Friday, 2025-03-08 a Saturday, 2025-03-10 a Monday.
FRIDAY = datetime(2025, 3, 7, 10, 0)
SATURDAY = datetime(2025, 3, 8, 10, 0)
MONDAY = datetime(2025, 3, 10, 10, 0)


def make_candle(
    open_time: datetime,
    *,
    open_price: float = 100.0,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    volume: int = 1000,
) -> Candle:
    return Candle(
        open_time=open_time,
        open=open_price,
        high=high if high is not None else open_price * 1.01,
        low=low if low is not None else open_price * 0.99,
        close=close,
        volume=volume,
    )


def make_trade(
    entry: datetime,
    exit_time: datetime,
    *,
    pnl: float = 100.0,
    quantity: float = 10_000.0,
    ticker: str = "SBER",
) -> PooledTrade:
    trade = Trade(
        trade_id=1,
        entry_time=entry,
        entry_index=0,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=101.0,
        sl_level=99.0,
        quantity=quantity,
        exit_time=exit_time,
        exit_index=1,
        exit_price=101.0,
        exit_type="TP",
        pnl_abs=pnl,
        pnl_net=pnl,
    )
    return PooledTrade(ticker=ticker, trade=trade)


def test_weekend_bar_is_decided_by_the_session_weekday():
    assert not is_weekend_bar(make_candle(FRIDAY))
    assert is_weekend_bar(make_candle(SATURDAY))
    assert is_weekend_bar(make_candle(datetime(2025, 3, 9, 10, 0)))


def test_bar_stats_count_zero_volume_and_range():
    candles = [
        make_candle(FRIDAY, open_price=100.0, high=101.0, low=99.0, volume=0),
        make_candle(SATURDAY, open_price=200.0, high=202.0, low=198.0, volume=500),
    ]
    stats = bar_stats(candles)
    assert stats.bars == 2
    assert stats.zero_volume == 1
    assert stats.zero_volume_share == pytest.approx(0.5)
    assert stats.median_volume == pytest.approx(500.0)
    assert stats.median_range_bps == pytest.approx(200.0)


def test_bar_stats_of_an_empty_set_is_zeroed():
    stats = bar_stats([])
    assert stats.bars == 0
    assert stats.zero_volume_share == 0.0
    assert stats.median_range_bps == 0.0


def test_window_starts_at_the_first_weekend_bar_of_the_ticker():
    candles = [
        make_candle(FRIDAY),
        make_candle(datetime(2025, 4, 12, 10, 0)),  # Saturday
    ]
    assert ticker_window_start(candles, start=date(2025, 3, 1)) == date(2025, 4, 12)


def test_window_start_keeps_the_configured_date_when_it_is_later():
    candles = [make_candle(SATURDAY)]
    assert ticker_window_start(candles, start=date(2025, 6, 1)) == date(2025, 6, 1)


def test_ticker_without_weekend_bars_is_out_of_the_audit():
    candles = [make_candle(FRIDAY)]
    assert ticker_window_start(candles, start=date(2025, 3, 1)) is None
    micro = ticker_microstructure("SBER", candles, start=date(2025, 3, 1))
    assert micro.window_start is None
    assert micro.weekend.bars == 0
    assert not micro.weekend_unusable


def test_microstructure_splits_the_window_into_weekend_and_weekday():
    candles = [
        make_candle(datetime(2025, 2, 3, 10, 0)),  # Monday, before the window, ignored
        make_candle(FRIDAY, volume=1000),  # before the first weekend bar, ignored
        make_candle(SATURDAY, volume=0),
        make_candle(MONDAY, volume=1000),
    ]
    micro = ticker_microstructure("SBER", candles, start=date(2025, 3, 1))
    assert micro.window_start == date(2025, 3, 8)
    assert micro.weekend.bars == 1
    assert micro.weekday.bars == 1
    assert micro.weekend_unusable is True


def test_zero_volume_criterion_is_per_ticker_not_pooled():
    thin = ticker_microstructure(
        "THIN",
        [make_candle(FRIDAY), make_candle(SATURDAY, volume=0)],
        start=date(2025, 3, 1),
    )
    liquid = ticker_microstructure(
        "LIQ",
        [make_candle(FRIDAY), make_candle(SATURDAY, volume=1000)],
        start=date(2025, 3, 1),
    )
    assert thin.weekend_unusable is True
    assert liquid.weekend_unusable is False


def test_session_structure_shows_a_short_weekend_session():
    candles = [
        make_candle(datetime(2025, 3, 7, 10, 0)),
        make_candle(datetime(2025, 3, 7, 14, 0)),
        make_candle(datetime(2025, 3, 8, 10, 0)),
    ]
    sessions = session_structure(candles)
    assert [item.bars for item in sessions] == [2, 1]
    assert sessions[0].weekend is False
    assert sessions[1].weekend is True
    assert sessions[1].span_hours == 0.0


def test_bar_calendar_marks_weekend_opens_and_ends():
    calendar = bar_calendar([make_candle(SATURDAY)], bar_duration=H4)
    assert SATURDAY in calendar.weekend_opens
    assert SATURDAY + H4 in calendar.weekend_ends


def test_entry_and_exit_on_a_weekend_bar_use_the_calendar():
    calendar = bar_calendar([make_candle(SATURDAY)], bar_duration=H4)
    trade = make_trade(SATURDAY, SATURDAY + H4).trade
    assert entry_is_weekend(trade, calendar) is True
    assert exit_is_weekend(trade, calendar) is True


def test_weekday_trade_is_not_a_weekend_trade():
    calendar = bar_calendar([make_candle(FRIDAY)], bar_duration=H4)
    trade = make_trade(FRIDAY, FRIDAY + H4).trade
    assert entry_is_weekend(trade, calendar) is False
    assert exit_is_weekend(trade, calendar) is False
    assert holding_spans_weekend(trade) is False


def test_friday_to_monday_holding_spans_a_weekend_without_weekend_bars():
    trade = make_trade(FRIDAY, MONDAY + H4).trade
    assert holding_spans_weekend(trade) is True


def test_open_trade_has_no_weekend_exit():
    trade = make_trade(FRIDAY, FRIDAY + H4).trade
    trade.exit_time = None
    assert exit_is_weekend(trade, None) is False
    assert holding_spans_weekend(trade) is False


def test_audit_groups_form_a_partition_that_sums_to_the_pool():
    thresholds = WeekendThresholds(min_sample_trades=1, bootstrap_iterations=50)
    calendars = {
        "SBER": bar_calendar([make_candle(SATURDAY)], bar_duration=H4),
    }
    pooled = [
        make_trade(SATURDAY, SATURDAY + H4),  # weekend entry and exit
        make_trade(FRIDAY, MONDAY + H4),  # spans only
        make_trade(FRIDAY, FRIDAY + H4),  # no weekend contact
    ]
    audit = audit_trades(pooled, calendars, thresholds=thresholds)
    assert audit.total == 3
    assert audit.both.trades == 1
    assert audit.spans_only.trades == 1
    assert audit.other.trades == 1
    assert audit.entry_only.trades == 0
    assert audit.exit_only.trades == 0
    assert audit.partition_trades == audit.total
    assert audit.entry_weekend_trades == 1
    assert audit.exit_weekend_trades == 1


def test_entry_only_and_exit_only_are_split_not_overlapping():
    thresholds = WeekendThresholds(min_sample_trades=1, bootstrap_iterations=50)
    calendars = {"SBER": bar_calendar([make_candle(SATURDAY)], bar_duration=H4)}
    pooled = [
        make_trade(SATURDAY, SATURDAY + H4),  # both
        make_trade(SATURDAY, MONDAY + H4),  # entry only (exit on a Monday bar)
        make_trade(FRIDAY, SATURDAY + H4),  # exit only
    ]
    audit = audit_trades(pooled, calendars, thresholds=thresholds)
    assert audit.both.trades == 1
    assert audit.entry_only.trades == 1
    assert audit.exit_only.trades == 1
    assert audit.partition_trades == 3 == audit.total
    assert audit.entry_weekend_trades == 2
    assert audit.exit_weekend_trades == 2


def test_group_below_the_floor_is_marked():
    thresholds = WeekendThresholds(min_sample_trades=30, bootstrap_iterations=50)
    pooled = [make_trade(SATURDAY, SATURDAY + H4)]
    audit = audit_trades(pooled, {}, thresholds=thresholds)
    assert audit.both.trades == 1
    assert audit.both.below_floor is True


def test_empty_group_has_zero_interval_and_is_below_the_floor():
    thresholds = WeekendThresholds(min_sample_trades=30, bootstrap_iterations=50)
    audit = audit_trades([make_trade(FRIDAY, FRIDAY + H4)], {}, thresholds=thresholds)
    assert audit.entry_only.trades == 0
    assert audit.entry_only.ci_low == 0.0
    assert audit.entry_only.ci_high == 0.0
    assert audit.entry_only.below_floor is True


def test_weekend_break_sums_every_transition_inside_it():
    # Friday close 100, Saturday opens at 96.78 (-3.22%), Monday opens at 85.27
    # (-11.89% from the Saturday open). Sum is what ADR-030 reports for MTSS 2025.
    candles = [
        make_candle(FRIDAY, open_price=100.0, close=100.0),
        make_candle(SATURDAY, open_price=96.78, close=96.78),
        make_candle(MONDAY, open_price=85.27, close=85.27),
    ]
    breaks = weekend_breaks(candles)
    assert len(breaks) == 1
    assert breaks[0].transitions == 2
    assert breaks[0].weekend_traded is True
    assert breaks[0].resumed_on_monday is True
    assert breaks[0].total_percent == pytest.approx(-3.22 - 11.89, abs=0.01)


def test_weekday_transitions_are_not_a_break():
    candles = [
        make_candle(datetime(2025, 3, 4, 10, 0)),
        make_candle(datetime(2025, 3, 5, 10, 0)),
    ]
    assert weekend_breaks(candles) == []


def test_break_flags_a_weekend_that_does_not_resume_on_monday():
    candles = [
        make_candle(FRIDAY, open_price=100.0, close=100.0),
        make_candle(SATURDAY, open_price=98.0, close=98.0),
        make_candle(datetime(2025, 3, 11, 10, 0), open_price=97.0),  # Tuesday
    ]
    breaks = weekend_breaks(candles)
    assert len(breaks) == 1
    assert breaks[0].weekend_traded is True
    assert breaks[0].resumed_on_monday is False


def test_schedule_mismatch_in_both_directions():
    candles = [
        make_candle(FRIDAY),
        make_candle(SATURDAY),  # broker says: not a trading day
        make_candle(datetime(2025, 3, 11, 10, 0)),  # keeps 03-10 inside the range
    ]
    schedule = [
        ScheduleDay(day=date(2025, 3, 7), is_trading_day=True),
        ScheduleDay(day=date(2025, 3, 8), is_trading_day=False),
        ScheduleDay(day=date(2025, 3, 10), is_trading_day=True),  # no candles
    ]
    mismatches = schedule_mismatches(schedule, candles)
    assert [(item.day.isoformat(), item.kind) for item in mismatches] == [
        ("2025-03-08", CANDLES_WITHOUT_TRADING_DAY),
        ("2025-03-10", TRADING_DAY_WITHOUT_CANDLES),
    ]


def test_schedule_days_outside_the_candle_range_are_ignored():
    candles = [make_candle(FRIDAY)]
    schedule = [ScheduleDay(day=date(2025, 5, 1), is_trading_day=True)]
    assert schedule_mismatches(schedule, candles) == []


def test_structural_rule_is_none_when_nothing_fires():
    thresholds = WeekendThresholds()
    liquid = ticker_microstructure(
        "SBER",
        [make_candle(FRIDAY), make_candle(SATURDAY, volume=1000)],
        start=date(2025, 3, 1),
    )
    assert structural_rule([liquid], [], thresholds=thresholds) is None


def test_structural_rule_names_the_criterion_and_the_ticker():
    thresholds = WeekendThresholds(max_zero_volume_share=0.1)
    thin = ticker_microstructure(
        "THIN",
        [make_candle(FRIDAY), make_candle(SATURDAY, volume=0)],
        start=date(2025, 3, 1),
    )
    rule = structural_rule([thin], [], thresholds=thresholds)
    assert rule is not None
    assert "zero-volume" in rule
    assert "THIN" in rule


def test_structural_rule_names_the_schedule_criterion():
    thresholds = WeekendThresholds()
    mismatches = schedule_mismatches(
        [ScheduleDay(day=date(2025, 3, 8), is_trading_day=False)],
        [make_candle(SATURDAY)],
    )
    rule = structural_rule([], mismatches, thresholds=thresholds)
    assert rule is not None
    assert "non-trading" in rule
    assert "2025-03-08" in rule


def test_load_weekend_thresholds_reads_the_shared_floor(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(
        json.dumps(
            {
                "version": "5",
                "min_sample_trades": 30,
                "weekend": {"start": "2025-03-01", "max_zero_volume_share": 0.1},
                "multi_instrument": {"block_days": 30, "bootstrap_iterations": 10, "seed": 7},
            }
        ),
        encoding="utf-8",
    )
    thresholds = load_weekend_thresholds(path)
    assert thresholds.start == date(2025, 3, 1)
    assert thresholds.min_sample_trades == 30
    assert thresholds.bootstrap_iterations == 10
    assert thresholds.seed == 7
    assert thresholds.fingerprint.startswith("5+")


def test_load_weekend_thresholds_requires_the_shared_floor(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(
        json.dumps(
            {"version": "5", "weekend": {"start": "2025-03-01", "max_zero_volume_share": 0.1}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="min_sample_trades"):
        load_weekend_thresholds(path)


def test_weekend_thresholds_reject_a_share_above_one():
    with pytest.raises(ValueError, match="max_zero_volume_share"):
        WeekendThresholds(max_zero_volume_share=1.5)


def test_bar_calendar_is_empty_for_weekdays_only():
    calendar: BarCalendar = bar_calendar([make_candle(FRIDAY)], bar_duration=H4)
    assert calendar.weekend_opens == frozenset()
    assert calendar.weekend_ends == frozenset()
