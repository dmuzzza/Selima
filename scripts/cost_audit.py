"""Decompose the PnL difference between two baseline snapshots.

The script answers a narrow question: when a cost model is switched on, where do
the rubles go? It reads two snapshots produced by `scripts/pnl_baseline.py --full`
(per-trade fingerprints) and rebuilds three PnL layers per trade:

- `pnl_price`              -- raw prices, no market costs, no commission;
- `pnl_after_market_costs` -- fills with spread and slippage, no commission;
- `pnl_net`                -- after market costs and commission (incl. margin).

The difference between the two runs is then split into four buckets:

- price/exit  -- the trade exited at another bar or price (levels moved);
- market      -- spread and slippage actually paid;
- commission  -- transaction and margin commission;
- new/lost    -- trades that exist in only one run.

The sum of the buckets equals the total PnL delta by construction, so any
unexplained residual shows up as a bug instead of hiding in a percentage.

Position size is not stored in a fingerprint, so it is replayed from the balance:
`quantity = balance * position_size_percent / 100`, `balance += pnl_net` after
each closed trade. Both runs are replayed independently.

Usage:
    uv run python scripts/cost_audit.py \\
        debug/baseline_pnl_audit_cur.json debug/baseline_pnl_audit_net.json
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.commissions import (  # noqa: E402
    TRANSACTION_COMMISSION_PERCENT,
    calculate_daily_margin_fee,
)

DEFAULT_INITIAL_BALANCE = 50_000.0
DEFAULT_POSITION_SIZE_PERCENT = 100.0
# TP is a limit order: it fills at its level and pays no market costs.
LIMIT_EXIT_TYPES = frozenset({"TP"})


@dataclass
class TradeRow:
    """One trade rebuilt from a fingerprint, with all PnL layers."""

    entry_time: datetime
    entry_price: float
    exit_time: datetime | None
    exit_type: str
    exit_price: float | None
    pnl_net: float
    quantity: float
    is_long: bool
    exit_reason: str = ""
    pnl_price: float = 0.0
    pnl_after_market: float = 0.0
    market_cost: float = 0.0
    entry_cost: float = 0.0
    exit_cost: float = 0.0
    commission: float = 0.0
    margin: float = 0.0
    entry_offset_bps: float = 0.0
    level_shift_bps: float = 0.0

    @property
    def pnl_bps(self) -> float:
        return self.pnl_net / self.quantity * 10_000.0 if self.quantity else 0.0


@dataclass
class Run:
    """A replayed snapshot: trades plus its cost description."""

    label: str
    trades: list[TradeRow] = field(default_factory=list)
    offset_bps: float = 0.0
    offset_abs: float | None = None
    costs_label: str = "GROSS"
    commission_mode: str = "SHORT_ONLY"
    levels_from_fill: bool = False

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_net for t in self.trades)


def _parse_time(raw: str) -> datetime | None:
    return None if raw == "-" else datetime.fromisoformat(raw)


def _parse_float(raw: str) -> float | None:
    return None if raw == "None" else float(raw)


def parse_fingerprint(
    raw: str,
) -> tuple[datetime, float, datetime | None, str, str, float | None, float]:
    """Split `entry_time|entry_price|exit_time|exit_type|exit_price|pnl_abs[|reason]`.

    The exit reason was appended later; snapshots written before that keep
    working, their reason is the exit type.
    """
    parts = raw.split("|")
    if len(parts) not in (6, 7):
        raise ValueError(f"unexpected trade fingerprint: {raw!r}")
    entry_time, entry_price, exit_time, exit_type, exit_price, pnl_abs = parts[:6]
    exit_reason = parts[6] if len(parts) == 7 else exit_type
    entry = _parse_time(entry_time)
    if entry is None:
        raise ValueError(f"trade without entry_time: {raw!r}")
    return (
        entry,
        float(entry_price),
        _parse_time(exit_time),
        exit_type,
        exit_reason,
        _parse_float(exit_price),
        float(pnl_abs),
    )


def _market_offset(costs: dict) -> tuple[float, float | None]:
    """Per-side deterioration: (bps, absolute) depending on the model mode."""
    if costs.get("mode") == "ticks":
        return 0.0, float(costs.get("market_offset_per_side", 0.0))
    bps = float(costs.get("spread_bps", 0.0)) / 2.0 + float(costs.get("slippage_bps", 0.0))
    return bps, None


def _commission(
    quantity: float,
    entry_price: float,
    exit_price: float | None,
    entry_time: datetime,
    exit_time: datetime | None,
    is_long: bool,
    mode: str,
) -> tuple[float, float]:
    """Transaction and margin commission, mirroring BaseStrategy._calculate_commissions."""
    if mode == "NONE":
        return 0.0, 0.0
    if mode == "LONG_ONLY" and not is_long:
        return 0.0, 0.0
    if mode == "SHORT_ONLY" and is_long:
        return 0.0, 0.0

    rate = TRANSACTION_COMMISSION_PERCENT / 100.0
    commission = quantity * rate
    if exit_price is not None:
        commission += quantity * (exit_price / entry_price) * rate
    margin = 0.0
    if exit_time is not None:
        days = max(1, (exit_time - entry_time).days)
        margin = calculate_daily_margin_fee(quantity) * days
    return commission, margin


def replay(snapshot_path: Path, label: str) -> Run:
    """Rebuild every trade of a snapshot and fill the PnL layers."""
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    run = Run(label=label)
    run_info = payload["results"][0]
    costs = run_info.get("costs", {})
    run.costs_label = costs.get("label", "GROSS")
    run.commission_mode = costs.get("commission_mode", "SHORT_ONLY")
    run.levels_from_fill = bool(run_info.get("levels_from_fill", False))
    run.offset_bps, run.offset_abs = _market_offset(costs)

    balance = DEFAULT_INITIAL_BALANCE
    for raw in run_info.get("trade_list", []):
        (
            entry_time,
            entry_price,
            exit_time,
            exit_type,
            exit_reason,
            exit_price,
            pnl_net,
        ) = parse_fingerprint(raw)
        quantity = balance * DEFAULT_POSITION_SIZE_PERCENT / 100.0

        row = TradeRow(
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_type=exit_type,
            exit_price=exit_price,
            pnl_net=pnl_net,
            quantity=quantity,
            is_long=True,
            exit_reason=exit_reason,
        )

        if exit_price is not None:
            # Direction is not stored in a fingerprint. A long exits above the
            # entry on TP and below on SL; a short does the opposite.
            is_long = exit_price >= entry_price if exit_type in LIMIT_EXIT_TYPES else (
                exit_price <= entry_price
            )
            row.is_long = is_long

            offset_abs = (
                run.offset_abs
                if run.offset_abs is not None
                else entry_price * run.offset_bps / 10_000.0
            )
            row.entry_offset_bps = offset_abs / entry_price * 10_000.0
            # With `levels_from_fill` the same offset moves TP/SL as well.
            row.level_shift_bps = row.entry_offset_bps if run.levels_from_fill else 0.0

            # Raw prices: undo the market offset the engine applied to the fills.
            raw_entry = entry_price - offset_abs if is_long else entry_price + offset_abs
            if exit_type in LIMIT_EXIT_TYPES:
                raw_exit = exit_price
                exit_offset = 0.0
            else:
                raw_exit = exit_price + offset_abs if is_long else exit_price - offset_abs
                exit_offset = offset_abs

            direction = 1.0 if is_long else -1.0
            row.pnl_price = quantity / raw_entry * (raw_exit - raw_entry) * direction
            row.pnl_after_market = quantity / entry_price * (exit_price - entry_price) * direction
            row.market_cost = row.pnl_after_market - row.pnl_price
            row.entry_cost = quantity * offset_abs / raw_entry
            row.exit_cost = quantity * exit_offset / raw_exit if exit_offset else 0.0

            commission, margin = _commission(
                quantity,
                entry_price,
                exit_price,
                entry_time,
                exit_time,
                is_long,
                run.commission_mode,
            )
            row.commission = commission
            row.margin = margin

            balance += pnl_net

        run.trades.append(row)
    return run


def _print_totals(run: Run) -> None:
    closed = [t for t in run.trades if t.exit_price is not None]
    shorts = sum(1 for t in closed if not t.is_long)
    print(f"\n[{run.label}] costs={run.costs_label} trades={len(run.trades)} closed={len(closed)}")
    print(f"  pnl_net            {run.total_pnl:>12.2f}")
    print(f"  pnl_after_market   {sum(t.pnl_after_market for t in closed):>12.2f}")
    print(f"  pnl_price          {sum(t.pnl_price for t in closed):>12.2f}")
    print(f"  market cost        {sum(t.market_cost for t in closed):>12.2f}")
    print(f"  commission         {sum(t.commission for t in closed):>12.2f}")
    print(f"  margin             {sum(t.margin for t in closed):>12.2f}")
    if closed:
        print(f"  shorts             {shorts}/{len(closed)}")
        print(
            f"  expectancy         {run.total_pnl / len(closed):>12.2f} RUB/trade "
            f"({sum(t.pnl_bps for t in closed) / len(closed):.2f} bps)"
        )


def _print_exit_breakdown(run: Run) -> None:
    closed = [t for t in run.trades if t.exit_price is not None]
    if not closed:
        return
    print("  exit type | trades | market cost | commission | avg bps")
    for exit_type in sorted({t.exit_type for t in closed}):
        group = [t for t in closed if t.exit_type == exit_type]
        print(
            f"  {exit_type:<9} | {len(group):>6} | "
            f"{sum(t.market_cost for t in group):>11.2f} | "
            f"{sum(t.commission for t in group):>10.2f} | "
            f"{sum(t.pnl_bps for t in group) / len(group):>7.2f}"
        )
    print("  exit reason | trades | market cost | avg bps")
    for reason in sorted({t.exit_reason for t in closed}):
        group = [t for t in closed if t.exit_reason == reason]
        print(
            f"  {reason:<11} | {len(group):>6} | "
            f"{sum(t.market_cost for t in group):>11.2f} | "
            f"{sum(t.pnl_bps for t in group) / len(group):>7.2f}"
        )


def bootstrap_expectancy(
    run: Run, *, iterations: int = 10_000, seed: int = 42, confidence: float = 0.95
) -> dict[str, float]:
    """Percentile bootstrap for per-trade expectancy.

    Forty-odd trades give a wide interval, so a point estimate alone cannot
    support a decision. Resampling with replacement is enough here: it needs no
    distribution assumption and no extra dependency.
    """
    closed = [t for t in run.trades if t.exit_price is not None]
    if not closed:
        return {"trades": 0, "mean_rub": 0.0, "mean_bps": 0.0, "low_rub": 0.0, "high_rub": 0.0}
    rng = random.Random(seed)
    pnl = [t.pnl_net for t in closed]
    bps = [t.pnl_bps for t in closed]
    size = len(pnl)
    means_rub: list[float] = []
    means_bps: list[float] = []
    for _ in range(iterations):
        sample = [rng.randrange(size) for _ in range(size)]
        means_rub.append(sum(pnl[i] for i in sample) / size)
        means_bps.append(sum(bps[i] for i in sample) / size)
    means_rub.sort()
    means_bps.sort()
    alpha = (1.0 - confidence) / 2.0
    low_idx = int(alpha * iterations)
    high_idx = int((1.0 - alpha) * iterations) - 1
    return {
        "trades": size,
        "mean_rub": sum(pnl) / size,
        "mean_bps": sum(bps) / size,
        "low_rub": means_rub[low_idx],
        "high_rub": means_rub[high_idx],
        "low_bps": means_bps[low_idx],
        "high_bps": means_bps[high_idx],
    }


def attribute(base: Run, net: Run) -> dict[str, float]:
    """Split `net - base` PnL delta into price, size, market, commission, new/lost.

    `price` is the pure price effect at the *base* position size, so a changed
    trade set or a moved level shows up there. `size` is the compounding effect:
    a smaller balance buys a smaller position, which scales every layer.
    """
    base_by_time = {t.entry_time: t for t in base.trades}
    net_by_time = {t.entry_time: t for t in net.trades}

    buckets = {
        "price": 0.0,
        "size": 0.0,
        "market": 0.0,
        "commission": 0.0,
        "new_trades": 0.0,
        "lost_trades": 0.0,
    }
    matched = 0
    for entry_time, net_row in net_by_time.items():
        base_row = base_by_time.get(entry_time)
        if base_row is None:
            buckets["new_trades"] += net_row.pnl_net
            continue
        matched += 1
        if base_row.quantity and net_row.quantity:
            rate_base = base_row.pnl_price / base_row.quantity
            rate_net = net_row.pnl_price / net_row.quantity
            buckets["price"] += (rate_net - rate_base) * base_row.quantity
            buckets["size"] += (net_row.quantity - base_row.quantity) * rate_net
        buckets["market"] += net_row.market_cost - base_row.market_cost
        buckets["commission"] += (base_row.commission + base_row.margin) - (
            net_row.commission + net_row.margin
        )
    for entry_time, base_row in base_by_time.items():
        if entry_time not in net_by_time:
            buckets["lost_trades"] -= base_row.pnl_net

    buckets["total"] = net.total_pnl - base.total_pnl
    buckets["residual"] = buckets["total"] - (
        buckets["price"]
        + buckets["size"]
        + buckets["market"]
        + buckets["commission"]
        + buckets["new_trades"]
        + buckets["lost_trades"]
    )
    print(f"\nAttribution {base.label} -> {net.label} (matched {matched} trades)")
    for name in ("price", "size", "market", "commission", "new_trades", "lost_trades"):
        print(f"  {name:<12} {buckets[name]:>12.2f}")
    print(f"  {'total':<12} {buckets['total']:>12.2f}")
    print(f"  {'residual':<12} {buckets['residual']:>12.2f}")
    return buckets


def write_csv(path: Path, runs: list[Run]) -> None:
    """Per-trade rows for both runs, one file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run", "entry_time", "entry_price", "exit_time", "exit_type", "exit_reason",
                "exit_price", "direction", "quantity", "pnl_price", "pnl_after_market", "pnl_net",
                "market_cost", "entry_cost", "exit_cost", "commission", "margin",
                "entry_offset_bps", "level_shift_bps", "pnl_bps",
            ]
        )
        for run in runs:
            for t in run.trades:
                writer.writerow(
                    [
                        run.label,
                        t.entry_time.isoformat(),
                        f"{t.entry_price:.4f}",
                        t.exit_time.isoformat() if t.exit_time else "",
                        t.exit_type,
                        t.exit_reason,
                        f"{t.exit_price:.4f}" if t.exit_price is not None else "",
                        "LONG" if t.is_long else "SHORT",
                        f"{t.quantity:.2f}",
                        f"{t.pnl_price:.2f}",
                        f"{t.pnl_after_market:.2f}",
                        f"{t.pnl_net:.2f}",
                        f"{t.market_cost:.2f}",
                        f"{t.entry_cost:.2f}",
                        f"{t.exit_cost:.2f}",
                        f"{t.commission:.2f}",
                        f"{t.margin:.2f}",
                        f"{t.entry_offset_bps:.2f}",
                        f"{t.level_shift_bps:.2f}",
                        f"{t.pnl_bps:.2f}",
                    ]
                )
    print(f"\nCSV: {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cost attribution between two PnL snapshots")
    parser.add_argument("base", nargs="?", help="baseline snapshot (zero market costs)")
    parser.add_argument("net", nargs="?", help="snapshot with the cost model switched on")
    parser.add_argument(
        "--oos",
        help="walk-forward snapshot: print a bootstrap CI for the OOS expectancy only",
    )
    parser.add_argument("--csv", default=str(ROOT / "debug" / "cost_audit.csv"))
    parser.add_argument(
        "--bootstrap", type=int, default=10_000, help="bootstrap iterations for the expectancy CI"
    )
    return parser.parse_args(argv)


