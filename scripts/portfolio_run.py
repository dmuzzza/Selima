"""Multi-instrument run over the database history: one rule, independent accounts.

Reads the frozen set from `config/portfolio.json` (tickers, strategy, timeframe,
TP/SL), runs every ticker alone from its own balance, pools the closed trades and
resamples calendar blocks to put a confidence interval on the pooled expectancy.

    uv run python scripts/portfolio_run.py --tag t2-20
    uv run python scripts/portfolio_run.py --tag sens --tp 2.0 --sl 1.5

The set and the parameters are fixed before the run: `--tp`/`--sl` exist for a
separate sensitivity pass and must not replace the main report (ADR-022).

Costs come from `config/costs.json` per instrument; a zero-cost report is refused
unless `--allow-gross` is given (ADR-016). Outputs go to debug/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
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
from src.backtest.portfolio import (  # noqa: E402
    PortfolioConfig,
    build_report,
    gap_series,
    load_multi_instrument_thresholds,
    load_portfolio,
    pool_trades,
    render_markdown,
    write_ticker_csv,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("portfolio_run")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
PORTFOLIO_PATH = ROOT / "config" / "portfolio.json"
THRESHOLDS_PATH = ROOT / "config" / "validation.json"


def load_instruments(path: Path) -> dict[str, dict]:
    """figi and metadata by ticker from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {i["ticker"].upper(): i for i in data.get("instruments", [])}


def build_runner(
    strategy_code: str,
    timeframe: str,
    ticker: str,
    costs: CostModel,
    config: PortfolioConfig,
):
    """Runner for one ticker: fresh strategy, fixed parameters, own balance."""

    def runner(candles) -> list[Trade]:
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-instrument run: one rule, pooled trades")
    parser.add_argument("--tag", default="current", help="report name")
    parser.add_argument(
        "--portfolio-file", default=str(PORTFOLIO_PATH), help="frozen set and parameters"
    )
    parser.add_argument(
        "--thresholds-file", default=str(THRESHOLDS_PATH), help="report thresholds"
    )
    parser.add_argument(
        "--tp",
        type=float,
        default=None,
        help="sensitivity only: override TP from the portfolio config",
    )
    parser.add_argument(
        "--sl",
        type=float,
        default=None,
        help="sensitivity only: override SL from the portfolio config",
    )
    parser.add_argument("--limit", type=int, help="first N tickers of the set, for a smoke run")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--costs-file",
        default=str(ROOT / "config" / "costs.json"),
        help="per-instrument spread/slippage config",
    )
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=None,
        help="sensitivity: override the full spread for every ticker",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="sensitivity: override the per-side slippage",
    )
    parser.add_argument(
        "--allow-gross",
        action="store_true",
        help="allow a zero-cost run (report is marked GROSS and is not tradable)",
    )
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
    thresholds = load_multi_instrument_thresholds(args.thresholds_file)
    if args.tp is not None or args.sl is not None:
        config = replace(
            config,
            parameters=replace(
                config.parameters,
                tp_percent=args.tp if args.tp is not None else config.parameters.tp_percent,
                sl_percent=args.sl if args.sl is not None else config.parameters.sl_percent,
            ),
            fingerprint=f"{config.fingerprint}+tp{args.tp}+sl{args.sl}",
        )
    tickers = list(config.tickers)
    if args.limit:
        tickers = tickers[: args.limit]

    instruments = load_instruments(INSTRUMENTS_PATH)
    unknown = [ticker for ticker in tickers if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")

    costs_config: CostConfig = load_costs(args.costs_file)
    global_costs: CostModel | None = None
    if args.spread_bps is not None or args.slippage_bps is not None:
        global_costs = BpsCosts(
            spread_bps=args.spread_bps or 0.0,
            slippage_bps=args.slippage_bps or 0.0,
        )

    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    timeframe = Timeframe(config.timeframe)
    runs: dict[str, list[Trade]] = {}
    gaps: dict[str, dict] = {}
    costs_used: dict[str, dict] = {}
    candles_count: dict[str, int] = {}
    skipped: dict[str, str] = {}

    for ticker in tickers:
        costs = global_costs if global_costs is not None else costs_config.for_ticker(ticker)
        if not costs.is_tradable and not args.allow_gross:
            skipped[ticker] = "zero-cost model (GROSS), pass --allow-gross to allow"
            continue
        candles = history.get_candles(instruments[ticker]["figi"], timeframe)
        if len(candles) < 2:
            skipped[ticker] = "no candles in the database"
            continue
        logger.info("Run: %s %s", ticker, config.timeframe)
        runner = build_runner(config.strategy, config.timeframe, ticker, costs, config)
        runs[ticker] = runner(candles)
        gaps[ticker] = gap_series(candles)
        costs_used[ticker] = costs.describe()
        candles_count[ticker] = len(candles)

    if not runs:
        raise SystemExit("No ticker produced a run; check the database and the cost config")

    pooled = pool_trades(
        runs,
        gaps,
        gap_threshold_percent=thresholds.gap_threshold_percent,
    )
    report = build_report(
        pooled,
        config=config,
        thresholds=thresholds,
        extra_meta={
            "costs_version": f"{costs_config.version}+{costs_config.sha256[:12]}",
            "costs_source": str(args.costs_file),
            "costs_scope": "per-instrument" if global_costs is None else "global override",
            "costs_label": "NET" if all(c["tradable"] for c in costs_used.values()) else "GROSS",
            "commissions": describe_commissions(),
            # TP/SL come from the portfolio config, so `config/tp_sl.json` is not
            # used here; the field stays in the report to keep the version block
            # complete next to costs_version (ADR-022).
            "tp_sl_source": "portfolio_config (ADR-019, ADR-022)",
            "tp_sl_version": "n/a — parameters from portfolio config, params_sha256 above",
            "tickers_run": len(runs),
            "tickers_skipped": skipped or "none",
        },
        snapshot_meta={
            "candles": candles_count,
            "costs_per_ticker": costs_used,
        },
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"portfolio_{args.tag}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path = write_ticker_csv(out_dir / f"portfolio_{args.tag}_tickers.csv", report.summary)
    json_path = out_dir / f"portfolio_{args.tag}.json"
    json_path.write_text(
        json.dumps(report.describe(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = report.summary
    print(
        f"{config.strategy} {config.timeframe} "
        f"TP {config.parameters.tp_percent:g} / SL {config.parameters.sl_percent:g} "
        f"tickers={summary.tickers_with_trades}/{len(tickers)}"
    )
    print(f"portfolio={config.fingerprint} validation={thresholds.fingerprint}")
    print(
        f"trades={summary.trades} net={summary.net_rub:+.2f} RUB "
        f"({summary.net_bps:+.2f} bps) "
        f"CI95 [{report.bootstrap.ci_low:+.2f}; {report.bootstrap.ci_high:+.2f}] "
        f"verdict={report.verdict}"
    )
    print(
        f"profitable={summary.tickers_profitable}/{summary.tickers_above_min} "
        f"best={summary.best_ticker} gaps={summary.gap_affected_trades} "
        f"thin={len(summary.thin_tickers)}"
    )
    print(f"report: {md_path}")
    print(f"csv: {csv_path}")
    print(f"snapshot: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
