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
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

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

    strategy = StrategyFactory.create(strategy_code, timeframe, config={"ticker": ticker})
    result = strategy.run(candles)
    trades: list[Trade] = result["trades"]
    stats = result["stats"]
    closed = [t for t in trades if not t.is_open()]

    fingerprints = [trade_fingerprint(t) for t in trades]
    digest = hashlib.sha256("\n".join(fingerprints).encode("utf-8")).hexdigest()

    entry.update(
        {
            "status": "ok",
            "signals": len(result["signals"]),
            "trades": len(trades),
            "closed": len(closed),
            "total_pnl": stats.total_pnl,
            "win_rate": stats.win_rate,
            "final_balance": stats.final_balance,
            "trades_sha256": digest,
        }
    )
    if include_trades:
        entry["trade_list"] = fingerprints
    return entry


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

    runs: list[dict] = []
    for instrument in instruments:
        available = set(instrument.get("timeframes", timeframes))
        for timeframe in timeframes:
            if timeframe not in available:
                continue
            logger.info("Run: %s %s %s", instrument["ticker"], instrument["figi"], timeframe)
            runs.append(
                run_single(
                    history,
                    strategy_code=args.strategy,
                    figi=instrument["figi"],
                    ticker=instrument["ticker"],
                    timeframe=timeframe,
                    include_trades=args.full,
                )
            )

    total_pnl = sum(r["total_pnl"] for r in runs)
    total_trades = sum(r["trades"] for r in runs)
    return {
        "strategy": args.strategy,
        "tag": args.tag,
        "timeframes": timeframes,
        "runs": len(runs),
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "results": runs,
    }


def compare(before: dict, after: dict) -> int:
    """Print per-run differences; return the number of differing runs."""
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

    print(
        f"snapshot: {out_path}\nruns={payload['runs']} trades={payload['total_trades']} "
        f"total_pnl={payload['total_pnl']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