def report_oos(path: Path, iterations: int) -> None:
    """Bootstrap CI for the out-of-sample expectancy of a walk-forward snapshot."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results") or []
    if not results:
        print(f"{path}: no results block")
        return
    run = results[0]
    pnl = [float(x) for x in run.get("oos_trade_pnl", [])]
    if not pnl:
        print(f"{path}: no oos_trade_pnl (re-run walk_forward with the current version)")
        return
    rng = random.Random(42)
    size = len(pnl)
    means = []
    for _ in range(iterations):
        means.append(sum(pnl[rng.randrange(size)] for _ in range(size)) / size)
    means.sort()
    low = means[int(0.025 * iterations)]
    high = means[int(0.975 * iterations) - 1]
    mean = sum(pnl) / size
    print(
        f"[OOS] {run.get('ticker', path.stem)} trades={size} "
        f"expectancy {mean:.2f} RUB, 95% CI [{low:.2f}; {high:.2f}] RUB "
        f"({'includes' if low <= 0 <= high else 'excludes'} zero)"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.oos:
        report_oos(Path(args.oos), args.bootstrap)
        return 0
    if not args.base or not args.net:
        raise SystemExit("base and net snapshots are required unless --oos is used")
    base = replay(Path(args.base), "base")
    net = replay(Path(args.net), "net")
    _print_totals(base)
    _print_exit_breakdown(base)
    _print_totals(net)
    _print_exit_breakdown(net)
    buckets = attribute(base, net)
    for run in (base, net):
        ci = bootstrap_expectancy(run, iterations=args.bootstrap)
        print(
            f"\n[{run.label}] expectancy {ci['mean_rub']:.2f} RUB "
            f"({ci['mean_bps']:.2f} bps), 95% CI "
            f"[{ci['low_rub']:.2f}; {ci['high_rub']:.2f}] RUB "
            f"[{ci['low_bps']:.2f}; {ci['high_bps']:.2f}] bps "
            f"on {ci['trades']} trades"
        )
    write_csv(Path(args.csv), [base, net])
    return 0 if abs(buckets["residual"]) < 0.01 else 1


if __name__ == "__main__":
    raise SystemExit(main())
