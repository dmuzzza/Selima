"""Single verdict report over the frozen portfolio set.

Collects every check the project has and turns them into one state:
`works`, `fragile`, `inconclusive` or `rejected`. Gates and their order live in
`src/backtest/verdict.py`; this script only supplies the inputs:

- pool on the full history (block bootstrap, T2-20);
- holdout: a frozen calendar window from `config/validation.json` that no
  parameter was fitted on (T2-21);
- leave-one-out: the pool without its strongest ticker (T2-20a);
- doubled costs and TP penalty runs (sensitivity gates C3, C5);
- heatmap around the chosen TP/SL on the same 20-ticker pool, fit window only
  (gate C2) — a plateau on one ticker says nothing about the rule;
- null test on synthetic data with the same TP/SL (gate A1/B5).

    uv run python scripts/verdict.py --tag t2-21
    uv run python scripts/verdict.py --tag t2-21 --n-trials 12

Outputs Markdown and JSON to debug/ (git-ignored). No new dependencies.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from config.commissions import describe_commissions  # noqa: E402
from src.backtest.costs import (  # noqa: E402
    BpsCosts,
    CostConfig,
    CostModel,
    load_costs,
)
from src.backtest.dividends import (  # noqa: E402
    applied_ex_bars,
    build_coverage,
    load_dividend_thresholds,
    load_dividends,
    resolve_events,
    years_from_candles,
)
from src.backtest.heatmap import (  # noqa: E402
    CellStats,
    analyze_plateau_around,
    score_cell,
)
from src.backtest.heatmap import (  # noqa: E402
    load_thresholds as load_heatmap_thresholds,
)
from src.backtest.null_test import null_test, synthetic_candles  # noqa: E402
from src.backtest.portfolio import (  # noqa: E402
    WITHOUT_BEST_TICKER,
    PooledTrade,
    block_bootstrap,
    gap_series,
    leave_one_out_variants,
    load_multi_instrument_thresholds,
    load_portfolio,
    pool_trades,
    summarize,
)
from src.backtest.verdict import (  # noqa: E402
    IntervalResult,
    VerdictInputs,
    evaluate_verdict,
    load_verdict_thresholds,
    render_markdown,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.models import Candle  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("verdict")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
PORTFOLIO_PATH = ROOT / "config" / "portfolio.json"
THRESHOLDS_PATH = ROOT / "config" / "validation.json"
COSTS_PATH = ROOT / "config" / "costs.json"


def load_instruments(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {i["ticker"].upper(): i for i in data.get("instruments", [])}


def build_runner(strategy_code: str, timeframe: str, ticker: str, costs: CostModel, config):
    """Fresh strategy per run: fixed parameters, own balance, no shared state."""

    def runner(candles: list[Candle]) -> list[Trade]:
        strategy = StrategyFactory.create(
            strategy_code,
            timeframe,
            config={
                "ticker": ticker,
                "costs": costs,
                "initial_balance": config.initial_balance,
                **config.parameters.describe(),
            },
        )
        return strategy.run(candles)["trades"]

    return runner


def slice_window(
    candles: list[Candle],
    start: datetime | None,
    end: datetime | None,
    warmup_bars: int,
) -> list[Candle]:
    """Candles in [start, end) plus `warmup_bars` candles before `start`."""
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    if start is None and end is None:
        return ordered
    indices = [
        index
        for index, candle in enumerate(ordered)
        if (start is None or candle.open_time >= start)
        and (end is None or candle.open_time < end)
    ]
    if not indices:
        return []
    from_index = max(0, indices[0] - warmup_bars) if start is not None else indices[0]
    return ordered[from_index : indices[-1] + 1]


def run_pool(
    candles_by_ticker: dict[str, list[Candle]],
    *,
    strategy: str,
    timeframe: str,
    config,
    costs_for,
    thresholds,
    trade_start: datetime | None = None,
    trade_end: datetime | None = None,
    ex_bars_by_ticker: dict[str, tuple] | None = None,
) -> list[PooledTrade]:
    """Run every ticker and pool closed trades, optionally filtered by entry time."""
    runs: dict[str, list[Trade]] = {}
    gaps: dict[str, dict] = {}
    for ticker, candles in candles_by_ticker.items():
        if not candles:
            continue
        trades = build_runner(strategy, timeframe, ticker, costs_for(ticker), config)(candles)
        if trade_start is not None:
            trades = [trade for trade in trades if trade.entry_time >= trade_start]
        if trade_end is not None:
            trades = [trade for trade in trades if trade.entry_time < trade_end]
        runs[ticker] = trades
        gaps[ticker] = gap_series(candles)
    return pool_trades(
        runs,
        gaps,
        gap_threshold_percent=thresholds.gap_threshold_percent,
        ex_bars_by_ticker=ex_bars_by_ticker,
    )


def interval(pooled: list[PooledTrade], thresholds) -> IntervalResult:
    """Pooled expectancy with its block-bootstrap interval."""
    if not pooled:
        return IntervalResult(trades=0, net_bps=0.0, ci_low=0.0, ci_high=0.0)
    bootstrap = block_bootstrap(
        pooled,
        block_days=thresholds.block_days,
        iterations=thresholds.bootstrap_iterations,
        alpha=thresholds.alpha,
        seed=thresholds.seed,
    )
    return IntervalResult(
        trades=bootstrap.trades,
        net_bps=bootstrap.net_bps,
        ci_low=bootstrap.ci_low,
        ci_high=bootstrap.ci_high,
    )


def doubled_costs(costs_config: CostConfig, multiplier: float):
    """Per-ticker cost model with spread and slippage multiplied."""

    def costs_for(ticker: str) -> CostModel:
        described = costs_config.for_ticker(ticker).describe()
        return BpsCosts(
            spread_bps=float(described["spread_bps"]) * multiplier,
            slippage_bps=float(described["slippage_bps"]) * multiplier,
        )

    return costs_for


def tp_penalty_costs(costs_config: CostConfig, penalty_bps: float):
    """Per-ticker cost model with the take-profit fill penalty added."""

    def costs_for(ticker: str) -> CostModel:
        described = costs_config.for_ticker(ticker).describe()
        return BpsCosts(
            spread_bps=float(described["spread_bps"]),
            slippage_bps=float(described["slippage_bps"]),
            tp_penalty_bps=penalty_bps,
        )

    return costs_for


def grid_around(anchor: float, step: float, steps: int) -> list[float]:
    """Symmetric grid values around the traded parameter."""
    return [round(anchor + index * step, 6) for index in range(-steps, steps + 1)]


def pool_heatmap(
    candles_by_ticker: dict[str, list[Candle]],
    *,
    strategy: str,
    timeframe: str,
    config,
    costs_config: CostConfig,
    tp_values: list[float],
    sl_values: list[float],
    heatmap_thresholds,
) -> tuple[list[CellStats], object]:
    """Score the grid on the pool and check the plateau around the chosen cell.

    Computed on the fit window (history minus holdout): the stability check must
    not consume the holdout, and the pool is the same 20 tickers the rule trades.
    """
    cells: list[CellStats] = []
    for tp in tp_values:
        for sl in sl_values:
            trades: list[Trade] = []
            for ticker, candles in candles_by_ticker.items():
                if not candles:
                    continue
                run_config = replace(
                    config,
                    parameters=replace(
                        config.parameters, tp_percent=tp, sl_percent=sl
                    ),
                )
                trades.extend(
                    build_runner(
                        strategy, timeframe, ticker, costs_config.for_ticker(ticker), run_config
                    )(candles)
                )
            cells.append(score_cell(tp, sl, trades))
    verdict = analyze_plateau_around(
        cells,
        config.parameters.tp_percent,
        config.parameters.sl_percent,
        tp_values,
        sl_values,
        heatmap_thresholds,
    )
    return cells, verdict


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Single verdict report (T2-21)")
    parser.add_argument("--tag", default="current", help="report name")
    parser.add_argument("--portfolio-file", default=str(PORTFOLIO_PATH))
    parser.add_argument("--thresholds-file", default=str(THRESHOLDS_PATH))
    parser.add_argument("--costs-file", default=str(COSTS_PATH))
    parser.add_argument("--limit", type=int, help="first N tickers, for a smoke run")
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help="declared number of variants tried before this report (informational)",
    )
    parser.add_argument("--grid-step", type=float, default=0.5, help="heatmap step, percent")
    parser.add_argument(
        "--grid-steps", type=int, default=2, help="grid cells on each side of the anchor"
    )
    parser.add_argument(
        "--warmup-bars", type=int, default=250, help="candles before the holdout start"
    )
    parser.add_argument("--null-seeds", type=int, default=30)
    parser.add_argument(
        "--dividends-file",
        default=str(ROOT / "config" / "dividends.json"),
        help="dividend calendar used to mark dividend-affected trades (T2-22)",
    )
    parser.add_argument(
        "--no-dividends",
        action="store_true",
        help="sensitivity: run without the dividend calendar (marks only, PnL unchanged)",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    config = load_portfolio(args.portfolio_file)
    verdict_thresholds = load_verdict_thresholds(args.thresholds_file)
    pool_thresholds = load_multi_instrument_thresholds(args.thresholds_file)
    heatmap_thresholds = load_heatmap_thresholds(args.thresholds_file)
    if verdict_thresholds.holdout_start is None or verdict_thresholds.holdout_end is None:
        raise SystemExit("verdict.holdout_start / holdout_end must be set in the config")
    if args.n_trials is not None:
        verdict_thresholds = replace(verdict_thresholds, n_trials=args.n_trials)

    tickers = list(config.tickers)
    if args.limit:
        tickers = tickers[: args.limit]
    instruments = load_instruments(INSTRUMENTS_PATH)
    unknown = [ticker for ticker in tickers if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")

    costs_config = load_costs(args.costs_file)
    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    timeframe = Timeframe(config.timeframe)

    # Load once: the heatmap needs the same candles for dozens of cells.
    raw: dict[str, list[Candle]] = {
        ticker: history.get_candles(instruments[ticker]["figi"], timeframe)
        for ticker in tickers
    }
    empty = [ticker for ticker, candles in raw.items() if len(candles) < 2]
    if empty:
        raise SystemExit(f"No candles for: {', '.join(empty)}")

    holdout_start = datetime.combine(
        verdict_thresholds.holdout_start, datetime.min.time(), tzinfo=next(
            candles[0].open_time.tzinfo for candles in raw.values() if candles
        )
    )
    holdout_end = datetime.combine(
        verdict_thresholds.holdout_end, datetime.min.time(), tzinfo=holdout_start.tzinfo
    )
    warmup = timedelta(seconds=timeframe.to_timedelta.total_seconds() * args.warmup_bars)

    full = raw
    fit = {
        ticker: slice_window(candles, None, holdout_start, 0) for ticker, candles in raw.items()
    }
    holdout = {
        ticker: slice_window(candles, holdout_start - warmup, holdout_end, 0)
        for ticker, candles in raw.items()
    }

    dividend_thresholds = load_dividend_thresholds(args.thresholds_file)
    resolved = []
    ex_bars: dict[str, tuple] = {}
    coverage: dict | None = None
    calendar = None
    if args.no_dividends:
        logger.info("Dividend calendar disabled by --no-dividends")
    else:
        calendar = load_dividends(args.dividends_file)
        resolved = resolve_events(
            calendar,
            raw,
            gap_match_ratio=dividend_thresholds.gap_match_ratio,
        )
        ex_bars = applied_ex_bars(resolved)
        coverage = build_coverage(
            calendar,
            resolved,
            {ticker: gap_series(candles) for ticker, candles in raw.items()},
            tickers=tickers,
            years=years_from_candles(raw),
            gap_threshold_percent=pool_thresholds.gap_threshold_percent,
        ).describe()

    logger.info("Pool: full history")
    pooled_full = run_pool(
        full,
        strategy=config.strategy,
        timeframe=config.timeframe,
        config=config,
        costs_for=costs_config.for_ticker,
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    pool_interval = interval(pooled_full, pool_thresholds)
    pool_summary = summarize(pooled_full, pool_thresholds)

    logger.info("Pool: holdout")
    pooled_holdout = run_pool(
        holdout,
        strategy=config.strategy,
        timeframe=config.timeframe,
        config=config,
        costs_for=costs_config.for_ticker,
        thresholds=pool_thresholds,
        trade_start=holdout_start,
        trade_end=holdout_end,
        ex_bars_by_ticker=ex_bars,
    )
    holdout_interval = interval(pooled_holdout, pool_thresholds)

    logger.info("Pool: doubled costs")
    pooled_doubled = run_pool(
        full,
        strategy=config.strategy,
        timeframe=config.timeframe,
        config=config,
        costs_for=doubled_costs(costs_config, verdict_thresholds.cost_multiplier),
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    doubled_interval = interval(pooled_doubled, pool_thresholds)

    logger.info("Pool: TP penalty")
    pooled_penalty = run_pool(
        full,
        strategy=config.strategy,
        timeframe=config.timeframe,
        config=config,
        costs_for=tp_penalty_costs(costs_config, verdict_thresholds.tp_penalty_bps),
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    penalty_interval = interval(pooled_penalty, pool_thresholds)

    variants = leave_one_out_variants(
        pooled_full, thresholds=pool_thresholds, tickers=config.tickers
    )
    without_best = next(
        (variant for variant in variants if variant.name == WITHOUT_BEST_TICKER), None
    )
    without_best_interval = (
        IntervalResult(
            trades=without_best.summary.trades,
            net_bps=without_best.summary.net_bps,
            ci_low=without_best.bootstrap.ci_low,
            ci_high=without_best.bootstrap.ci_high,
        )
        if without_best is not None
        else None
    )

    clean = [item for item in pooled_full if not item.gap_affected]
    clean_interval = interval(clean, pool_thresholds)
    gap_verdict_changed = (clean_interval.positive != pool_interval.positive) or (
        clean_interval.negative != pool_interval.negative
    )

    without_dividends = [item for item in pooled_full if not item.dividend_affected]
    dividend_interval = interval(without_dividends, pool_thresholds)
    dividend_verdict_changed = (
        dividend_interval.positive != pool_interval.positive
        or dividend_interval.negative != pool_interval.negative
    )

    logger.info("Heatmap around the chosen cell")
    tp_values = grid_around(
        config.parameters.tp_percent, args.grid_step, args.grid_steps
    )
    sl_values = grid_around(
        config.parameters.sl_percent, args.grid_step, args.grid_steps
    )
    cells, heatmap_verdict = pool_heatmap(
        fit,
        strategy=config.strategy,
        timeframe=config.timeframe,
        config=config,
        costs_config=costs_config,
        tp_values=tp_values,
        sl_values=sl_values,
        heatmap_thresholds=heatmap_thresholds,
    )

    logger.info("Null test")
    null_result = null_test(
        synthetic_candles(),
        seeds=args.null_seeds,
        tp_percent=config.parameters.tp_percent,
        sl_percent=config.parameters.sl_percent,
        costs=costs_config.for_ticker(None),
        timeframe=config.timeframe,
    )

    inputs = VerdictInputs(
        pool=pool_interval,
        oos=holdout_interval,
        pool_without_best=without_best_interval,
        costs_doubled=doubled_interval,
        tp_penalty=penalty_interval,
        null_test=null_result.verdict,
        heatmap_status=heatmap_verdict.status,
        heatmap_plateau=heatmap_verdict.is_plateau,
        tradable=all(
            costs_config.for_ticker(ticker).is_tradable for ticker in tickers
        ),
        gap_verdict_changed=gap_verdict_changed,
        dividend_verdict_changed=dividend_verdict_changed,
        best_ticker=pool_summary.best_ticker,
        best_contribution_percent=pool_summary.best_contribution_percent,
        n_trials=verdict_thresholds.n_trials,
        extra_meta={
            "strategy": config.strategy,
            "timeframe": config.timeframe,
            "portfolio_version": config.fingerprint,
            "tickers": len(tickers),
            "tickers_sha256": config.tickers_sha256,
            "params_sha256": config.params_sha256,
            "tp_percent": config.parameters.tp_percent,
            "sl_percent": config.parameters.sl_percent,
            "costs_version": f"{costs_config.version}+{costs_config.sha256[:12]}",
            "commissions": describe_commissions(),
            "dividends": (
                "disabled (--no-dividends)"
                if calendar is None
                else (
                    f"{calendar.version}+{calendar.sha256[:12]}, "
                    f"gap_match_ratio={dividend_thresholds.gap_match_ratio:g}"
                )
            ),
            "model_revision": dividend_thresholds.model_revision,
            "dividend_affected": {
                "trades": pool_summary.dividend_affected_trades,
                "net_rub": round(pool_summary.dividend_affected_net_rub, 2),
                "share": (
                    round(pool_summary.dividend_affected_share, 4)
                    if pool_summary.dividend_affected_share is not None
                    else None
                ),
            },
            "without_dividend_affected": {
                "trades": dividend_interval.trades,
                "net_bps": round(dividend_interval.net_bps, 2),
                "ci_low": round(dividend_interval.ci_low, 2),
                "ci_high": round(dividend_interval.ci_high, 2),
                "positive": dividend_interval.positive,
            },
            "dividend_coverage": coverage,
            "heatmap_version": heatmap_thresholds.fingerprint,
            "null_test_costs": costs_config.for_ticker(None).describe(),
        },
    )
    result = evaluate_verdict(inputs, verdict_thresholds)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"verdict_{args.tag}.md"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    json_path = out_dir / f"verdict_{args.tag}.json"
    payload = {
        **result.describe(),
        "heatmap": {
            "anchor_tp": config.parameters.tp_percent,
            "anchor_sl": config.parameters.sl_percent,
            "tp_values": tp_values,
            "sl_values": sl_values,
            "cells": [
                {
                    "tp_percent": cell.tp,
                    "sl_percent": cell.sl,
                    "trades": cell.trades,
                    "expectancy_bps": round(cell.expectancy_bps, 4),
                    "pnl_net_rub": round(cell.pnl_net, 2),
                }
                for cell in cells
            ],
            "verdict": heatmap_verdict.describe(),
        },
        "null_test": null_result.to_dict(),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"strategy={config.strategy} {config.timeframe} "
          f"TP {config.parameters.tp_percent:g} / SL {config.parameters.sl_percent:g} "
          f"tickers={len(tickers)}")
    print(f"portfolio={config.fingerprint} verdict={verdict_thresholds.fingerprint}")
    print(
        f"pool: trades={pool_interval.trades} net={pool_interval.net_bps:+.2f} bps "
        f"CI95 [{pool_interval.ci_low:+.2f}; {pool_interval.ci_high:+.2f}]"
    )
    print(
        f"holdout: trades={holdout_interval.trades} net={holdout_interval.net_bps:+.2f} bps "
        f"CI95 [{holdout_interval.ci_low:+.2f}; {holdout_interval.ci_high:+.2f}]"
    )
    print(f"heatmap around anchor: {heatmap_verdict.status}")
    print(f"null test: {null_result.verdict}")
    print(f"VERDICT: {result.status}")
    for reason in result.reasons:
        print(f"  {reason.code}: {reason.message}")
    print(f"report: {md_path}")
    print(f"snapshot: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
