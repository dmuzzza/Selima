"""Walk-forward (out-of-sample) TP/SL evaluation over the database history.

For every instrument/timeframe the script splits the history into train/test
windows, picks TP/SL on train and replays the winner on test. Only the test
results are reported as the expected performance; train PnL is printed next to
them to make overfitting visible.

Costs come from `config/costs.json` (per instrument) and are applied to every
run. A zero-cost run is refused unless `--allow-gross` is given: such a report
is marked GROSS and is not tradable (ADR-016).

    uv run python scripts/walk_forward.py --tag before
    uv run python scripts/walk_forward.py --ticker SBER --timeframe 1h --limit 5
    uv run python scripts/walk_forward.py --spread-bps 2 --slippage-bps 5

Candles are read from PostgreSQL only, so no broker token is needed. Reports go
to debug/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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
from src.backtest.walk_forward import (  # noqa: E402
    WalkForwardResult,
    generate_windows,
    run_walk_forward,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("walk_forward")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"


def load_instruments(path: Path) -> list[dict]:
    """Read enabled instruments from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [i for i in data.get("instruments", []) if i.get("enabled", True)]


def build_history_manager() -> HistoryManager:
    """HistoryManager for read-only access; get_candles() never uses the broker."""
    return HistoryManager(broker=None)  # type: ignore[arg-type]


def build_grid(tp_values: list[float], sl_values: list[float]) -> list[dict]:
    """Cartesian product of TP and SL percentages."""
    return [
        {"tp_percent": tp, "sl_percent": sl}
        for tp in tp_values
        for sl in sl_values
    ]


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


