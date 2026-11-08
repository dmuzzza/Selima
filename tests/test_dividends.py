"""Dividend calendar: ex-bar rule, gap match, marking, coverage, config parsing."""

import json
from datetime import date, datetime, timedelta

import pytest

from src.backtest.dividends import (
    APPLIED,
    FUTURE,
    NO_GAP_MATCH,
    NOT_PAID,
    OUT_OF_HISTORY,
    DividendCalendar,
    DividendEvent,
    build_coverage,
    clear_dividends_cache,
    clear_threshold_cache,
    find_ex_bar,
    load_dividend_thresholds,
    load_dividends,
    mark_dividend_affected,
    resolve_event,
    resolve_events,
)
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

DAY = timedelta(days=1)


def candle(open_time: datetime, open_price: float, close: float) -> Candle:
    return Candle(
        open_time=open_time,
        open=open_price,
        high=max(open_price, close),
        low=min(open_price, close),
        close=close,
        volume=1_000,
    )


def make_trade(entry: datetime, exit_time: datetime, pnl: float = 100.0) -> Trade:
    return Trade(
        trade_id=1,
        entry_time=entry,
        entry_index=0,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=101.0,
        sl_level=99.0,
        quantity=10_000.0,
        exit_time=exit_time,
        exit_index=1,
        exit_price=101.0,
        exit_type="TP",
        pnl_abs=pnl,
        pnl_net=pnl,
    )


def sber_like_candles() -> list[Candle]:
    """Three bars: the middle one opens below the previous close (ex-dividend)."""
    start = datetime(2024, 7, 9, 3, 0)
    return [
        candle(start, 324.30, 319.99),
        candle(start + DAY, 319.00, 316.68),
        candle(start + 2 * DAY, 289.96, 295.87),
    ]


# ── ex-bar rule ────────────────────────────────────────────────────────────


def test_ex_bar_is_first_bar_strictly_after_last_buy_date():
    candles = sber_like_candles()
    found = find_ex_bar(candles, date(2024, 7, 10))
    assert found is not None
    previous, ex_bar = found
    assert previous.open_time.date() == date(2024, 7, 10)
    assert ex_bar.open_time.date() == date(2024, 7, 11)


def test_ex_bar_accepts_a_weekend_bar():
    """Weekend trading is real since 2025-03; the rule must not skip those bars."""
    friday = datetime(2026, 7, 17, 3, 0)
    saturday = datetime(2026, 7, 18, 3, 0)
    monday = datetime(2026, 7, 20, 3, 0)
    candles = [
        candle(friday, 277.50, 279.92),
        candle(saturday, 247.30, 249.34),
        candle(monday, 251.05, 260.99),
    ]
    found = find_ex_bar(candles, date(2026, 7, 17))
    assert found is not None
    assert found[1].open_time.date() == date(2026, 7, 18)
    assert found[1].open_time.weekday() == 5


def test_ex_bar_missing_when_no_bar_after_the_date():
    assert find_ex_bar(sber_like_candles(), date(2030, 1, 1)) is None


# ── gap match ──────────────────────────────────────────────────────────────


def test_sber_2024_event_is_applied():
    """SBER: 33.30 per share on a 316.68 close, the bar opens 8.44% lower."""
    event = DividendEvent("SBER", date(2024, 7, 10), 33.3)
    resolved = resolve_event(event, sber_like_candles(), gap_match_ratio=0.3)
    assert resolved.status == APPLIED
    assert resolved.ex_bar_open is not None
    assert resolved.ex_bar_open.date() == date(2024, 7, 11)
    assert resolved.drop_percent == pytest.approx(8.44, abs=0.01)
    assert resolved.expected_gap_percent == pytest.approx(10.52, abs=0.01)
    assert resolved.weekend_ex_bar is False


