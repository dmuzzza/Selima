"""CLI for the null test (T2-18): random entries must lose the costs.

Usage:

    # Driftless synthetic walk, costs from the `default` block of costs.json
    python scripts/null_test.py --tag synthetic

    # Zero costs: the control run, the interval must contain zero
    python scripts/null_test.py --tag gross --gross

    # Real candles from PostgreSQL, per-instrument costs
    python scripts/null_test.py --tag sber --ticker SBER --timeframe 4h

Exit code 1 when the verdict is `optimism`: the engine produced a positive
expectancy out of random entries, which invalidates every PnL report until fixed.
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

from config import commissions as commissions_config  # noqa: E402
from src.backtest.costs import ZERO_COSTS, CostModel, load_costs  # noqa: E402
from src.backtest.null_test import (  # noqa: E402
    DEFAULT_ALPHA,
    DEFAULT_BARS,
    DEFAULT_ITERATIONS,
    DEFAULT_SEEDS,
    DEFAULT_SIGMA,
    DEFAULT_SL_PERCENT,
    DEFAULT_TP_PERCENT,
    null_test,
    synthetic_candles,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402

logger = logging.getLogger("null_test")

DEFAULT_OUT_DIR = ROOT / "debug"
INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
DEFAULT_COSTS_PATH = ROOT / "config" / "costs.json"


def load_instrument(ticker: str) -> dict:
    """Return the instrument entry from config/instruments.json."""
    data = json.loads(INSTRUMENTS_PATH.read_text(encoding="utf-8"))
    for instrument in data.get("instruments", []):
        if str(instrument["ticker"]).upper() == ticker.upper():
            return instrument
    raise SystemExit(f"Unknown ticker: {ticker} (check config/instruments.json)")


def resolve_costs(args: argparse.Namespace) -> tuple[CostModel, dict]:
    """Pick the cost model and its report block.

    `--gross` means *zero trading costs*, so the commission must be off too:
    a report with `COMMISSION_MODE=SHORT_ONLY` is not gross (ADR-017).
    """
    if args.gross:
        if commissions_config.COMMISSION_MODE != "NONE":
            raise SystemExit(
                "--gross requires COMMISSION_MODE=NONE, otherwise the run still pays "
                f"commission (current mode: {commissions_config.COMMISSION_MODE}).\n"
                'PowerShell: $env:COMMISSION_MODE="NONE"; python scripts/null_test.py --gross'
            )
        return ZERO_COSTS, {**ZERO_COSTS.describe(), "source": "none"}
    config = load_costs(args.costs_file)
    model = config.for_ticker(args.ticker)
    return model, config.describe(args.ticker)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Null test: random entries vs costs")
    parser.add_argument("--tag", default="current", help="snapshot name")
    parser.add_argument("--ticker", default=None, help="use real candles of this ticker")
    parser.add_argument("--timeframe", default="1h", help="timeframe for --ticker")
    parser.add_argument("--tp-percent", type=float, default=DEFAULT_TP_PERCENT)
    parser.add_argument("--sl-percent", type=float, default=DEFAULT_SL_PERCENT)
    parser.add_argument("--entry-rate", type=float, default=0.05)
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS, help="synthetic bars")
    parser.add_argument("--sigma", type=float, default=DEFAULT_SIGMA, help="synthetic vol")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--gross", action="store_true", help="zero costs (control run)")
    parser.add_argument("--costs-file", default=str(DEFAULT_COSTS_PATH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    costs, costs_report = resolve_costs(args)

    if args.ticker:
        load_dotenv(ROOT / ".env")
        required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
        instrument = load_instrument(args.ticker)
        history = HistoryManager(broker=None)  # type: ignore[arg-type]
        candles = history.get_candles(instrument["figi"], Timeframe(args.timeframe))
        source = {"mode": "real", "ticker": args.ticker, "timeframe": args.timeframe}
        if not candles:
            raise SystemExit(f"No candles for {args.ticker} {args.timeframe}")
    else:
        candles = synthetic_candles(bars=args.bars, sigma=args.sigma)
        source = {"mode": "synthetic", "bars": args.bars, "sigma": args.sigma}

    result = null_test(
        candles,
        seeds=args.seeds,
        tp_percent=args.tp_percent,
        sl_percent=args.sl_percent,
        costs=costs,
        entry_rate=args.entry_rate,
        timeframe=args.timeframe,
        iterations=args.iterations,
        alpha=args.alpha,
    )

    payload = {
        "tag": args.tag,
        "source": source,
        "tp_percent": args.tp_percent,
        "sl_percent": args.sl_percent,
        "entry_rate": args.entry_rate,
        "alpha": args.alpha,
        "costs": costs_report,
        **result.to_dict(),
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"null_test_{args.tag}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"null test [{args.tag}] {source['mode']}: seeds={result.seeds} "
        f"trades={result.trades}\n"
        f"  expectancy price  {result.expectancy_price:>10.4f} RUB/trade "
        f"CI95 [{result.price_ci_low:.4f}; {result.price_ci_high:.4f}]\n"
        f"  expectancy market {result.expectancy_market:>10.4f} RUB/trade\n"
        f"  expectancy net    {result.expectancy_net:>10.4f} RUB/trade "
        f"CI95 [{result.ci_low:.4f}; {result.ci_high:.4f}]\n"
        f"  market costs      {result.market_costs_per_trade:>10.4f} RUB/trade "
        f"({result.market_costs_bps:>7.2f} bps)\n"
        f"  commission        {result.commission_per_trade:>10.4f} RUB/trade "
        f"({result.commission_bps:>7.2f} bps)\n"
        f"  costs total       {result.costs_per_trade:>10.4f} RUB/trade "
        f"({result.costs_bps:>7.2f} bps)\n"
        f"  verdict: {result.verdict.upper()}"
    )
    if source["mode"] == "real":
        print(
            "  note: real candles carry market drift; `expectancy price` is that drift,\n"
            "        the verdict is about `expectancy net` only."
        )
    print(f"snapshot: {out_path}")

    return 1 if result.verdict == "optimism" else 0


if __name__ == "__main__":
    raise SystemExit(main())
