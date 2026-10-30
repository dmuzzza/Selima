"""Snapshot of the exact trade set produced by a strategy.

The PnL baseline (`scripts/pnl_baseline.py`) fingerprints trades by timestamps
and prices. For the exit-policy refactor (T3-4) that is not enough: the set of
trades must be proven identical, so entry/exit candle indices are recorded too.

Usage:

    python scripts/trade_set_snapshot.py --tag before \
        --ticker SBER --ticker CRU6 --ticker CNYRUB_TOM --timeframe 4h
    # ... refactor ...
    python scripts/trade_set_snapshot.py --tag after ... (same arguments)
    python scripts/trade_set_snapshot.py --compare before after

Snapshots go to `debug/` (git-ignored). No broker calls: candles are read from
the `market_data` table only, so no API token is needed.

Comparison is strict on indices, direction, exit reason and levels, and
tolerant only at the kopeck level for money fields (0.005 RUB).
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

from src.backtest.costs import ZERO_COSTS, CostModel, load_costs  # noqa: E402
from src.backtest.exit_policy import MissingExitPolicyError  # noqa: E402
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402
from src.strategies.base_strategy import Trade  # noqa: E402
from src.strategies.strategy_factory import StrategyFactory  # noqa: E402

logger = logging.getLogger("trade_set_snapshot")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
DEFAULT_COSTS_PATH = ROOT / "config" / "costs.json"

# Money fields are compared at the kopeck level; everything else strictly.
MONEY_FIELDS = (
    "entry_price",
    "exit_price",
    "tp_level",
    "sl_level",
    "pnl_abs",
    "pnl_price",
    "pnl_after_market_costs",
    "market_cost",
    "commission_total",
)
STRICT_FIELDS = ("entry_index", "exit_index", "signal_type", "exit_type", "exit_reason")
MONEY_TOLERANCE = 0.005


def trade_record(trade: Trade) -> dict:
    """Trade fields that must survive the refactor unchanged."""
    record: dict = {
        "trade_id": trade.trade_id,
        "entry_index": trade.entry_index,
        "exit_index": trade.exit_index,
        "signal_type": trade.signal_type,
        "exit_type": trade.exit_type,
        "exit_reason": trade.exit_reason,
        "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
    }
    for name in MONEY_FIELDS:
        record[name] = getattr(trade, name)
    return record


def load_instruments(path: Path) -> dict[str, dict]:
    """Map TICKER -> instrument entry from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["ticker"]).upper(): item for item in data.get("instruments", [])}


def run_single(
    history: HistoryManager,
    *,
    strategy_code: str,
    instrument: dict,
    ticker: str,
    timeframe: str,
    costs: CostModel,
) -> dict:
    """Run one strategy/instrument/timeframe combination."""
    candles = history.get_candles(instrument["figi"], Timeframe(timeframe))
    run: dict = {
        "ticker": ticker,
        "figi": instrument["figi"],
        "timeframe": timeframe,
        "candles": len(candles),
    }
    if not candles:
        run.update({"status": "no_data", "trades": 0, "total_pnl": 0.0, "trade_list": []})
        return run

    try:
        strategy = StrategyFactory.create(
            strategy_code,
            timeframe,
            config={"ticker": ticker, "costs": costs},
        )
        result = strategy.run(candles)
    except MissingExitPolicyError as exc:
        logger.warning("Skip %s %s: %s", ticker, timeframe, exc)
        run.update({"status": "no_exit_policy", "reason": str(exc), "trade_list": []})
        return run
    trades: list[Trade] = result["trades"]
    stats = result["stats"]
    closed = [t for t in trades if not t.is_open()]

    run.update(
        {
            "status": "ok",
            "costs": costs.describe(),
            "exit_policy": strategy.exit_policy_description(),
            "signals": len(result["signals"]),
            "trades": len(trades),
            "closed": len(closed),
            "total_pnl": stats.total_pnl,
            "pnl_price": sum(t.pnl_price or 0.0 for t in closed),
            "pnl_after_market_costs": sum(t.pnl_after_market_costs or 0.0 for t in closed),
            "commission": sum(t.commission_total for t in closed),
            "trade_list": [trade_record(t) for t in trades],
        }
    )
    return run