def test_sber_2026_event_lands_on_a_weekend_bar_and_is_applied():
    friday = datetime(2026, 7, 17, 3, 0)
    saturday = datetime(2026, 7, 18, 3, 0)
    candles = [
        candle(friday - DAY, 288.28, 277.02),
        candle(friday, 277.50, 279.92),
        candle(saturday, 247.30, 249.34),
    ]
    event = DividendEvent("SBER", date(2026, 7, 17), 37.64)
    resolved = resolve_event(event, candles, gap_match_ratio=0.3)
    assert resolved.status == APPLIED
    assert resolved.ex_bar_open is not None
    assert resolved.ex_bar_open.date() == date(2026, 7, 18)
    assert resolved.weekend_ex_bar is True
    assert resolved.status_label == "applied (weekend_ex_bar)"


def test_price_rise_never_matches_even_when_large():
    """A gap up is not an ex-dividend bar, however big it is."""
    start = datetime(2024, 7, 9, 3, 0)
    candles = [
        candle(start, 100.0, 100.0),
        candle(start + DAY, 130.0, 131.0),
    ]
    event = DividendEvent("SBER", date(2024, 7, 9), 5.0)
    resolved = resolve_event(event, candles, gap_match_ratio=0.3)
    assert resolved.status == NO_GAP_MATCH


def test_pre_switch_event_drops_on_the_last_buy_session():
    """Before the MOEX T+1 switch the API's `last_buy_date` is the ex-date itself.

    SBER 2023-05-10: the bar of that day opens 9.14% lower. The old "strictly
    after" rule reported `no_gap_match` and left the trade unmarked (ADR-030).
    """
    start = datetime(2023, 5, 8, 3, 0)
    candles = [
        candle(start, 238.69, 238.27),
        candle(start + 2 * DAY, 216.50, 227.06),
        candle(start + 3 * DAY, 228.87, 229.32),
    ]
    event = DividendEvent("SBER", date(2023, 5, 10), 25.0)
    resolved = resolve_event(event, candles, gap_match_ratio=0.3)
    assert resolved.status == APPLIED
    assert resolved.ex_bar_open is not None
    assert resolved.ex_bar_open.date() == date(2023, 5, 10)
    assert resolved.drop_percent == pytest.approx(9.14, abs=0.01)
    assert resolved.expected_gap_percent == pytest.approx(10.49, abs=0.01)


def test_switch_boundary_decides_which_session_is_the_ex_bar():
    """The regime changes on 2023-07-31: same date, two different ex-bars."""
    candles = [
        candle(datetime(2023, 7, 27, 3, 0), 100.0, 100.0),
        candle(datetime(2023, 7, 28, 3, 0), 90.0, 95.0),
        candle(datetime(2023, 7, 31, 3, 0), 91.0, 92.0),
    ]
    before = find_ex_bar(candles, date(2023, 7, 28))
    assert before is not None
    assert before[1].open_time.date() == date(2023, 7, 28)

    at_switch = find_ex_bar(
        candles, date(2023, 7, 28), t1_settlement_switch=date(2023, 7, 28)
    )
    assert at_switch is not None
    assert at_switch[1].open_time.date() == date(2023, 7, 31)


def test_pre_switch_event_on_a_non_trading_day_uses_the_next_session():
    """A date with no session of its own falls back to the next session."""
    candles = [
        candle(datetime(2023, 7, 7, 3, 0), 100.0, 100.0),
        candle(datetime(2023, 7, 10, 3, 0), 90.0, 95.0),
    ]
    found = find_ex_bar(candles, date(2023, 7, 8))
    assert found is not None
    assert found[1].open_time.date() == date(2023, 7, 10)


def test_post_switch_event_keeps_using_the_next_session():
    """SBER 2023-10-02: after the switch the drop sits on the following session."""
    candles = [
        candle(datetime(2023, 9, 29, 3, 0), 260.0, 262.0),
        candle(datetime(2023, 10, 2, 3, 0), 240.0, 245.0),
    ]
    found = find_ex_bar(candles, date(2023, 9, 29))
    assert found is not None
    assert found[1].open_time.date() == date(2023, 10, 2)


