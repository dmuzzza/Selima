"""Portfolio run: fingerprints, independent runs, pooling, blocks, verdicts."""

import json
from datetime import datetime, timedelta

import pytest

from src.backtest.portfolio import (
    MultiInstrumentThresholds,
    PooledTrade,
    PortfolioConfig,
    PortfolioParameters,
    assign_blocks,
    block_bootstrap,
    build_report,
    classify_portfolio,
    gap_series,
    load_multi_instrument_thresholds,
    load_portfolio,
    mark_gap_affected,
    pool_trades,
    render_markdown,
    summarize,
    write_ticker_csv,
)
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

DAY = timedelta(days=1)
START = datetime(2024, 1, 1)


def make_trade(
    entry: datetime = START,
    pnl: float = 100.0,
    *,
    quantity: float = 10_000.0,
    hold_days: int = 1,
    open_trade: bool = False,
) -> Trade:
    return Trade(
        trade_id=1,
        entry_time=entry,
        entry_index=0,
        entry_price=100.0,
        signal_type="BULLISH",
        tp_level=101.0,
        sl_level=99.0,
        quantity=quantity,
        exit_time=None if open_trade else entry + hold_days * DAY,
        exit_index=None if open_trade else 1,
        exit_price=None if open_trade else 101.0,
        exit_type=None if open_trade else "TP",
        pnl_abs=None if open_trade else pnl,
        pnl_net=None if open_trade else pnl,
    )


def make_candles(closes: list[float], opens: list[float] | None = None) -> list[Candle]:
    candles = []
    for index, close in enumerate(closes):
        open_price = opens[index] if opens else close
        candles.append(
            Candle(
                open_time=START + index * DAY,
                open=open_price,
                high=max(open_price, close) * 1.01,
                low=min(open_price, close) * 0.99,
                close=close,
                volume=100,
            )
        )
    return candles


def pooled(ticker: str, pnl: float, entry: datetime = START) -> PooledTrade:
    return PooledTrade(ticker=ticker, trade=make_trade(entry, pnl))


THRESHOLDS = MultiInstrumentThresholds(min_total_trades=2, min_ticker_trades=2)


# ── configuration ──────────────────────────────────────────────────────────


