"""Event-study power: window placement, eligibility filters, unconditional sigma.

The pre-check exists to refuse a journal slot before the strategy is written, so
the tests pin the two things that can make it lie: a window that is not the one
the idea would trade, and an eligibility filter that silently drops events.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from scripts.evaluate import parse_windows
from src.backtest.dividends import DividendEvent
from src.backtest.evaluate import (
    PowerReport,
    build_power_row,
    mde_from_sigma,
    render_power_report,
    target_for_trades,
)
from src.backtest.event_study import (
    CLUSTER_BY_MONTH,
    CLUSTER_BY_QUARTER,
    CLUSTER_BY_YEAR,
    basket_index,
    cluster_count,
    cluster_design_effect,
    cluster_key,
    median_sigma_bps,
    rolling_returns_bps,
    scan_event_windows,
    standard_error_bps,
    unconditional_excess_sigma,
    unconditional_sigma,
    window_return_bps,
)
from src.market_data.models import Candle

DAY = timedelta(days=1)
START = date(2024, 1, 1)
SEALED = date(2030, 1, 1)


def series(length: int = 60, *, step: float = 1.0, base: float = 100.0) -> list[Candle]:
    """Daily bars with a deterministic close: 100, 101, 102, ..."""
    start = datetime(2024, 1, 1, 10, 0)
    return [
        Candle(
            open_time=start + index * DAY,
            open=base + index * step,
            high=base + index * step + 0.5,
            low=base + index * step - 0.5,
            close=base + index * step,
            volume=1_000,
        )
        for index in range(length)
    ]


def event(
    *,
    last_buy: date,
    declared: date | None = None,
    status: str = "active",
    ticker: str = "SBER",
) -> DividendEvent:
    return DividendEvent(
        ticker=ticker,
        last_buy_date=last_buy,
        dividend_gross=10.0,
        declared_date=declared,
        status=status,
    )


def bar_date(candles: list[Candle], index: int) -> date:
    return candles[index].open_time.date()


# ── window placement ───────────────────────────────────────────────────────


def test_entry_is_window_sessions_before_the_last_cum_dividend_bar():
    candles = series()
    last_buy = bar_date(candles, 30)
    scan = scan_event_windows(
        {"SBER": candles}, [event(last_buy=last_buy)], window_sessions=10, sealed_start=SEALED
    )

    assert scan.events == 1
    window = scan.windows[0]
    assert window.exit_index == 30  # the bar before the ex-bar
    assert window.entry_index == 20
    assert window.exit_time == candles[30].open_time
    assert window.entry_time == candles[20].open_time


def test_window_return_is_close_to_close_in_bps():
    candles = series(step=1.0)
    window = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=bar_date(candles, 30))],
        window_sessions=10,
        sealed_start=SEALED,
    ).windows[0]

    # close 120 -> close 130: +8.33%
    assert window_return_bps(candles, window) == pytest.approx(833.33, abs=0.01)


def test_ex_bar_rule_still_follows_the_settlement_switch():
    """Before T+1 the drop sits on the last_buy_date session itself."""
    candles = series()
    last_buy = bar_date(candles, 30)
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=last_buy)],
        window_sessions=5,
        sealed_start=SEALED,
        t1_settlement_switch=date(2023, 7, 31),  # event is after the switch
    )
    assert scan.windows[0].exit_index == 30


# ── eligibility filters ────────────────────────────────────────────────────


def test_event_declared_after_the_entry_is_look_ahead_and_excluded():
    candles = series()
    last_buy = bar_date(candles, 30)
    entry_date = bar_date(candles, 20)
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=last_buy, declared=entry_date + DAY)],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 0
    assert scan.declared_after_entry == 1
    assert scan.missing_declared_date == 0


def test_event_declared_before_the_entry_is_kept():
    candles = series()
    last_buy = bar_date(candles, 30)
    entry_date = bar_date(candles, 20)
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=last_buy, declared=entry_date - 30 * DAY)],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 1
    assert scan.declared_after_entry == 0


def test_missing_declaration_date_is_counted_not_silently_kept():
    candles = series()
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=bar_date(candles, 30))],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 1
    assert scan.missing_declared_date == 1
    assert scan.windows[0].declaration_known is False


def test_sealed_period_is_excluded_from_the_pre_check():
    candles = series()
    last_buy = bar_date(candles, 30)
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=last_buy)],
        window_sessions=10,
        sealed_start=bar_date(candles, 31),
    )

    assert scan.events == 0
    assert scan.sealed == 1


def test_event_without_enough_history_is_counted():
    candles = series()
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=bar_date(candles, 5))],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 0
    assert scan.out_of_history == 1


def test_future_event_is_counted_separately():
    candles = series()
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=bar_date(candles, len(candles) - 1) + 10 * DAY)],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 0
    assert scan.future == 1
    assert scan.out_of_history == 0


def test_not_paid_event_is_kept_by_default_and_can_be_dropped():
    candles = series()
    last_buy = bar_date(candles, 30)
    events = [event(last_buy=last_buy, status="not_paid")]

    kept = scan_event_windows(
        {"SBER": candles}, events, window_sessions=10, sealed_start=SEALED
    )
    dropped = scan_event_windows(
        {"SBER": candles},
        events,
        window_sessions=10,
        sealed_start=SEALED,
        include_not_paid=False,
    )

    assert kept.events == 1
    assert kept.windows[0].status == "not_paid"
    assert dropped.events == 0


def test_unknown_ticker_counts_as_out_of_history():
    scan = scan_event_windows(
        {"SBER": series()},
        [event(last_buy=date(2024, 1, 30), ticker="GAZP")],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 0
    assert scan.out_of_history == 1


def test_per_ticker_counts_sum_to_the_event_total():
    candles = series()
    last_buy = bar_date(candles, 30)
    scan = scan_event_windows(
        {"SBER": candles, "GAZP": candles},
        [event(last_buy=last_buy), event(last_buy=last_buy, ticker="GAZP")],
        window_sessions=10,
        sealed_start=SEALED,
    )

    assert scan.events == 2
    assert scan.per_ticker() == {"GAZP": 1, "SBER": 1}
    assert sum(scan.per_ticker().values()) == scan.events


# ── sigma ──────────────────────────────────────────────────────────────────


def test_rolling_returns_count_and_values():
    candles = series(length=30, step=1.0)
    returns = rolling_returns_bps(candles, 10)

    assert len(returns) == 20
    # 100 -> 110 is +10%, the same for every window
    assert returns[0] == pytest.approx(1000.0, abs=0.01)
    # last window: 119 -> 129
    assert returns[-1] == pytest.approx((129 / 119 - 1) * 10_000, abs=0.01)


def test_unconditional_sigma_is_the_same_as_the_only_ticker():
    candles = series(length=50, step=1.0)
    sigma = unconditional_sigma({"SBER": candles}, 10)

    # One ticker: the pooled number is that ticker's own dispersion.
    assert sigma.sigma_bps is not None
    assert sigma.windows == 40
    assert sigma.per_ticker["SBER"] == pytest.approx(sigma.sigma_bps, abs=1e-9)


def test_unconditional_sigma_is_positive_for_noisy_series():
    up = series(length=50, step=1.0)
    down = series(length=50, step=-1.0, base=200.0)
    sigma = unconditional_sigma({"SBER": up, "GAZP": down}, 10)

    assert sigma.sigma_bps is not None
    assert sigma.sigma_bps > 0
    assert set(sigma.per_ticker) == {"SBER", "GAZP"}
    assert median_sigma_bps(sigma) is not None


def test_unconditional_sigma_ignores_non_positive_closes():
    candles = series(length=30)
    candles[5].close = 0.0
    sigma = unconditional_sigma({"SBER": candles}, 10)

    assert sigma.sigma_bps is not None


def test_sigma_needs_two_observations():
    sigma = unconditional_sigma({"SBER": series(length=11)}, 10)

    assert sigma.sigma_bps is None
    assert sigma.windows == 0  # one return is not a distribution


def test_standard_error_of_the_mean():
    assert standard_error_bps(570.0, 130) == pytest.approx(570.0 / 130**0.5)
    assert standard_error_bps(None, 130) is None
    assert standard_error_bps(570.0, 1) is None


# ── excess return to the basket ────────────────────────────────────────────


def test_basket_index_is_normalised_per_ticker():
    up = series(length=10, step=1.0, base=100.0)
    big = series(length=10, step=10.0, base=1000.0)
    index = basket_index({"SBER": up, "GAZP": big})

    # Both series start at 1.0 after normalisation, whatever the price level.
    assert index[up[0].open_time.date()] == pytest.approx(1.0)
    assert index[up[-1].open_time.date()] == pytest.approx(
        (up[-1].close / up[0].close + big[-1].close / big[0].close) / 2
    )


def test_excess_sigma_of_identical_series_is_zero():
    """Two identical tickers: the basket moves with them, nothing is left."""
    same = series(length=40, step=1.0)
    sigma = unconditional_excess_sigma({"SBER": same, "GAZP": same}, 10)

    assert sigma.sigma_bps == pytest.approx(0.0, abs=1e-9)


def test_excess_sigma_is_below_raw_sigma_for_common_moves():
    """A common market move dominates raw dispersion; the basket removes it."""
    market = series(length=40, step=1.0)
    # Two tickers that follow the same trend with a small independent wobble.
    a = series(length=40, step=1.0)
    b = series(length=40, step=1.0)
    for index in range(len(a)):
        wobble = 0.004 if index % 2 else -0.004
        a[index].close = a[index].close * (1 + wobble)
        a[index].high = a[index].close
        b[index].close = b[index].close * (1 - wobble)
        b[index].high = b[index].close
    assert len(market) == len(a)

    raw = unconditional_sigma({"AAA": a, "BBB": b}, 10)
    excess = unconditional_excess_sigma({"AAA": a, "BBB": b}, 10)

    assert raw.sigma_bps is not None and excess.sigma_bps is not None
    assert excess.sigma_bps < raw.sigma_bps


def test_basket_excludes_the_traded_stock():
    """The traded stock must not be part of its own benchmark."""
    a = series(length=20, step=1.0, base=100.0)
    b = series(length=20, step=2.0, base=100.0)
    c = series(length=20, step=3.0, base=100.0)

    everything = basket_index({"AAA": a, "BBB": b, "CCC": c})
    without_aaa = basket_index({"AAA": a, "BBB": b, "CCC": c}, exclude="AAA")

    last = a[-1].open_time.date()
    assert everything[last] == pytest.approx(
        (a[-1].close / a[0].close + b[-1].close / b[0].close + c[-1].close / c[0].close) / 3
    )
    assert without_aaa[last] == pytest.approx(
        (b[-1].close / b[0].close + c[-1].close / c[0].close) / 2
    )


def test_excess_sigma_falls_back_to_raw_for_a_single_ticker():
    """With no peers the ticker is compared with itself: excess == raw."""
    candles = series(length=40, step=1.0)
    raw = unconditional_sigma({"AAA": candles}, 10)
    excess = unconditional_excess_sigma({"AAA": candles}, 10)

    assert raw.sigma_bps == pytest.approx(excess.sigma_bps)


# ── clustering ─────────────────────────────────────────────────────────────


def test_cluster_key_by_quarter_month_year():
    day = date(2024, 7, 15)

    assert cluster_key(day, CLUSTER_BY_QUARTER) == (2024, 3)
    assert cluster_key(day, CLUSTER_BY_MONTH) == (2024, 7)
    assert cluster_key(day, CLUSTER_BY_YEAR) == (2024, 0)
    with pytest.raises(ValueError):
        cluster_key(day, "week")


def test_cluster_count_counts_seasons_not_events():
    candles = series(length=200)
    # Three events: two in the first quarter, one in the second.
    events = [
        event(last_buy=bar_date(candles, 10)),
        event(last_buy=bar_date(candles, 20)),
        event(last_buy=bar_date(candles, 150)),
    ]
    scan = scan_event_windows(
        {"SBER": candles}, events, window_sessions=2, sealed_start=SEALED
    )

    assert scan.events == 3
    assert cluster_count(scan.windows, CLUSTER_BY_QUARTER) == 2
    assert cluster_count(scan.windows, CLUSTER_BY_YEAR) == 1
    assert cluster_design_effect(scan.events, 2) == pytest.approx(1.5)


def test_cluster_design_effect_is_one_without_clustering():
    assert cluster_design_effect(0, 0) == 1.0
    assert cluster_design_effect(5, 5) == 1.0


# ── MDE from sigma ─────────────────────────────────────────────────────────


def test_mde_from_sigma_matches_the_hand_calculation():
    """sigma 570, 130 events, 80% power, one trial: ~140 bps."""
    assert mde_from_sigma(570.0, 130, alpha=0.05, power=0.80, n_trials=1) == pytest.approx(
        140.1, abs=0.5
    )


def test_mde_from_sigma_grows_with_trials():
    one = mde_from_sigma(570.0, 130, n_trials=1)
    many = mde_from_sigma(570.0, 130, n_trials=82)

    assert one is not None and many is not None
    assert many > one


def test_mde_from_sigma_needs_a_sample():
    assert mde_from_sigma(570.0, 1) is None
    assert mde_from_sigma(None, 130) is None
    assert mde_from_sigma(0.0, 130) is None


def test_clustered_mde_is_larger_than_the_iid_one():
    """Fewer clusters than events means a larger MDE: the honest number."""
    iid = mde_from_sigma(500.0, 129, n_trials=82)
    clustered = mde_from_sigma(500.0, 24, n_trials=82)

    assert iid is not None and clustered is not None
    assert clustered > iid
    assert clustered / iid == pytest.approx((129 / 24) ** 0.5, rel=1e-6)


# ── idea target (ADR-034) ──────────────────────────────────────────────────


def test_target_for_trades_matches_the_hand_calculation():
    """sigma 514, 22 trades a year, retention 0.6: 1.0*514/sqrt(22)/0.6 = 182 bps."""
    target = target_for_trades(
        sigma_bps=514.0,
        trades_per_year=22.1,
        sharpe_target=1.0,
        live_retention=0.6,
        cost_floor_bps=10.0,
    )

    assert target == pytest.approx(182.2, abs=0.5)


def test_target_for_trades_rises_when_the_idea_trades_less():
    few = target_for_trades(sigma_bps=500.0, trades_per_year=20)
    many = target_for_trades(sigma_bps=500.0, trades_per_year=280)

    assert few is not None and many is not None
    assert few > many


def test_target_for_trades_has_a_cost_floor():
    target = target_for_trades(
        sigma_bps=10.0, trades_per_year=10_000, cost_floor_bps=10.0
    )

    assert target == 10.0


def test_target_for_trades_needs_sigma_and_trades():
    assert target_for_trades(sigma_bps=None, trades_per_year=22) is None
    assert target_for_trades(sigma_bps=500.0, trades_per_year=0) is None


def test_target_for_trades_rejects_a_bad_retention():
    with pytest.raises(ValueError):
        target_for_trades(sigma_bps=500.0, trades_per_year=22, live_retention=0.0)


# ── report ─────────────────────────────────────────────────────────────────


def make_report(
    *, mde_cluster: float = 448.0, target_idea: float = 182.0, events: int = 129
) -> PowerReport:
    candles = series()
    scan = scan_event_windows(
        {"SBER": candles},
        [event(last_buy=bar_date(candles, 30))],
        window_sessions=10,
        sealed_start=SEALED,
    )
    sigma = unconditional_sigma({"SBER": candles}, 10)
    row = build_power_row(
        window_sessions=10,
        scan=scan,
        sigma=sigma,
        clusters=1,
        target_edge_bps=25.0,
        target_idea_bps=target_idea,
        trades_per_year=22.1,
        alpha=0.05,
        power=0.80,
        n_trials=1,
    )
    row = replace(row, events=events, mde_cluster_bps=mde_cluster)
    return PowerReport(
        label="H1 pre-ex run-up",
        timeframe="1d",
        tickers=("SBER",),
        total_events=scan.total_events,
        events=events,
        per_ticker=scan.per_ticker(),
        sigma_bps=sigma.sigma_bps,
        median_sigma_bps=median_sigma_bps(sigma),
        sigma_windows=sigma.windows,
        target_edge_bps=25.0,
        power=0.80,
        alpha=0.05,
        n_trials=1,
        grid_cells=1,
        main_window=10,
        sealed_start=SEALED,
        rows=(row,),
        missing_declared_date=events,
        evaluate_fingerprint="8+test",
        span_years=6.9,
        events_per_year=22.1,
    )


def test_report_says_it_is_not_a_verdict():
    text = render_power_report(make_report())

    assert "NOT A VERDICT" in text
    assert "No strategy ran" in text
    assert "journal is untouched" in text


def test_report_prints_no_mean_return():
    """A mean seen before the journal entry would bias the decision (ADR-033)."""
    text = render_power_report(make_report())
    table = text.split("## Power")[1].split("```")[1]

    assert "mean" not in table.lower()
    assert "excess" not in table.lower()


def test_report_marks_an_undetectable_target():
    text = render_power_report(make_report(mde_cluster=448.0, target_idea=182.0))

    assert "**Not measurable:**" in text
    assert "448 bps" in text and "182 bps" in text


def test_report_marks_a_detectable_target():
    text = render_power_report(make_report(mde_cluster=150.0, target_idea=182.0))

    assert "**Measurable:**" in text


def test_report_prints_the_eligibility_counters_and_caveats():
    text = render_power_report(make_report())

    assert "out of history" in text
    assert "declared after entry" in text
    assert "declaration missing" in text
    assert "not_paid" in text
    assert "cluster" in text


def test_measurable_uses_the_clustered_mde():
    assert make_report(mde_cluster=150.0, target_idea=182.0).measurable is True
    assert make_report(mde_cluster=448.0, target_idea=182.0).measurable is False


def test_report_prints_the_target_formula():
    text = render_power_report(make_report())

    assert "## Economic scale" in text
    assert "22.1 trades a year" in text
    assert "idea target = 1 * sigma / sqrt(trades a year) / 0.6" in text


# ── CLI parsing ────────────────────────────────────────────────────────────


def test_parse_windows_sorts_and_validates():
    assert parse_windows("15,5") == (15, 5)
    with pytest.raises(SystemExit):
        parse_windows("0")
    with pytest.raises(SystemExit):
        parse_windows(" , ")