def test_event_after_the_last_candle_is_future_not_dropped():
    """A declared future payout is kept: live needs the ex-date before it happens."""
    event = DividendEvent("SBER", date(2030, 1, 1), 10.0)
    resolved = resolve_event(event, sber_like_candles())
    assert resolved.status == FUTURE
    assert resolved.ex_bar_open is None
    assert "no candle" in resolved.reason


def test_event_before_the_first_candle_is_out_of_history():
    """Comparing it with the first bars would invent a gap at the start of history."""
    event = DividendEvent("SBER", date(2019, 1, 1), 10.0)
    resolved = resolve_event(event, sber_like_candles())
    assert resolved.status == OUT_OF_HISTORY
    assert resolved.ex_bar_open is None


def test_declared_but_not_paid_event_never_marks_a_trade():
    """GAZP 2022-10-10: the meeting voted the payout down, no gap must be expected."""
    event = DividendEvent("SBER", date(2024, 7, 10), 33.3, status="not_paid")
    resolved = resolve_event(event, sber_like_candles())
    assert resolved.status == NOT_PAID
    assert "not paid" in resolved.reason


def test_gap_match_ratio_must_be_positive():
    event = DividendEvent("SBER", date(2024, 7, 10), 33.3)
    with pytest.raises(ValueError):
        resolve_event(event, sber_like_candles(), gap_match_ratio=0.0)


def test_known_limitation_mtss_2025_splits_the_gap_across_a_weekend_bar():
    """Known limitation (T2-23): a weekend bar takes part of the drop.

    MTSS 2025: last buy 2025-07-04, the ex-bar is Saturday 2025-07-05 and falls
    only 3.22% of a 15.52% expected gap; the rest lands on Monday. The event is
    therefore `no_gap_match` and trades across it are not marked.

    When the weekend-gap rule is fixed (T2-23), this test must fail and bring the
    case back. Do not "fix" it by lowering the ratio.
    """
    thursday = datetime(2025, 7, 3, 3, 0)
    candles = [
        candle(thursday, 234.50, 228.50),
        candle(thursday + DAY, 228.00, 225.50),
        candle(thursday + 2 * DAY, 218.25, 218.25),
        candle(thursday + 4 * DAY, 192.30, 193.80),
    ]
    event = DividendEvent("MTSS", date(2025, 7, 4), 35.0)
    resolved = resolve_event(event, candles, gap_match_ratio=0.3)
    assert resolved.status == NO_GAP_MATCH
    assert resolved.weekend_ex_bar is True
    assert resolved.ex_bar_open is not None
    assert resolved.ex_bar_open.date() == date(2025, 7, 5)


# ── trade marking ──────────────────────────────────────────────────────────


def test_trade_is_marked_when_ex_bar_is_inside_the_holding_period():
    ex_bar = datetime(2024, 7, 11, 3, 0)
    spanning = make_trade(datetime(2024, 7, 10, 3, 0), datetime(2024, 7, 12, 3, 0))
    before = make_trade(datetime(2024, 7, 1, 3, 0), datetime(2024, 7, 9, 3, 0))
    after = make_trade(datetime(2024, 7, 11, 3, 0), datetime(2024, 7, 15, 3, 0))
    flags = mark_dividend_affected([spanning, before, after], [ex_bar])
    assert flags == [True, False, False]


def test_open_trade_is_never_marked():
    trade = make_trade(datetime(2024, 7, 10, 3, 0), datetime(2024, 7, 12, 3, 0))
    trade.exit_time = None
    assert mark_dividend_affected([trade], [datetime(2024, 7, 11, 3, 0)]) == [False]


def test_no_events_means_no_marks():
    trade = make_trade(datetime(2024, 7, 10, 3, 0), datetime(2024, 7, 12, 3, 0))
    assert mark_dividend_affected([trade], []) == [False]


