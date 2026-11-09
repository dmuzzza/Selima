"""Event-study power: window placement, eligibility filters, unconditional sigma.

The pre-check exists to refuse a journal slot before the strategy is written, so
the tests pin the two things that can make it lie: a window that is not the one
the idea would trade, and an eligibility filter that silently drops events.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from scripts.evaluate import parse_windows
from src.backtest.dividends import DividendEvent
from src.backtest.evaluate import (
    IccReport,
    PowerReport,
    build_power_row,
    icc_ceiling,
    icc_scenarios,
    mde_from_sigma,
    project_power,
    render_power_report,
    target_for_trades,
)
from src.backtest.event_study import (
    CLUSTER_BY_MONTH,
    CLUSTER_BY_QUARTER,
    CLUSTER_BY_YEAR,
    PLACEBO_STATUS,
    ClusterLevel,
    IccEstimate,
    basket_index,
    basket_totals,
    choose_cluster_level,
    cluster_count,
    cluster_design_effect,
    cluster_groups,
    cluster_key,
    cluster_means,
    icc_bootstrap_ci,
    icc_oneway,
    lag1_autocorrelation,
    load_power_universe,
    median_sigma_bps,
    placebo_windows,
    rolling_returns_bps,
    scan_event_windows,
    standard_error_bps,
    unconditional_excess_sigma,
    unconditional_sigma,
    universe_sha256,
    window_excess_bps,
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


def test_excess_window_return_removes_the_basket_move():
    """Two identical series: the basket moves with the ticker, nothing is left."""
    same = series(length=40)
    totals = basket_totals({"AAA": same, "BBB": same})
    window = one_window(same, index=20, ticker="AAA")

    assert window_excess_bps(same, window, totals) == pytest.approx(0.0, abs=1e-9)
    assert window_return_bps(same, window) != pytest.approx(0.0, abs=1e-9)


# ── placebo windows ────────────────────────────────────────────────────────


def one_window(candles: list[Candle], index: int = 150, ticker: str = "SBER"):
    scan = scan_event_windows(
        {ticker: candles},
        [event(last_buy=bar_date(candles, index), ticker=ticker)],
        window_sessions=10,
        sealed_start=SEALED,
    )
    assert scan.events == 1
    return scan.windows[0]


def test_placebo_window_is_shifted_off_the_real_date():
    candles = series(length=200)
    real = one_window(candles)

    placebo = placebo_windows(
        {"SBER": candles},
        [real],
        window_sessions=10,
        sealed_start=SEALED,
        min_shift=20,
        seed=7,
    )

    assert placebo.events == 1
    window = placebo.windows[0]
    assert abs(window.exit_index - real.exit_index) >= 20
    assert window.entry_index == window.exit_index - 10
    assert window.status == PLACEBO_STATUS


def test_placebo_window_never_overlaps_the_real_window():
    """|shift| >= 20 with a 10-session window leaves a gap in both directions."""
    candles = series(length=200)
    real = one_window(candles)

    for seed in range(20):
        placebo = placebo_windows(
            {"SBER": candles},
            [real],
            window_sessions=10,
            sealed_start=SEALED,
            min_shift=20,
            seed=seed,
        )
        window = placebo.windows[0]
        assert window.exit_index <= real.entry_index - 10 or window.entry_index >= real.exit_index + 10


def test_placebo_is_deterministic_for_a_seed_and_moves_for_another():
    candles = series(length=400)
    real = one_window(candles, index=200)

    first = placebo_windows(
        {"SBER": candles},
        [real],
        window_sessions=10,
        sealed_start=SEALED,
        min_shift=20,
        seed=11,
    )
    again = placebo_windows(
        {"SBER": candles},
        [real],
        window_sessions=10,
        sealed_start=SEALED,
        min_shift=20,
        seed=11,
    )
    other = placebo_windows(
        {"SBER": candles},
        [real],
        window_sessions=10,
        sealed_start=SEALED,
        min_shift=20,
        seed=12,
    )

    assert first.windows[0].exit_index == again.windows[0].exit_index
    assert first.windows[0].exit_index != other.windows[0].exit_index


def test_placebo_keeps_the_window_inside_history_and_before_the_sealed_start():
    candles = series(length=120)
    real = one_window(candles, index=60)
    cutoff = bar_date(candles, 100)

    for seed in range(20):
        placebo = placebo_windows(
            {"SBER": candles},
            [real],
            window_sessions=10,
            sealed_start=cutoff,
            min_shift=20,
            seed=seed,
        )
        window = placebo.windows[0]
        assert 0 <= window.entry_index < window.exit_index < len(candles)
        assert window.exit_time.date() < cutoff


def test_placebo_counts_a_window_it_cannot_place():
    candles = series(length=30)
    real = one_window(candles, index=10)

    placebo = placebo_windows(
        {"SBER": candles},
        [real],
        window_sessions=10,
        sealed_start=SEALED,
        min_shift=20,
        seed=3,
    )

    assert placebo.events == 0
    assert placebo.skipped == 1


def test_placebo_rejects_a_bad_window_length():
    with pytest.raises(ValueError):
        placebo_windows(
            {"SBER": series()},
            [],
            window_sessions=0,
            sealed_start=SEALED,
        )


# ── intra-cluster correlation ──────────────────────────────────────────────


def synthetic_clusters(
    icc: float, *, clusters: int = 40, size: int = 10, seed: int = 3
) -> list[list[float]]:
    """One-way random effects: y = a_i + e_ij, ICC = tau^2 / (tau^2 + sigma^2)."""
    rng = random.Random(seed)
    between = math.sqrt(icc)
    within = math.sqrt(1.0 - icc)
    groups: list[list[float]] = []
    for _ in range(clusters):
        effect = rng.gauss(0.0, between)
        groups.append([effect + rng.gauss(0.0, within) for _ in range(size)])
    return groups


def test_icc_recovers_a_known_intra_cluster_correlation():
    estimate = icc_oneway(synthetic_clusters(0.25))

    assert estimate.icc == pytest.approx(0.25, abs=0.10)
    assert estimate.clusters == 40
    assert estimate.observations == 400


def test_icc_is_near_zero_for_independent_values():
    estimate = icc_oneway(synthetic_clusters(0.0))

    assert estimate.icc is not None
    assert abs(estimate.icc) < 0.10


def test_icc_uses_the_unbalanced_cluster_correction():
    """Hand calculation with m0, not the balanced-design formula."""
    groups = [[0.0, 2.0], [10.0, 12.0, 14.0]]

    estimate = icc_oneway(groups)

    assert estimate.clusters == 2
    assert estimate.observations == 5
    assert estimate.mean_cluster_size == pytest.approx(2.5)
    assert estimate.icc == pytest.approx(0.9466, abs=1e-3)


def test_icc_needs_two_clusters_and_within_variance():
    assert icc_oneway([[1.0, 2.0]]).icc is None
    assert icc_oneway([[1.0], [2.0]]).icc is None


def test_icc_interval_excludes_zero_for_clustered_values():
    low, high = icc_bootstrap_ci(synthetic_clusters(0.4), samples=300, seed=5)

    assert low is not None and high is not None
    assert low > 0.0
    assert low < high


def test_icc_interval_includes_zero_for_independent_values():
    low, _ = icc_bootstrap_ci(synthetic_clusters(0.0), samples=300, seed=5)

    assert low is not None
    assert low <= 0.0


def test_icc_bootstrap_rejects_a_tiny_sample_count():
    with pytest.raises(ValueError):
        icc_bootstrap_ci(synthetic_clusters(0.2), samples=10)


def test_cluster_means_are_chronological_and_lag1_detects_a_trend():
    values = [1.0, 2.0, 3.0, 4.0]

    assert cluster_means([[1.0], [2.0], [3.0], [4.0]]) == [1.0, 2.0, 3.0, 4.0]
    assert lag1_autocorrelation(values) == pytest.approx(1.0)
    assert lag1_autocorrelation([1.0, -1.0, 1.0, -1.0]) == pytest.approx(-1.0)
    assert lag1_autocorrelation([1.0, 2.0]) is None


def test_cluster_groups_keep_the_season_order():
    candles = series(length=400)
    windows = []
    values = []
    for index in (60, 200, 320):
        window = one_window(candles, index=index)
        windows.append(window)
        values.append(float(index))

    groups = cluster_groups(windows, values, CLUSTER_BY_QUARTER)

    assert len(groups) == 3
    assert groups[0] == [60.0]


def level(by: str, *, icc: float, low: float, high: float, lag1: float | None) -> ClusterLevel:
    return ClusterLevel(
        by=by,
        icc=IccEstimate(
            icc=icc,
            clusters=10,
            observations=100,
            mean_cluster_size=10.0,
            ci_low=low,
            ci_high=high,
        ),
        lag1=lag1,
    )


def test_gate_uses_the_coarsest_significant_level():
    decision = choose_cluster_level(
        [
            level(CLUSTER_BY_MONTH, icc=0.2, low=0.05, high=0.35, lag1=0.1),
            level(CLUSTER_BY_QUARTER, icc=0.3, low=0.10, high=0.50, lag1=0.1),
            level(CLUSTER_BY_YEAR, icc=0.4, low=-0.10, high=0.70, lag1=0.1),
        ]
    )

    assert decision.level == CLUSTER_BY_QUARTER
    assert decision.merged is False


def test_gate_falls_back_to_the_finest_level_without_significance():
    decision = choose_cluster_level(
        [
            level(CLUSTER_BY_MONTH, icc=0.05, low=-0.05, high=0.15, lag1=0.1),
            level(CLUSTER_BY_QUARTER, icc=0.10, low=-0.10, high=0.30, lag1=0.1),
            level(CLUSTER_BY_YEAR, icc=0.20, low=-0.30, high=0.60, lag1=0.1),
        ]
    )

    assert decision.level == CLUSTER_BY_MONTH


def test_positive_adjacent_correlation_moves_the_gate_one_level_coarser():
    """A shock that outlives a month is not removed by a monthly bucket."""
    decision = choose_cluster_level(
        [
            level(CLUSTER_BY_MONTH, icc=0.25, low=0.05, high=0.45, lag1=0.45),
            level(CLUSTER_BY_QUARTER, icc=0.10, low=-0.05, high=0.25, lag1=0.1),
            level(CLUSTER_BY_YEAR, icc=0.20, low=-0.20, high=0.60, lag1=0.1),
        ]
    )

    assert decision.level == CLUSTER_BY_QUARTER
    assert decision.merged is True


def test_merge_rule_walks_from_quarter_to_year():
    decision = choose_cluster_level(
        [
            level(CLUSTER_BY_MONTH, icc=0.25, low=-0.05, high=0.45, lag1=0.1),
            level(CLUSTER_BY_QUARTER, icc=0.30, low=0.10, high=0.50, lag1=0.40),
            level(CLUSTER_BY_YEAR, icc=0.20, low=-0.20, high=0.60, lag1=0.1),
        ]
    )

    assert decision.level == CLUSTER_BY_YEAR
    assert decision.merged is True


def test_merge_stops_at_the_coarsest_level():
    decision = choose_cluster_level(
        [
            level(CLUSTER_BY_MONTH, icc=0.1, low=-0.1, high=0.3, lag1=0.1),
            level(CLUSTER_BY_QUARTER, icc=0.1, low=-0.1, high=0.3, lag1=0.1),
            level(CLUSTER_BY_YEAR, icc=0.4, low=0.10, high=0.70, lag1=0.9),
        ]
    )

    assert decision.level == CLUSTER_BY_YEAR


# ── projection ─────────────────────────────────────────────────────────────


def projection(
    *,
    sigma_bps: float | None = 514.0,
    events_per_ticker: float | None = 6.45,
    clusters: int = 24,
    icc: float | None = 0.3,
    ticker_counts: tuple[int, ...] = (40, 60, 100),
    cap: int | None = None,
    target_idea_bps: float | None = 182.0,
):
    return project_power(
        sigma_bps=sigma_bps,
        events_per_ticker=events_per_ticker,
        clusters=clusters,
        icc=icc,
        ticker_counts=ticker_counts,
        cap=cap,
        target_idea_bps=target_idea_bps,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )


def test_projection_falls_as_the_universe_grows():
    rows = projection()

    assert [row.tickers for row in rows] == [40, 60, 100]
    assert rows[0].mde_bps > rows[1].mde_bps > rows[2].mde_bps


def test_projection_saturates_at_the_season_count():
    """With a fixed cluster count the effective sample tends to clusters / ICC."""
    rows = projection(icc=0.5)

    assert rows[-1].effective_events < 24 / 0.5 * 1.05
    assert rows[-1].design_effect > rows[0].design_effect


def test_projection_is_clipped_by_the_ticker_cap():
    rows = projection(cap=60)

    assert [row.tickers for row in rows] == [40, 60]
    assert rows[-1].capped is True


def test_projection_without_dependence_matches_the_event_count():
    rows = projection(icc=0.0)

    assert rows[0].effective_events == pytest.approx(rows[0].events)


def test_projection_needs_a_sample():
    assert projection(sigma_bps=None) == ()
    assert projection(events_per_ticker=None) == ()


def test_projection_marks_a_target_it_can_reach():
    rows = projection(icc=0.0, target_idea_bps=10_000.0)

    assert all(row.below_target for row in rows)


# ── ICC sensitivity and ceiling ────────────────────────────────────────────


def test_icc_ceiling_matches_the_hand_calculation():
    """MDE = z*sigma*sqrt(1+(m-1)*ICC)/sqrt(n); solve for ICC at MDE == target."""
    events, clusters = 490.0, 24
    sigma, target = 514.4, 182.3
    z_total = 4.27  # z(1 - 0.05/82/2) + z(0.8), the values the run uses

    ceiling = icc_ceiling(
        sigma_bps=sigma,
        events=events,
        clusters=clusters,
        target_idea_bps=target,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )

    assert ceiling is not None
    expected = ((target * math.sqrt(events) / (z_total * sigma)) ** 2 - 1) / (
        events / clusters - 1
    )
    assert ceiling == pytest.approx(expected, rel=0.02)


def test_icc_ceiling_is_negative_when_the_target_is_missed_without_dependence():
    ceiling = icc_ceiling(
        sigma_bps=514.0,
        events=100.0,
        clusters=24,
        target_idea_bps=182.0,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )

    assert ceiling is not None and ceiling < 0


def test_icc_ceiling_needs_a_sample():
    assert (
        icc_ceiling(
            sigma_bps=None,
            events=490.0,
            clusters=24,
            target_idea_bps=182.0,
            alpha=0.05,
            power=0.80,
            n_trials=1,
        )
        is None
    )


def test_icc_scenarios_prints_zero_point_and_upper_bound():
    clustered = ClusterLevel(
        by=CLUSTER_BY_QUARTER,
        icc=IccEstimate(
            icc=0.08,
            clusters=24,
            observations=129,
            mean_cluster_size=5.4,
            ci_low=-0.07,
            ci_high=0.19,
        ),
    )

    rows = icc_scenarios(
        level=clustered,
        tickers=76,
        events_per_ticker=6.45,
        sigma_bps=514.0,
        target_idea_bps=182.0,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )

    assert [row.icc_label for row in rows] == ["zero", "point", "ci_high"]
    assert rows[0].icc == 0.0
    zero, point, high = rows[0].mde_bps, rows[1].mde_bps, rows[2].mde_bps
    assert zero is not None and point is not None and high is not None
    assert zero < point < high


def test_icc_scenarios_grows_the_design_effect_with_the_universe():
    clustered = ClusterLevel(
        by=CLUSTER_BY_QUARTER,
        icc=IccEstimate(
            icc=0.19, clusters=24, observations=129, mean_cluster_size=5.4, ci_high=0.19
        ),
    )

    small = icc_scenarios(
        level=clustered,
        tickers=20,
        events_per_ticker=6.45,
        sigma_bps=514.0,
        target_idea_bps=182.0,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )
    large = icc_scenarios(
        level=clustered,
        tickers=76,
        events_per_ticker=6.45,
        sigma_bps=514.0,
        target_idea_bps=182.0,
        alpha=0.05,
        power=0.80,
        n_trials=82,
    )

    # The design effect grows with the universe, but the event count grows faster:
    # the MDE still falls, just less than it would at ICC 0.
    assert large[-1].design_effect > small[-1].design_effect
    assert large[-1].effective_events > small[-1].effective_events
    large_mde, small_mde = large[-1].mde_bps, small[-1].mde_bps
    assert large_mde is not None and small_mde is not None
    assert large_mde < small_mde


# ── frozen event universe ──────────────────────────────────────────────────


def test_universe_hash_ignores_order_case_and_duplicates():
    first = universe_sha256(["SBER", "GAZP"])
    second = universe_sha256(["gazp", " SBER ", "SBER"])

    assert first == second
    assert first != universe_sha256(["SBER"])


def test_universe_loader_refuses_an_edited_list(tmp_path):
    path = tmp_path / "universe.json"
    path.write_text(
        json.dumps(
            {
                "version": "1",
                "rule": "liquid payers",
                "tickers": ["SBER", "GAZP"],
                "sha256": universe_sha256(["SBER", "GAZP"]),
            }
        ),
        encoding="utf-8",
    )

    universe = load_power_universe(path)
    assert universe.tickers == ("SBER", "GAZP")

    path.write_text(
        json.dumps(
            {
                "version": "1",
                "rule": "liquid payers",
                "tickers": ["SBER", "GAZP", "LKOH"],
                "sha256": universe_sha256(["SBER", "GAZP"]),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="new attempt"):
        load_power_universe(path)


def test_universe_loader_requires_a_hash(tmp_path):
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({"tickers": ["SBER"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        load_power_universe(path)


# ── report with the ICC section ────────────────────────────────────────────


def make_icc_report(gate_mde: float = 448.0, target: float = 182.0) -> IccReport:
    levels = (
        level(CLUSTER_BY_MONTH, icc=0.2, low=0.05, high=0.35, lag1=0.1),
        level(CLUSTER_BY_QUARTER, icc=0.3, low=0.10, high=0.50, lag1=0.1),
        level(CLUSTER_BY_YEAR, icc=0.4, low=-0.10, high=0.70, lag1=0.1),
    )
    return IccReport(
        seed=20261109,
        min_shift=20,
        placebo_events=129,
        placebo_skipped=0,
        levels=levels,
        decision=choose_cluster_level(levels),
        gate_mde_bps=gate_mde,
        gate_measurable=gate_mde <= target,
        target_idea_bps=target,
        gate_tickers=60,
        current_mde_bps=gate_mde + 40.0,
        current_tickers=20,
        projection=projection(),
        ticker_cap=60,
        ticker_cap_source="set in config/validation.json",
        available_now=36,
    )


def test_report_prints_the_icc_section_and_the_gate():
    report = make_report()
    report = replace(report, icc=make_icc_report())
    text = render_power_report(report)

    assert "## ICC on placebo windows" in text
    assert "Gate level: quarter" in text
    assert "## Projection" in text
    assert "Ticker cap 60" in text
    assert "448 bps" in text and "182 bps" in text


def test_report_uses_the_gate_mde_not_the_cluster_count():
    report = replace(make_report(mde_cluster=100.0), icc=make_icc_report(gate_mde=448.0))

    assert report.measurable is False


def test_icc_section_prints_no_mean_return():
    report = replace(make_report(), icc=make_icc_report())
    text = render_power_report(report)
    section = text.split("## ICC on placebo windows")[1].split("## Economic scale")[0]
    table = section.split("```")[1]

    assert "+99" not in section and "+185" not in section
    assert "bps" not in table
    assert "mean" not in table.lower()