def run_instrument(
    history: HistoryManager,
    *,
    strategy_code: str,
    figi: str,
    ticker: str,
    timeframe: str,
    args: argparse.Namespace,
    costs_config: CostConfig,
    global_costs: CostModel | None,
) -> dict:
    """Walk-forward for one instrument/timeframe pair."""
    candles = history.get_candles(figi, Timeframe(timeframe))
    costs = global_costs if global_costs is not None else costs_config.for_ticker(ticker)
    entry: dict = {
        "figi": figi,
        "ticker": ticker,
        "timeframe": timeframe,
        "candles": len(candles),
        "costs": costs_config.describe(ticker) if global_costs is None else costs.describe(),
    }

    if not costs.is_tradable and not args.allow_gross:
        # A gross report is not evidence about future performance (ADR-016).
        entry.update({"status": "gross_rejected"})
        return entry

    if len(candles) < 2:
        entry.update({"status": "no_data"})
        return entry

    bar = Timeframe(timeframe).to_timedelta
    windows = generate_windows(
        candles[0].open_time,
        candles[-1].open_time + bar,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    if not windows:
        entry.update({"status": "short_history", "windows": 0})
        return entry

    result: WalkForwardResult = run_walk_forward(
        candles,
        windows,
        runner=build_runner(strategy_code, timeframe, ticker, costs),
        grid=build_grid(args.tp, args.sl),
        warmup_bars=args.warmup_bars,
        min_train_trades=args.min_train_trades,
    )

    entry.update({"status": "ok", **result.to_dict()})
    return entry


def collect(args: argparse.Namespace) -> dict:
    """Run every requested combination and return the report payload."""
    instruments = load_instruments(INSTRUMENTS_PATH)
    if args.ticker:
        wanted = {t.upper() for t in args.ticker}
        instruments = [i for i in instruments if i["ticker"].upper() in wanted]
    if args.limit:
        instruments = instruments[: args.limit]
    if not instruments:
        raise SystemExit("No instruments selected (check --ticker / --limit).")

    timeframes = args.timeframe or ["1h", "4h", "1d"]
    history = build_history_manager()

    costs_config = load_costs(args.costs_file)
    global_costs: CostModel | None = None
    if args.spread_bps is not None or args.slippage_bps is not None:
        global_costs = BpsCosts(
            spread_bps=args.spread_bps or 0.0,
            slippage_bps=args.slippage_bps or 0.0,
        )
        if not global_costs.is_tradable and not args.allow_gross:
            raise SystemExit(
                "Refusing to run: --spread-bps/--slippage-bps are zero, "
                "the report would be GROSS and not tradable. "
                "Pass --allow-gross if you really want it."
            )

    runs: list[dict] = []
    for instrument in instruments:
        available = set(instrument.get("timeframes", timeframes))
        for timeframe in timeframes:
            if timeframe not in available:
                continue
            logger.info("Run: %s %s %s", instrument["ticker"], instrument["figi"], timeframe)
            entry = run_instrument(
                history,
                strategy_code=args.strategy,
                figi=instrument["figi"],
                ticker=instrument["ticker"],
                timeframe=timeframe,
                args=args,
                costs_config=costs_config,
                global_costs=global_costs,
            )
            runs.append(entry)
            if entry["status"] == "ok":
                print(format_run(entry), flush=True)

    evaluated = [r for r in runs if r["status"] == "ok"]
    rejected = [r for r in runs if r["status"] == "gross_rejected"]
    return {
        "strategy": args.strategy,
        "tag": args.tag,
        "grid": build_grid(args.tp, args.sl),
        "train_days": args.train_days,
        "test_days": args.test_days,
        "step_days": args.step_days or args.test_days,
        "warmup_bars": args.warmup_bars,
        "min_train_trades": args.min_train_trades,
        "costs": (
            global_costs.describe()
            if global_costs is not None
            else {
                "costs_version": f"{costs_config.version}+{costs_config.sha256[:12]}",
                "source": str(args.costs_file),
                "scope": "per-instrument",
                "default": costs_config.for_ticker(None).describe(),
                "tradable": costs_config.for_ticker(None).is_tradable,
                "label": (
                    "NET" if costs_config.for_ticker(None).is_tradable else "GROSS"
                ),
            }
        ),
        "commissions": describe_commissions(),
        "runs": len(runs),
        "evaluated": len(evaluated),
        "gross_rejected": len(rejected),
        "profitable_runs": sum(1 for r in evaluated if r["oos_pnl"] > 0),
        "oos_pnl": round(sum(r["oos_pnl"] for r in evaluated), 2),
        "in_sample_pnl": round(sum(r["in_sample_pnl"] for r in evaluated), 2),
        "results": runs,
    }


def format_run(entry: dict) -> str:
    """One summary line per instrument/timeframe."""
    degradation = entry.get("degradation_percent")
    degradation_text = f"{degradation:>7.1f}%" if degradation is not None else "      —"
    label = entry.get("costs", {}).get("label", "?")
    return (
        f"{entry['ticker']:<12} {entry['timeframe']:<3} "
        f"windows {entry['evaluated_windows']:>3}/{entry['evaluated_windows'] + entry['skipped_windows']:<3} "
        f"oos {entry['oos_pnl']:>12.2f} is {entry['in_sample_pnl']:>12.2f} "
        f"trades {entry['oos_trades']:>4} win {entry['oos_win_rate']:>5.1f}% "
        f"oos/is {degradation_text} costs {label}"
    )


def parse_float_list(value: str) -> list[float]:
    """Parse a comma-separated list of floats."""
    try:
        return [float(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number list: {value}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward (out-of-sample) TP/SL evaluation"
    )
    parser.add_argument(
        "--strategy",
        default="dual_ma_crossover_after_testing",
        help="strategy code from STRATEGY_REGISTRY",
    )
    parser.add_argument("--tag", default="current", help="report name, e.g. before/after")
    parser.add_argument("--timeframe", action="append", help="repeatable: 1h, 4h, 1d")
    parser.add_argument("--ticker", action="append", help="repeatable: SBER, GAZP")
    parser.add_argument("--limit", type=int, help="first N instruments only")
    parser.add_argument("--train-days", type=int, default=365, help="train window length")
    parser.add_argument("--test-days", type=int, default=90, help="test window length")
    parser.add_argument(
        "--step-days", type=int, default=None, help="window shift (default: test-days)"
    )
    parser.add_argument(
        "--warmup-bars",
        type=int,
        default=250,
        help="candles prepended to each test window for indicators",
    )
    parser.add_argument(
        "--min-train-trades",
        type=int,
        default=3,
        help="minimum train trades for a config to be eligible",
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
        help="override the full spread for every instrument",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="override the per-side slippage for every instrument",
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
        # The grid runs dozens of strategies that log "no trades" on purpose;
        # keep those warnings out of the report unless -v is given.
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    payload = collect(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"walk_forward_{args.tag}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"\nsnapshot: {out_path}\n"
        f"runs={payload['runs']} evaluated={payload['evaluated']} "
        f"gross_rejected={payload['gross_rejected']} "
        f"profitable={payload['profitable_runs']}\n"
        f"oos_pnl={payload['oos_pnl']:.2f} in_sample_pnl={payload['in_sample_pnl']:.2f} "
        f"costs={payload['costs']['label']}"
    )
    if not payload["costs"]["tradable"]:
        print("WARNING: GROSS run, not tradable (ADR-016).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