def test_marking_does_not_change_the_trade():
    """The calendar is a label: PnL and the set of trades must be untouched."""
    trade = make_trade(datetime(2024, 7, 10, 3, 0), datetime(2024, 7, 12, 3, 0), 123.45)
    before = (trade.pnl_net, trade.pnl_abs, trade.entry_time, trade.exit_time)
    mark_dividend_affected([trade], [datetime(2024, 7, 11, 3, 0)])
    assert (trade.pnl_net, trade.pnl_abs, trade.entry_time, trade.exit_time) == before


# ── config ─────────────────────────────────────────────────────────────────


def test_shipped_calendar_loads_and_has_a_fingerprint():
    calendar = load_dividends()
    assert calendar.version
    assert len(calendar.sha256) == 64
    assert calendar.events
    assert "SBER" in calendar.tickers()


def test_shipped_calendar_has_no_events_for_unknown_tickers():
    calendar = load_dividends()
    for event in calendar.events:
        assert event.ticker == event.ticker.upper()
        assert event.dividend_gross > 0
        assert event.source in {"api", "manual"}


def test_sha256_changes_when_a_value_changes(tmp_path):
    payload: dict = {
        "version": "1",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 33.3,
                "source": "api",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    first = load_dividends(path).sha256

    payload["events"][0]["dividend_gross"] = 33.31
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    second = load_dividends(path).sha256
    assert first != second


def test_reformatting_the_file_keeps_the_fingerprint(tmp_path):
    payload = {
        "version": "1",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 33.3,
                "source": "api",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    clear_dividends_cache()
    first = load_dividends(path).sha256

    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    clear_dividends_cache()
    assert load_dividends(path).sha256 == first


def test_manual_event_without_reference_is_refused(tmp_path):
    payload = {
        "version": "1",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 33.3,
                "source": "manual",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    with pytest.raises(ValueError, match="reference"):
        load_dividends(path)


def test_manual_event_with_reference_is_accepted(tmp_path):
    payload = {
        "version": "1",
        "events": [
            {
                "ticker": "MTSS",
                "last_buy_date": "2025-07-04",
                "dividend_gross": 35.0,
                "source": "manual",
                "reference": "ir.mts.ru corporate release 2025-06-05",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    calendar = load_dividends(path)
    assert calendar.events[0].reference


def test_unknown_source_is_refused(tmp_path):
    payload = {
        "version": "1",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 33.3,
                "source": "guess",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    with pytest.raises(ValueError, match="source"):
        load_dividends(path)


def test_payouts_split_is_parsed_and_must_sum_to_the_total(tmp_path):
    """Two payouts on one ex-date: CHMF 2024-06-17 is 191.51 + 38.30."""
    event = {
        "ticker": "CHMF",
        "last_buy_date": "2024-06-17",
        "dividend_gross": 229.81,
        "source": "api",
        "payouts": [
            {"dividend_gross": 191.51, "record_date": "2024-06-18"},
            {"dividend_gross": 38.3, "record_date": "2024-06-18"},
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps({"version": "3", "events": [event]}), encoding="utf-8")
    clear_dividends_cache()
    parsed = load_dividends(path).events[0]
    assert parsed.dividend_gross == pytest.approx(229.81)
    assert parsed.payouts == pytest.approx((191.51, 38.3))

    event["dividend_gross"] = 200.0
    path.write_text(json.dumps({"version": "3", "events": [event]}), encoding="utf-8")
    clear_dividends_cache()
    with pytest.raises(ValueError, match="does not match"):
        load_dividends(path)


def test_annotations_layer_survives_and_applies_status(tmp_path):
    """The hand-written layer is separate from the API rows, so a rewrite keeps it."""
    payload = {
        "version": "3",
        "annotations": {
            "GAZP|2022-10-10": {
                "status": "not_paid",
                "note": "the meeting on 2022-09-30 voted the payout down",
                "reference": "company release 2022-09-30",
            }
        },
        "events": [
            {
                "ticker": "GAZP",
                "last_buy_date": "2022-10-10",
                "dividend_gross": 51.03,
                "source": "api",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    event = load_dividends(path).events[0]
    assert event.status == "not_paid"
    assert event.note and "voted" in event.note
    assert event.reference


def test_unknown_event_status_is_refused(tmp_path):
    payload = {
        "version": "3",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 33.3,
                "source": "api",
                "status": "maybe",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    with pytest.raises(ValueError, match="status"):
        load_dividends(path)


def test_non_positive_amount_is_refused(tmp_path):
    payload = {
        "version": "1",
        "events": [
            {
                "ticker": "SBER",
                "last_buy_date": "2024-07-10",
                "dividend_gross": 0,
                "source": "api",
            }
        ],
    }
    path = tmp_path / "dividends.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    clear_dividends_cache()
    with pytest.raises(ValueError, match="positive"):
        load_dividends(path)


def test_shipped_thresholds_have_the_sanity_floor():
    thresholds = load_dividend_thresholds()
    assert thresholds.gap_match_ratio == pytest.approx(0.3)
    assert thresholds.model_revision
    clear_threshold_cache()


# ── coverage ───────────────────────────────────────────────────────────────


def test_coverage_lists_rejected_events_with_a_reason():
    start = datetime(2024, 7, 9, 3, 0)
    candles = {
        "SBER": [
            candle(start, 320.0, 318.0),
            candle(start + DAY, 319.0, 320.0),
            candle(start + 2 * DAY, 321.0, 322.0),
        ]
    }
    calendar = DividendCalendar(
        version="test",
        sha256="0" * 64,
        source="memory",
        events=(DividendEvent("SBER", date(2024, 7, 10), 33.0),),
    )
    resolved = resolve_events(calendar, candles, gap_match_ratio=0.3)
    coverage = build_coverage(
        calendar,
        resolved,
        {"SBER": {candles["SBER"][2].open_time: 0.8}},
        tickers=["SBER"],
        years=[2024],
        gap_threshold_percent=5.0,
    )
    assert coverage.applied == 0
    assert coverage.no_gap_match == 1
    assert coverage.rejected_events
    assert coverage.rejected_events[0].reason
    assert coverage.missing_years == {}


def test_coverage_reports_years_without_events():
    calendar = DividendCalendar(
        version="test",
        sha256="0" * 64,
        source="memory",
        events=(DividendEvent("SBER", date(2024, 7, 10), 33.3),),
    )
    coverage = build_coverage(
        calendar,
        [],
        {},
        tickers=["SBER", "GAZP"],
        years=[2023, 2024],
        gap_threshold_percent=5.0,
    )
    assert coverage.missing_years["SBER"] == (2023,)
    assert coverage.missing_years["GAZP"] == (2023, 2024)


def test_coverage_reports_unexplained_gaps_only_above_the_threshold():
    candles = sber_like_candles()
    calendar = DividendCalendar(
        version="test",
        sha256="0" * 64,
        source="memory",
        events=(DividendEvent("SBER", date(2024, 7, 10), 33.3),),
    )
    resolved = resolve_events(calendar, {"SBER": candles}, gap_match_ratio=0.3)
    gaps = {"SBER": {candles[1].open_time: -0.2, candles[2].open_time: -8.44}}
    coverage = build_coverage(
        calendar,
        resolved,
        gaps,
        tickers=["SBER"],
        years=[2024],
        gap_threshold_percent=5.0,
    )
    # The 8.44% gap is the applied ex-bar and is explained; the small one is below
    # the threshold and is not listed at all.
    assert coverage.unexplained_gaps == ()

    gaps["SBER"][candles[1].open_time] = -7.5
    coverage = build_coverage(
        calendar,
        resolved,
        gaps,
        tickers=["SBER"],
        years=[2024],
        gap_threshold_percent=5.0,
    )
    assert len(coverage.unexplained_gaps) == 1
    assert coverage.unexplained_gaps[0].gap_percent == pytest.approx(-7.5)


def test_coverage_requires_a_positive_gap_threshold():
    calendar = DividendCalendar("test", "0" * 64, "memory", ())
    with pytest.raises(ValueError):
        build_coverage(
            calendar, [], {}, tickers=[], years=[], gap_threshold_percent=0.0
        )