def write_portfolio(path, **overrides):
    payload = {
        "version": "4",
        "strategy": "dual_ma_crossover_after_testing",
        "timeframe": "4h",
        "parameters": {
            "tp_percent": 2.5,
            "sl_percent": 2.0,
            "position_size_percent": 100.0,
            "allow_short": True,
        },
        "initial_balance": 100_000.0,
        "tickers": ["SBER", "GAZP"],
        **overrides,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_portfolio_reads_set_and_fingerprint(tmp_path):
    config = load_portfolio(write_portfolio(tmp_path / "portfolio.json"))

    assert config.tickers == ("SBER", "GAZP")
    assert config.parameters.tp_percent == 2.5
    assert config.parameters.sl_percent == 2.0
    assert config.initial_balance == 100_000.0
    assert config.fingerprint.startswith("4+")
    assert len(config.tickers_sha256) == 12
    assert len(config.params_sha256) == 12


def test_tickers_hash_does_not_depend_on_order(tmp_path):
    first = load_portfolio(write_portfolio(tmp_path / "a.json", tickers=["SBER", "GAZP"]))
    second = load_portfolio(write_portfolio(tmp_path / "b.json", tickers=["GAZP", "SBER"]))

    assert first.tickers_sha256 == second.tickers_sha256
    assert first.fingerprint != second.fingerprint  # the file itself differs


def test_params_hash_changes_with_parameters(tmp_path):
    first = load_portfolio(write_portfolio(tmp_path / "a.json"))
    second = load_portfolio(
        write_portfolio(
            tmp_path / "b.json",
            parameters={
                "tp_percent": 3.0,
                "sl_percent": 2.5,
                "position_size_percent": 100.0,
                "allow_short": True,
            },
        )
    )

    assert first.params_sha256 != second.params_sha256


def test_load_portfolio_rejects_duplicates_and_empty_set(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_portfolio(write_portfolio(tmp_path / "dup.json", tickers=["SBER", "SBER"]))
    with pytest.raises(ValueError, match="empty"):
        load_portfolio(write_portfolio(tmp_path / "empty.json", tickers=[]))


def test_load_portfolio_rejects_non_positive_levels(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        load_portfolio(
            write_portfolio(
                tmp_path / "bad.json",
                parameters={
                    "tp_percent": 0.0,
                    "sl_percent": 2.0,
                    "position_size_percent": 100.0,
                    "allow_short": True,
                },
            )
        )


def test_load_thresholds_reads_multi_instrument_section(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(
        json.dumps(
            {
                "version": "9",
                "multi_instrument": {
                    "min_total_trades": 200,
                    "min_ticker_trades": 30,
                    "block_days": 30,
                    "bootstrap_iterations": 100,
                    "alpha": 0.05,
                    "seed": 7,
                    "gap_threshold_percent": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    thresholds = load_multi_instrument_thresholds(path)

    assert thresholds.min_total_trades == 200
    assert thresholds.min_ticker_trades == 30
    assert thresholds.block_days == 30
    assert thresholds.gap_threshold_percent == 5.0
    assert thresholds.fingerprint.startswith("9+")


def test_load_thresholds_requires_section(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps({"version": "1", "heatmap": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="multi_instrument"):
        load_multi_instrument_thresholds(path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_total_trades": 0}, "trade thresholds"),
        ({"block_days": 0}, "block_days"),
        ({"bootstrap_iterations": 0}, "bootstrap_iterations"),
        ({"alpha": 1.0}, "alpha"),
        ({"gap_threshold_percent": 0.0}, "gap_threshold"),
    ],
)
def test_thresholds_reject_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        MultiInstrumentThresholds(**kwargs)


# ── gaps ───────────────────────────────────────────────────────────────────


def test_gap_series_measures_open_against_previous_close():
    candles = make_candles([100.0, 110.0, 100.0], opens=[100.0, 110.0, 100.0])

    gaps = gap_series(candles)

    assert gaps[START + DAY] == pytest.approx(10.0)
    assert gaps[START + 2 * DAY] == pytest.approx(-9.0909, rel=1e-3)


def test_mark_gap_affected_flags_only_trades_spanning_a_gap():
    candles = make_candles([100.0, 100.0, 100.0], opens=[100.0, 110.0, 100.0])
    gaps = gap_series(candles)
    spanning = make_trade(START, 100.0, hold_days=2)
    before = make_trade(START + 3 * DAY, 100.0, hold_days=1)

    flags = mark_gap_affected([spanning, before], gaps, threshold_percent=5.0)

    assert flags == [True, False]


def test_gap_below_threshold_is_not_flagged():
    candles = make_candles([100.0, 100.0], opens=[100.0, 103.0])
    gaps = gap_series(candles)

    flags = mark_gap_affected(
        [make_trade(START, 100.0, hold_days=1)], gaps, threshold_percent=5.0
    )

    assert flags == [False]


def test_gap_marking_requires_positive_threshold():
    with pytest.raises(ValueError, match="threshold_percent"):
        mark_gap_affected([], {}, threshold_percent=0.0)


# ── pooling ────────────────────────────────────────────────────────────────


def test_pool_keeps_ticker_and_drops_open_trades():
    runs = {
        "SBER": [make_trade(pnl=100.0), make_trade(open_trade=True)],
        "GAZP": [make_trade(pnl=-50.0)],
    }

    items = pool_trades(runs, {}, gap_threshold_percent=5.0)

    assert [item.ticker for item in items] == ["SBER", "GAZP"]
    assert all(not item.trade.is_open() for item in items)


def test_pool_marks_gap_affected_trades():
    candles = make_candles([100.0, 100.0, 100.0], opens=[100.0, 110.0, 100.0])
    runs = {"SBER": [make_trade(START, 100.0, hold_days=2)]}

    items = pool_trades(runs, {"SBER": gap_series(candles)}, gap_threshold_percent=5.0)

    assert items[0].gap_affected is True


def test_pool_bps_is_per_trade_ratio():
    items = [PooledTrade("SBER", make_trade(pnl=100.0)), PooledTrade("GAZP", make_trade(pnl=-50.0))]

    assert items[0].bps == pytest.approx(100.0)
    assert items[1].bps == pytest.approx(-50.0)


def test_zero_quantity_gives_zero_bps():
    item = PooledTrade("SBER", make_trade(pnl=100.0, quantity=0.0))

    assert item.bps == 0.0


# ── summaries ──────────────────────────────────────────────────────────────


def test_summary_pool_totals_and_contributions():
    items = [
        pooled("SBER", 300.0),
        pooled("SBER", 100.0),
        pooled("GAZP", -100.0),
        pooled("GAZP", -100.0),
    ]

    summary = summarize(items, THRESHOLDS)

    assert summary.trades == 4
    assert summary.net_rub == pytest.approx(200.0)
    assert summary.net_bps == pytest.approx(50.0)
    assert summary.tickers_with_trades == 2
    assert summary.tickers_profitable == 1
    assert summary.share_profitable == pytest.approx(0.5)
    assert summary.best_ticker == "SBER"
    assert summary.best_contribution_percent == pytest.approx(200.0)


def test_thin_tickers_stay_in_totals_but_not_in_share():
    items = [
        pooled("SBER", 300.0),
        pooled("SBER", 100.0),
        pooled("GAZP", 50.0),
    ]

    summary = summarize(items, MultiInstrumentThresholds(min_total_trades=1, min_ticker_trades=2))

    assert summary.trades == 3
    assert summary.thin_tickers == ("GAZP",)
    assert summary.tickers_above_min == 1
    assert summary.share_profitable == pytest.approx(1.0)


def test_contribution_is_none_when_total_is_zero():
    items = [pooled("SBER", 100.0), pooled("GAZP", -100.0)]

    summary = summarize(items, THRESHOLDS)

    assert summary.net_rub == pytest.approx(0.0)
    assert all(ticker.contribution_percent is None for ticker in summary.per_ticker)


def test_gap_affected_totals_are_reported():
    items = [
        PooledTrade("SBER", make_trade(pnl=100.0), gap_affected=True),
        PooledTrade("GAZP", make_trade(pnl=-100.0)),
    ]

    summary = summarize(items, THRESHOLDS)

    assert summary.gap_affected_trades == 1
    assert summary.gap_affected_net_rub == pytest.approx(100.0)
    assert summary.gap_affected_share == pytest.approx(0.5)


def test_empty_pool_gives_zero_summary():
    summary = summarize([], THRESHOLDS)

    assert summary.trades == 0
    assert summary.net_bps == 0.0
    assert summary.best_ticker is None
    assert summary.share_profitable is None


# ── blocks and bootstrap ───────────────────────────────────────────────────


def test_blocks_include_every_ticker_of_a_period():
    items = [
        pooled("SBER", 100.0, START),
        pooled("GAZP", 50.0, START + DAY),
        pooled("SBER", -100.0, START + 45 * DAY),
    ]

    blocks = assign_blocks(items, block_days=30)

    assert len(blocks) == 2
    assert {item.ticker for item in blocks[0]} == {"SBER", "GAZP"}
    assert len(blocks[1]) == 1


def test_empty_blocks_are_kept():
    items = [pooled("SBER", 100.0, START), pooled("SBER", 100.0, START + 95 * DAY)]

    blocks = assign_blocks(items, block_days=30)

    # 0, 1, 2, 3 -> two empty blocks between the trades.
    assert len(blocks) == 4
    assert sum(1 for block in blocks if not block) == 2


def test_assign_blocks_rejects_bad_block_length():
    with pytest.raises(ValueError, match="block_days"):
        assign_blocks([pooled("SBER", 1.0)], block_days=0)


def test_bootstrap_is_deterministic_for_a_seed():
    items = [pooled("SBER", 100.0, START + i * 10 * DAY) for i in range(12)]

    first = block_bootstrap(items, block_days=30, iterations=200, seed=3)
    second = block_bootstrap(items, block_days=30, iterations=200, seed=3)

    assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)


def test_bootstrap_interval_contains_the_point_estimate():
    items = [
        PooledTrade("SBER", make_trade(START + i * 10 * DAY, 100.0 if i % 2 else 20.0))
        for i in range(12)
    ]

    result = block_bootstrap(items, block_days=30, iterations=500, seed=1)

    assert result.ci_low <= result.net_bps <= result.ci_high
    assert result.blocks == len(assign_blocks(items, block_days=30))
    assert result.trades == 12


def test_bootstrap_of_constant_result_has_zero_width():
    items = [pooled("SBER", 100.0, START + i * 10 * DAY) for i in range(10)]

    result = block_bootstrap(items, block_days=30, iterations=200, seed=1)

    assert result.ci_low == pytest.approx(100.0)
    assert result.ci_high == pytest.approx(100.0)


def test_bootstrap_on_empty_pool_is_safe():
    result = block_bootstrap([], block_days=30, iterations=10)

    assert result.blocks == 0
    assert result.trades == 0
    assert (result.ci_low, result.ci_high) == (0.0, 0.0)


def test_bootstrap_skips_iterations_without_trades():
    items = [pooled("SBER", 100.0, START)]
    blocks = assign_blocks(items, block_days=1)
    items = items + [pooled("SBER", 0.0, START + 1 * DAY)] if len(blocks) > 1 else items

    result = block_bootstrap(items, block_days=30, iterations=50, seed=1)

    assert result.empty_iterations >= 0
    assert result.iterations == 50


# ── verdict ────────────────────────────────────────────────────────────────


def test_insufficient_trades_wins_over_the_interval():
    items = [pooled("SBER", 100.0)]
    summary = summarize(items, MultiInstrumentThresholds(min_total_trades=200))
    bootstrap = block_bootstrap(items, block_days=30, iterations=100)

    assert classify_portfolio(summary, bootstrap, MultiInstrumentThresholds(min_total_trades=200)) == (
        "insufficient_trades"
    )


def test_positive_when_interval_is_above_zero():
    items = [pooled("SBER", 100.0, START + i * 10 * DAY) for i in range(10)]
    summary = summarize(items, THRESHOLDS)
    bootstrap = block_bootstrap(items, block_days=30, iterations=200, seed=1)

    assert classify_portfolio(summary, bootstrap, THRESHOLDS) == "positive"


def test_negative_when_interval_is_below_zero():
    items = [pooled("SBER", -100.0, START + i * 10 * DAY) for i in range(10)]
    summary = summarize(items, THRESHOLDS)
    bootstrap = block_bootstrap(items, block_days=30, iterations=200, seed=1)

    assert classify_portfolio(summary, bootstrap, THRESHOLDS) == "negative"


# ── report ─────────────────────────────────────────────────────────────────


def build_config() -> PortfolioConfig:
    return PortfolioConfig(
        version="1",
        strategy="dual_ma_crossover_after_testing",
        timeframe="4h",
        parameters=PortfolioParameters(
            tp_percent=2.5, sl_percent=2.0, position_size_percent=100.0, allow_short=True
        ),
        initial_balance=100_000.0,
        tickers=("SBER", "GAZP"),
        fingerprint="1+abcdef123456",
        tickers_sha256="tick12345678",
        params_sha256="para12345678",
    )


def test_report_evaluates_three_variants():
    items = [
        PooledTrade("SBER", make_trade(START + i * 10 * DAY, 100.0)) for i in range(10)
    ] + [PooledTrade("GAZP", make_trade(START, -50.0), gap_affected=True)]

    report = build_report(items, config=build_config(), thresholds=THRESHOLDS)

    assert report.summary.trades == 11
    assert report.without_gaps_summary.trades == 10
    assert report.without_thin_summary.trades == 10
    assert report.verdict in {"positive", "negative", "inconclusive"}
    payload = report.describe()
    assert payload["portfolio_version"] == "1+abcdef123456"
    assert payload["tickers_sha256"] == "tick12345678"
    assert payload["without_gap_affected"]["summary"]["trades"] == 10


def test_markdown_prints_fingerprints_thresholds_and_gap_note():
    items = [pooled("SBER", 100.0, START + i * 10 * DAY) for i in range(10)]

    report = build_report(items, config=build_config(), thresholds=THRESHOLDS)
    text = render_markdown(report)

    assert "portfolio_version" in text
    assert "tickers_sha256" in text
    assert "validation_version" in text
    assert "min_total_trades" in text
    assert "gap-affected" in text
    assert "survivorship" in text
    assert "| SBER |" in text


def test_snapshot_meta_goes_to_json_only():
    items = [pooled("SBER", 100.0, START + i * 10 * DAY) for i in range(10)]

    report = build_report(
        items,
        config=build_config(),
        thresholds=THRESHOLDS,
        extra_meta={"costs_version": "3+abcdef123456", "tp_sl_version": "n/a"},
        snapshot_meta={"costs_per_ticker": {"SBER": {"spread_bps": 2.0}}},
    )
    text = render_markdown(report)
    payload = report.describe()

    assert "costs_version" in text and "3+abcdef123456" in text
    assert "tp_sl_version" in text
    # Raw maps stay out of Markdown but must be in the snapshot.
    assert "costs_per_ticker" not in text
    assert payload["costs_per_ticker"] == {"SBER": {"spread_bps": 2.0}}


def test_ticker_csv_has_one_row_per_ticker(tmp_path):
    items = [pooled("SBER", 100.0), pooled("GAZP", -50.0)]
    summary = summarize(items, THRESHOLDS)

    path = write_ticker_csv(tmp_path / "tickers.csv", summary)

    rows = path.read_text(encoding="utf-8").strip().splitlines()
    assert rows[0].startswith("ticker,trades,net_rub")
    assert len(rows) == 3
