"""Reproducible PnL baseline for backtest changes.

Runs a strategy over every instrument/timeframe available in PostgreSQL and
writes a JSON snapshot (aggregates + per-trade fingerprint hash). Two snapshots
taken before and after a simulator change make the diff explicit:

    uv run python scripts/pnl_baseline.py --tag before
    # ... change src/backtest/simulator.py ...
    uv run python scripts/pnl_baseline.py --tag after
    uv run python scripts/pnl_baseline.py --compare before after

Snapshots are written to debug/ (git-ignored). No broker calls: candles are
read from the market_data table only, so no API token is needed.

Costs are zero by default so that snapshots stay comparable with historical
runs. Such a snapshot is reported as GROSS and is not tradable: pass
`--spread-bps/--slippage-bps` or `--costs-file` for a net run (ADR-016).
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

from src.backtest.costs import (  # noqa: E402
    ZERO_COSTS,
    BpsCosts,
    CostConfig,
    CostModel,
    TickCosts,
    load_costs,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("pnl_baseline")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"


def trade_fingerprint(trade: Trade) -> str:
    """Stable string representation of a trade, used for bit-exact comparison."""
    return "|".join(
        [
            trade.entry_time.isoformat() if trade.entry_time else "-",
            repr(trade.entry_price),
            trade.exit_time.isoformat() if trade.exit_time else "-",
            trade.exit_type or "-",
            repr(trade.exit_price),
            repr(trade.pnl_abs),
            trade.exit_reason or "-",
        ]
    )


def load_instruments(path: Path) -> list[dict]:
    """Read enabled instruments from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [i for i in data.get("instruments", []) if i.get("enabled", True)]


def build_history_manager() -> HistoryManager:
    """HistoryManager for read-only access; get_candles() never uses the broker."""
    return HistoryManager(broker=None)  # type: ignore[arg-type]


def run_single(
    history: HistoryManager,
    *,
    strategy_code: str,
    figi: str,
    ticker: str,
    timeframe: str,
    include_trades: bool,
    costs: CostModel = ZERO_COSTS,
    levels_from_fill: bool = False,
) -> dict:
    """Run one strategy/instrument/timeframe combination."""
    candles = history.get_candles(figi, Timeframe(timeframe))
    entry: dict = {
        "figi": figi,
        "ticker": ticker,
        "timeframe": timeframe,
        "candles": len(candles),
    }

    if not candles:
        entry.update({"status": "no_data", "trades": 0, "closed": 0, "total_pnl": 0.0})
        return entry

    strategy = StrategyFactory.create(
        strategy_code,
        timeframe,
        config={
            "ticker": ticker,
            "costs": costs,
            "levels_from_fill": levels_from_fill,
        },
    )
    result = strategy.run(candles)
    trades: list[Trade] = result["trades"]
    stats = result["stats"]
    closed = [t for t in trades if not t.is_open()]

    fingerprints = [trade_fingerprint(t) for t in trades]
    digest = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()

    entry.update(
        {
            "status": "ok",
            "costs": costs.describe(),
            "levels_from_fill": levels_from_fill,
            "signals": len(result["signals"]),
            "trades": len(trades),
            "closed": len(closed),
            "total_pnl": stats.total_pnl,
            # Three PnL layers (ADR-017): price -> market costs -> net.
            "pnl_price": sum(t.pnl_price or 0.0 for t in closed),
            "pnl_after_market_costs": sum(t.pnl_after_market_costs or 0.0 for t in closed),
            "market_cost": sum(t.market_cost or 0.0 for t in closed),
            "commission": sum(t.commission_total for t in closed),
            "win_rate": stats.win_rate,
            "final_balance": stats.final_balance,
            "trades_sha256": digest,
        }
    )
    if include_trades:
        entry["trade_list"] = fingerprints
    return entry


def _with_tp_penalty(model: CostModel, penalty_bps: float) -> CostModel:
    """Apply the TP sensitivity knob on top of a per-instrument model."""
    if not penalty_bps:
        return model
    if isinstance(model, BpsCosts):
        return replace(model, tp_penalty_bps=penalty_bps)
    if isinstance(model, TickCosts):
        # One tick is the smallest unit: a bps penalty has no exact tick form.
        return replace(model, tp_penalty_ticks=1.0)
    return model


def build_costs(
    args: argparse.Namespace,
) -> tuple[CostConfig | None, CostModel | None, dict]:
    """Resolve the cost model for the run.

    Returns `(config, global_model, description)`:

    - `config` is set when `--costs-file` is used, so each instrument gets its
      own model;
    - `global_model` is set when flat `--spread-bps/--slippage-bps` are given;
    - with neither, the run is gross, which the report states explicitly.
    """
    if args.spread_bps is not None or args.slippage_bps is not None or args.tp_penalty_bps:
        model = BpsCosts(
            spread_bps=args.spread_bps or 0.0,
            slippage_bps=args.slippage_bps or 0.0,
            tp_penalty_bps=args.tp_penalty_bps or 0.0,
        )
        return None, model, {**model.describe(), "source": "cli"}

    if args.costs_file:
        config = load_costs(args.costs_file)
        default_model = config.for_ticker(None)
        description = {
            "costs_version": f"{config.version}+{config.sha256[:12]}",
            "source": str(args.costs_file),
            # The top-level block describes the default; each run carries its own.
            "scope": "per-instrument",
            "default": default_model.describe(),
            "tradable": default_model.is_tradable,
            "label": "NET" if default_model.is_tradable else "GROSS",
        }
        return config, None, description

    return None, None, {**ZERO_COSTS.describe(), "source": "none"}