def collect(args: argparse.Namespace) -> dict:
    """Run all requested combinations and return the snapshot payload."""
    instruments = load_instruments(INSTRUMENTS_PATH)
    tickers = [t.upper() for t in args.ticker]
    unknown = [t for t in tickers if t not in instruments]
    if unknown:
        raise SystemExit(f"Unknown tickers in config/instruments.json: {', '.join(unknown)}")

    if args.gross:
        costs_description: dict = {**ZERO_COSTS.describe(), "source": "none"}
        costs_config = None
    else:
        costs_config = load_costs(args.costs_file)
        default_model = costs_config.for_ticker(None)
        costs_description = {
            "costs_version": f"{costs_config.version}+{costs_config.sha256[:12]}",
            "source": str(args.costs_file),
            "default": default_model.describe(),
        }

    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    runs: list[dict] = []
    for ticker in tickers:
        for timeframe in args.timeframe:
            costs = ZERO_COSTS if costs_config is None else costs_config.for_ticker(ticker)
            logger.info("Run: %s %s", ticker, timeframe)
            runs.append(
                run_single(
                    history,
                    strategy_code=args.strategy,
                    instrument=instruments[ticker],
                    ticker=ticker,
                    timeframe=timeframe,
                    costs=costs,
                )
            )

    return {
        "strategy": args.strategy,
        "tag": args.tag,
        "tickers": tickers,
        "timeframes": args.timeframe,
        "costs": costs_description,
        "runs": len(runs),
        "total_trades": sum(r["trades"] for r in runs),
        "total_pnl": sum(r["total_pnl"] for r in runs),
        "results": runs,
    }


def _field_diff(old: dict, new: dict, name: str) -> str | None:
    """Return a human-readable difference for one field, or None."""
    old_value, new_value = old.get(name), new.get(name)
    if name in MONEY_FIELDS:
        if old_value is None or new_value is None:
            return None if old_value is new_value else f"{name}: {old_value} -> {new_value}"
        if abs(old_value - new_value) <= MONEY_TOLERANCE:
            return None
        return f"{name}: {old_value:.6f} -> {new_value:.6f}"
    if old_value != new_value:
        return f"{name}: {old_value} -> {new_value}"
    return None


def compare_runs(old: dict, new: dict) -> list[str]:
    """Diff one run: trade count, then each trade field by field."""
    problems: list[str] = []
    old_trades, new_trades = old.get("trade_list", []), new.get("trade_list", [])
    if len(old_trades) != len(new_trades):
        problems.append(f"trades {len(old_trades)} -> {len(new_trades)}")
    for position, (old_trade, new_trade) in enumerate(
        zip(old_trades, new_trades, strict=False), start=1
    ):
        for name in STRICT_FIELDS + MONEY_FIELDS:
            diff = _field_diff(old_trade, new_trade, name)
            if diff is not None:
                problems.append(f"trade #{position}: {diff}")
    return problems


def compare(before: dict, after: dict) -> int:
    """Print per-run differences; return the number of differing runs."""
    key = lambda r: (r["ticker"], r["timeframe"])  # noqa: E731
    before_map = {key(r): r for r in before["results"]}
    after_map = {key(r): r for r in after["results"]}

    failed = 0
    for run_key in sorted(set(before_map) & set(after_map)):
        old, new = before_map[run_key], after_map[run_key]
        problems = compare_runs(old, new)
        status = "OK" if not problems else "DIFF"
        print(
            f"{old['ticker']:<12} {old['timeframe']:<3} {status:<4} "
            f"trades {old['trades']:>3} pnl {old['total_pnl']:>12.2f} -> {new['total_pnl']:>12.2f}"
        )
        for problem in problems[:20]:
            print(f"    {problem}")
        if problems:
            failed += 1

    missing = sorted(set(before_map) - set(after_map))
    added = sorted(set(after_map) - set(before_map))
    for ticker, timeframe in missing:
        print(f"MISSING in after: {ticker} {timeframe}")
    for ticker, timeframe in added:
        print(f"ADDED in after:   {ticker} {timeframe}")

    print(
        f"\nruns={len(before_map)} mismatched={failed} "
        f"missing={len(missing)} added={len(added)}\n"
        f"total_pnl {before['total_pnl']:.2f} -> {after['total_pnl']:.2f} "
        f"(delta {after['total_pnl'] - before['total_pnl']:+.2f})"
    )
    return failed + len(missing) + len(added)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trade-set snapshot for backtest refactors")
    parser.add_argument(
        "--strategy",
        default="dual_ma_crossover_after_testing",
        help="strategy code from STRATEGY_REGISTRY",
    )
    parser.add_argument("--tag", default="current", help="snapshot name, e.g. before/after")
    parser.add_argument(
        "--ticker", action="append", default=None, help="repeatable: SBER (required to collect)"
    )
    parser.add_argument(
        "--timeframe", action="append", default=None, help="repeatable: 1h, 4h, 1d"
    )
    parser.add_argument(
        "--gross", action="store_true", help="zero costs instead of config/costs.json"
    )
    parser.add_argument("--costs-file", default=str(DEFAULT_COSTS_PATH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.timeframe is None:
        args.timeframe = ["4h"]
    if not args.compare and not args.ticker:
        parser.error("--ticker is required unless --compare is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    out_dir = Path(args.out_dir)
    if args.compare:
        before = json.loads((out_dir / f"trade_set_{args.compare[0]}.json").read_text("utf-8"))
        after = json.loads((out_dir / f"trade_set_{args.compare[1]}.json").read_text("utf-8"))
        return 0 if compare(before, after) == 0 else 1

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    payload = collect(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"trade_set_{args.tag}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"snapshot: {out_path}\nruns={payload['runs']} trades={payload['total_trades']} "
        f"total_pnl={payload['total_pnl']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
