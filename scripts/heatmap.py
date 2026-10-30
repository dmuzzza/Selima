"""TP/SL heatmap over the database history: plateau or a single point?

Builds two blocks:

- train windows of the walk-forward split — the decision block: parameters are
  scored only on periods that do not include the bars they are judged on;
- the whole history — diagnostics, marked "not for selection" in the report.

Outputs Markdown and CSV to debug/ (git-ignored), no images and no new
dependencies.

    uv run python scripts/heatmap.py --ticker SBER --timeframe 4h --tag t2-19
    uv run python scripts/heatmap.py --tp 1,1.5,2,2.5,3 --sl 0.5,1,1.5,2,2.5

Costs come from `config/costs.json` and are applied to every run; a zero-cost
report is refused unless `--allow-gross` is given (ADR-016).
"""

from __future__ import annotations

import argparse
import hashlib
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
from src.backtest.heatmap import (  # noqa: E402
    analyze_plateau,
    build_grid,
    collect_full_history_trades,
    collect_train_trades,
    load_thresholds,
    render_markdown,
    score_cells,
    write_cells_csv,
)
from src.backtest.walk_forward import generate_windows  # noqa: E402
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("heatmap")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
THRESHOLDS_PATH = ROOT / "config" / "validation.json"


def load_instruments(path: Path) -> list[dict]:
    """Read enabled instruments from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [i for i in data.get("instruments", []) if i.get("enabled", True)]


def build_runner(strategy_code: str, timeframe: str, ticker: str, costs: CostModel):
    """Runner that builds a fresh strategy per config and returns its trades."""

    def runner(config: dict, candles) -> list[Trade]:
        strategy = StrategyFactory.create(
            strategy_code,
            timeframe,
            config={"ticker": ticker, "costs": costs, **config},
        )
        return strategy.run(candles)["trades"]

    return runner


def parse_float_list(value: str) -> list[float]:
    """Parse a comma-separated list of floats."""
    try:
        return [float(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number list: {value}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TP/SL heatmap: plateau or single point")
    parser.add_argument(
        "--strategy",
        default="dual_ma_crossover_after_testing",
        help="strategy code from STRATEGY_REGISTRY",
    )
    parser.add_argument("--ticker", default="SBER", help="instrument ticker")
    parser.add_argument("--timeframe", default="4h", help="1h, 4h, 1d")
    parser.add_argument("--tag", default="current", help="report name")
    parser.add_argument("--train-days", type=int, default=365, help="train window length")
    parser.add_argument("--test-days", type=int, default=90, help="test window length")
    parser.add_argument(
        "--step-days", type=int, default=None, help="window shift (default: test-days)"
    )
    parser.add_argument(
        "--warmup-bars",
        type=int,
        default=250,
        help="candles prepended to each train window for indicators",
    )
    parser.add_argument(
        "--tp", type=parse_float_list, default=[1.0, 2.0, 3.0], help="TP grid, e.g. 1,2,3"
    )
    parser.add_argument(
        "--sl",
        type=parse_float_list,
        default=[0.5, 1.0, 2.0],
        help="SL grid, e.g. 0.5,1,2",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=None,
        help="override min_trades_per_cell from config/validation.json",
    )
    parser.add_argument(
        "--thresholds-file",
        default=str(THRESHOLDS_PATH),
        help="validation thresholds config",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--costs-file",
        default=str(ROOT / "config" / "costs.json"),
        help="per-instrument spread/slippage/commission config",
    )
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=None,
        help="override the full spread for the instrument",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="override the per-side slippage",
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

    instruments = [
        instrument
        for instrument in load_instruments(INSTRUMENTS_PATH)
        if instrument["ticker"].upper() == args.ticker.upper()
    ]
    if not instruments:
        raise SystemExit(f"Ticker {args.ticker} not found in config/instruments.json")
    instrument = instruments[0]

    costs_config: CostConfig = load_costs(args.costs_file)
    costs: CostModel = costs_config.for_ticker(instrument["ticker"])
    if args.spread_bps is not None or args.slippage_bps is not None:
        costs = BpsCosts(
            spread_bps=args.spread_bps or 0.0,
            slippage_bps=args.slippage_bps or 0.0,
        )
    if not costs.is_tradable and not args.allow_gross:
        raise SystemExit(
            "Refusing to run: the cost model is zero, the report would be GROSS "
            "and not tradable (ADR-016). Pass --allow-gross if you really want it."
        )

    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    candles = history.get_candles(instrument["figi"], Timeframe(args.timeframe))
    if len(candles) < 2:
        raise SystemExit("Not enough candles in the database for this instrument/timeframe")

    bar = Timeframe(args.timeframe).to_timedelta
    windows = generate_windows(
        candles[0].open_time,
        candles[-1].open_time + bar,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    if not windows:
        raise SystemExit("History is shorter than one train+test window")

    thresholds = load_thresholds(args.thresholds_file)
    if args.min_trades is not None:
        thresholds = replace(
            thresholds,
            min_trades_per_cell=args.min_trades,
            fingerprint=f"{thresholds.fingerprint}+min_trades={args.min_trades}",
        )

    grid = build_grid(args.tp, args.sl)
    runner = build_runner(args.strategy, args.timeframe, instrument["ticker"], costs)

    train_cells = score_cells(
        collect_train_trades(
            candles,
            windows,
            runner=runner,
            grid=grid,
            warmup_bars=args.warmup_bars,
        )
    )
    full_cells = score_cells(
        collect_full_history_trades(candles, runner=runner, grid=grid)
    )
    train_verdict = analyze_plateau(train_cells, args.tp, args.sl, thresholds)
    full_verdict = analyze_plateau(full_cells, args.tp, args.sl, thresholds)

    grid_sha = hashlib.sha256(
        json.dumps(grid, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    common_meta = {
        "strategy": args.strategy,
        "ticker": instrument["ticker"],
        "timeframe": args.timeframe,
        "candles": len(candles),
        "costs": f"{costs.describe()['label']} ({costs.describe()})",
        "commissions": describe_commissions(),
        "grid": f"{len(args.tp)}x{len(args.sl)}, sha256 {grid_sha}",
        "tp_sl_source": "grid (ADR-019)",
    }
    train_meta = {
        **common_meta,
        "block": "TRAIN WINDOWS (decision block)",
        "windows": len(windows),
        "train_days": args.train_days,
        "test_days": args.test_days,
        "step_days": args.step_days or args.test_days,
        "warmup_bars": args.warmup_bars,
    }
    full_meta = {
        **common_meta,
        "block": "WHOLE HISTORY — DIAGNOSTICS, NOT FOR SELECTION",
        "history": f"{candles[0].open_time.isoformat()} .. {candles[-1].open_time.isoformat()}",
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = "\n\n---\n\n".join(
        [
            render_markdown(
                title=f"TP/SL heatmap — {instrument['ticker']} {args.timeframe} (train windows)",
                cells=train_cells,
                tp_values=args.tp,
                sl_values=args.sl,
                thresholds=thresholds,
                verdict=train_verdict,
                meta=train_meta,
            ),
            render_markdown(
                title=(
                    f"TP/SL heatmap — {instrument['ticker']} {args.timeframe} "
                    "(whole history, diagnostics)"
                ),
                cells=full_cells,
                tp_values=args.tp,
                sl_values=args.sl,
                thresholds=thresholds,
                verdict=full_verdict,
                meta=full_meta,
            ),
        ]
    )
    md_path = out_dir / f"heatmap_{args.tag}.md"
    md_path.write_text(markdown, encoding="utf-8")
    train_csv = write_cells_csv(
        out_dir / f"heatmap_{args.tag}_train.csv",
        cells=train_cells,
        thresholds=thresholds,
        verdict=train_verdict,
    )
    full_csv = write_cells_csv(
        out_dir / f"heatmap_{args.tag}_full_history.csv",
        cells=full_cells,
        thresholds=thresholds,
        verdict=full_verdict,
    )

    print(f"strategy={args.strategy} {instrument['ticker']} {args.timeframe}")
    print(f"thresholds={thresholds.fingerprint} min_trades={thresholds.min_trades_per_cell}")
    print(f"train windows={len(windows)} verdict={train_verdict.status}: {train_verdict.reason}")
    print(f"whole history verdict={full_verdict.status} (diagnostics only)")
    print(f"report: {md_path}")
    print(f"csv: {train_csv}")
    print(f"csv: {full_csv}")
    if not costs.is_tradable:
        print("WARNING: GROSS run, not tradable (ADR-016).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