def collect(args: argparse.Namespace) -> dict:
    """Run all requested combinations and return the snapshot payload."""
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
    costs_config, global_costs, costs_description = build_costs(args)

    runs: list[dict] = []
    for instrument in instruments:
        available = set(instrument.get("timeframes", timeframes))
        for timeframe in timeframes:
            if timeframe not in available:
                continue
            if global_costs is not None:
                costs = global_costs
            elif costs_config is not None:
                costs = _with_tp_penalty(
                    costs_config.for_ticker(instrument["ticker"]), args.tp_penalty_bps
                )
            else:
                costs = ZERO_COSTS
            logger.info("Run: %s %s %s", instrument["ticker"], instrument["figi"], timeframe)
            runs.append(
                run_single(
                    history,
                    strategy_code=args.strategy,
                    figi=instrument["figi"],
                    ticker=instrument["ticker"],
                    timeframe=timeframe,
                    include_trades=args.full,
                    costs=costs,
                    levels_from_fill=args.levels_from_fill,
                )
            )

    total_pnl = sum(r["total_pnl"] for r in runs)
    total_trades = sum(r["trades"] for r in runs)
    return {
        "strategy": args.strategy,
        "tag": args.tag,
        "timeframes": timeframes,
        "costs": costs_description,
        "runs": len(runs),
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "results": runs,
    }


def compare(before: dict, after: dict) -> int:
    """Print per-run differences; return the number of differing runs."""
    before_costs = before.get("costs", {})
    after_costs = after.get("costs", {})
    if before_costs.get("label") != after_costs.get("label"):
        print(
            f"WARNING: cost label differs "
            f"({before_costs.get('label')} -> {after_costs.get('label')}); "
            "the PnL delta mixes a cost change with the code change."
        )

    key = lambda r: (r["figi"], r["timeframe"])  # noqa: E731
    before_map = {key(r): r for r in before["results"]}
    after_map = {key(r): r for r in after["results"]}

    missing = sorted(set(before_map) - set(after_map))
    added = sorted(set(after_map) - set(before_map))
    for figi, tf in missing:
        print(f"MISSING in after: {figi} {tf}")
    for figi, tf in added:
        print(f"ADDED in after:   {figi} {tf}")

    changed = 0
    for run_key in sorted(set(before_map) & set(after_map)):
        old, new = before_map[run_key], after_map[run_key]
        # Runs without data have no trades_sha256: equal only if both are empty.
        if old.get("trades_sha256") == new.get("trades_sha256"):
            continue
        changed += 1
        print(
            f"{old['ticker']:<12} {old['timeframe']:<3} "
            f"trades {old['trades']:>4} -> {new['trades']:<4} "
            f"pnl {old['total_pnl']:>12.2f} -> {new['total_pnl']:<12.2f} "
            f"delta {new['total_pnl'] - old['total_pnl']:>12.2f}"
        )

    print(
        f"\nruns={len(before_map)} changed={changed} missing={len(missing)} added={len(added)}\n"
        f"total_pnl {before['total_pnl']:.2f} -> {after['total_pnl']:.2f} "
        f"(delta {after['total_pnl'] - before['total_pnl']:+.2f})"
    )
    return changed + len(missing) + len(added)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PnL baseline for backtest changes")
    parser.add_argument(
        "--strategy",
        default="dual_ma_crossover_after_testing",
        help="strategy code from STRATEGY_REGISTRY",
    )
    parser.add_argument("--tag", default="current", help="snapshot name, e.g. before/after")
    parser.add_argument("--timeframe", action="append", help="repeatable: 1h, 4h, 1d")
    parser.add_argument("--ticker", action="append", help="repeatable: SBER, GAZP")
    parser.add_argument("--limit", type=int, help="first N instruments only")
    parser.add_argument("--full", action="store_true", help="store per-trade fingerprints")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=None,
        help="full spread in bps for every instrument (default: 0 = GROSS)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        help="slippage per side in bps for every instrument (default: 0)",
    )
    parser.add_argument(
        "--costs-file",
        default=None,
        help="use per-instrument costs from this file instead of flat values",
    )
    parser.add_argument(
        "--tp-penalty-bps",
        type=float,
        default=0.0,
        help="sensitivity: fill TP this many bps worse than its level (default: 0)",
    )
    parser.add_argument(
        "--levels-from-fill",
        action="store_true",
        help="sensitivity: build TP/SL from the paid entry price, as live does",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.compare:
        out_dir = Path(args.out_dir)
        before = json.loads((out_dir / f"baseline_pnl_{args.compare[0]}.json").read_text("utf-8"))
        after = json.loads((out_dir / f"baseline_pnl_{args.compare[1]}.json").read_text("utf-8"))
        return 0 if compare(before, after) == 0 else 1

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    payload = collect(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"baseline_pnl_{args.tag}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    costs = payload["costs"]
    if not costs["tradable"]:
        print(
            "WARNING: costs are zero, this snapshot is GROSS and NOT TRADABLE.\n"
            "         Pass --spread-bps/--slippage-bps or --costs-file for a net run."
        )

    print(
        f"snapshot: {out_path}\nruns={payload['runs']} trades={payload['total_trades']} "
        f"total_pnl={payload['total_pnl']:.2f} costs={costs['label']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
