"""Null test: random entries must lose exactly the round-trip costs (T2-18).

The test answers one question: does the simulator create profit out of nothing?
Entries are independent of price, so the only expected result is the negative of
the trading costs. A confidence interval that lies entirely above zero means the
engine leaks optimism — look-ahead, a favourable exit order, levels built from a
better price than the fill, or costs applied to one side only.

Two data sources are supported:

- **synthetic** (default): a driftless random walk, so `pnl_price` has zero
  expectation and any positive `pnl_net` is a red flag by construction;
- **real candles**: the market drift is reported separately and excluded from
  the verdict, because a rising market makes random longs profitable.

The verdict uses a bootstrap interval of the per-trade net PnL:

| Interval | Verdict | Meaning |
|---|---|---|
| entirely below 0 | `ok` | costs are paid, no edge appears |
| contains 0 | `inconclusive` | not enough trades, or costs partly missing |
| entirely above 0 | `optimism` | the engine invents an edge |

Limitations (stated so a report cannot overclaim):

- **What it proves:** only the mechanics — costs are charged, levels are not built
  from a better price than the fill, exits do not favour the trader.
- **What it does not prove:** a cost model that is wrong in the same way for random
  and real entries (a wrong tariff, an unrealistic fill) passes this test. Neither
  does it validate a strategy's signal quality.
- **The residual is pessimism by design.** On driftless data `expectancy_price` is
  slightly negative because when a bar touches both levels SL wins (ADR-012). The
  direction is consistent with the project policy: the engine never reports a
  better result than the pessimistic assumption allows.
- **The interval is optimistic in width.** Seeds replay the *same* bars, so trades
  across seeds are correlated and the bootstrap interval is narrower than an
  interval built from independent observations. That is safe for the "zero inside"
  control, but a claim about the *magnitude* of an effect needs a block bootstrap
  over time (or independent samples), not a pooled per-trade bootstrap.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.backtest.costs import ZERO_COSTS, CostModel
from src.market_data.models import Candle
from src.strategies._null_entry.strategy import NullEntryStrategy
from src.strategies.base_strategy import Trade

DEFAULT_TP_PERCENT = 2.0
DEFAULT_SL_PERCENT = 1.0
DEFAULT_SIGMA = 0.01
DEFAULT_BARS = 2_000
DEFAULT_SEEDS = 30
DEFAULT_ITERATIONS = 2_000
DEFAULT_ALPHA = 0.05


@dataclass(frozen=True)
class NullTestResult:
    """Aggregates of a null-test run."""

    seeds: int
    trades: int
    expectancy_price: float
    expectancy_market: float
    expectancy_net: float
    market_costs_per_trade: float
    commission_per_trade: float
    market_costs_bps: float
    commission_bps: float
    ci_low: float
    ci_high: float
    price_ci_low: float
    price_ci_high: float
    verdict: str

    @property
    def costs_per_trade(self) -> float:
        """Total trading costs per trade, negative by convention."""
        return self.market_costs_per_trade - self.commission_per_trade

    @property
    def costs_bps(self) -> float:
        """Total costs in bps of the position, comparable across instruments."""
        return self.market_costs_bps - self.commission_bps

    def to_dict(self) -> dict:
        return {
            "seeds": self.seeds,
            "trades": self.trades,
            "expectancy_price": round(self.expectancy_price, 4),
            "expectancy_market": round(self.expectancy_market, 4),
            "expectancy_net": round(self.expectancy_net, 4),
            "market_costs_per_trade": round(self.market_costs_per_trade, 4),
            "commission_per_trade": round(self.commission_per_trade, 4),
            "costs_per_trade": round(self.costs_per_trade, 4),
            # bps of the position size: rubles depend on sizing, bps do not.
            "market_costs_bps": round(self.market_costs_bps, 2),
            "commission_bps": round(self.commission_bps, 2),
            "costs_bps": round(self.costs_bps, 2),
            "ci95": [round(self.ci_low, 4), round(self.ci_high, 4)],
            "price_ci95": [round(self.price_ci_low, 4), round(self.price_ci_high, 4)],
            "verdict": self.verdict,
        }


def synthetic_candles(
    bars: int = DEFAULT_BARS,
    *,
    sigma: float = DEFAULT_SIGMA,
    seed: int = 0,
    start_price: float = 100.0,
    start_time: datetime | None = None,
    bar_duration: timedelta = timedelta(hours=1),
) -> list[Candle]:
    """Driftless random walk: `close = prev * exp(sigma * z)`, `z ~ N(0, 1)`.

    High and low extend beyond the open/close by a half-normal step, so TP/SL
    are touched inside the bar as well as at the open.
    """
    rng = random.Random(seed)
    moment = start_time or datetime(2024, 1, 1)
    price = start_price
    candles: list[Candle] = []

    for index in range(bars):
        open_price = price
        close_price = open_price * math.exp(sigma * rng.gauss(0.0, 1.0))
        high_price = max(open_price, close_price) * math.exp(
            sigma * 0.5 * abs(rng.gauss(0.0, 1.0))
        )
        low_price = min(open_price, close_price) * math.exp(
            -sigma * 0.5 * abs(rng.gauss(0.0, 1.0))
        )
        candles.append(
            Candle(
                open_time=moment + index * bar_duration,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=1_000,
                is_complete=True,
            )
        )
        price = close_price

    return candles


def run_seed(
    candles: list[Candle],
    *,
    seed: int,
    tp_percent: float = DEFAULT_TP_PERCENT,
    sl_percent: float = DEFAULT_SL_PERCENT,
    costs: CostModel = ZERO_COSTS,
    entry_rate: float = 0.05,
    initial_balance: float = 100_000.0,
    position_size_percent: float = 100.0,
    allow_short: bool = True,
    timeframe: str = "1h",
) -> list[Trade]:
    """Run one seeded null-entry pass through the shared engine."""
    strategy = NullEntryStrategy(
        timeframe=timeframe,
        config={
            "seed": seed,
            "entry_rate": entry_rate,
            "initial_balance": initial_balance,
            "position_size_percent": position_size_percent,
            "allow_short": allow_short,
            "tp_percent": tp_percent,
            "sl_percent": sl_percent,
            "costs": costs,
        },
    )
    return strategy.run(candles)["trades"]


def bootstrap_ci(
    values: list[float],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    size = len(values)
    means = sorted(
        sum(rng.choices(values, k=size)) / size for _ in range(iterations)
    )
    low_index = int(iterations * (alpha / 2.0))
    high_index = int(iterations * (1.0 - alpha / 2.0)) - 1
    return means[max(low_index, 0)], means[min(high_index, iterations - 1)]


def classify(ci_low: float, ci_high: float) -> str:
    """Map a confidence interval of net expectancy to a verdict."""
    if ci_high < 0.0:
        return "ok"
    if ci_low > 0.0:
        return "optimism"
    return "inconclusive"


def null_test(
    candles: list[Candle],
    *,
    seeds: int = DEFAULT_SEEDS,
    tp_percent: float = DEFAULT_TP_PERCENT,
    sl_percent: float = DEFAULT_SL_PERCENT,
    costs: CostModel = ZERO_COSTS,
    entry_rate: float = 0.05,
    initial_balance: float = 100_000.0,
    position_size_percent: float = 100.0,
    allow_short: bool = True,
    timeframe: str = "1h",
    iterations: int = DEFAULT_ITERATIONS,
    alpha: float = DEFAULT_ALPHA,
) -> NullTestResult:
    """Run the null test over several seeds and aggregate per-trade PnL."""
    price_pnl: list[float] = []
    market_pnl: list[float] = []
    net_pnl: list[float] = []
    market_costs = 0.0
    commissions = 0.0
    # Costs in bps of the position: rubles depend on sizing, bps do not, so a
    # synthetic run and a real instrument can be compared (T2-18 review).
    market_bps: list[float] = []
    commission_bps: list[float] = []

    for seed in range(seeds):
        trades = run_seed(
            candles,
            seed=seed,
            tp_percent=tp_percent,
            sl_percent=sl_percent,
            costs=costs,
            entry_rate=entry_rate,
            initial_balance=initial_balance,
            position_size_percent=position_size_percent,
            allow_short=allow_short,
            timeframe=timeframe,
        )
        for trade in trades:
            if trade.is_open():
                continue
            price_pnl.append(trade.pnl_price or 0.0)
            market_pnl.append(trade.pnl_after_market_costs or 0.0)
            net_pnl.append(trade.pnl_net or 0.0)
            # pnl_net = pnl_gross - commission_total, so the commission enters
            # the total with a minus sign; market_cost is already negative.
            market_costs += trade.market_cost or 0.0
            commissions += trade.commission_total or 0.0
            if trade.quantity > 0:
                market_bps.append((trade.market_cost or 0.0) / trade.quantity * 10_000.0)
                commission_bps.append(trade.commission_total / trade.quantity * 10_000.0)

    trades_count = len(net_pnl)
    if not trades_count:
        return NullTestResult(
            seeds=seeds,
            trades=0,
            expectancy_price=0.0,
            expectancy_market=0.0,
            expectancy_net=0.0,
            market_costs_per_trade=0.0,
            commission_per_trade=0.0,
            market_costs_bps=0.0,
            commission_bps=0.0,
            ci_low=0.0,
            ci_high=0.0,
            price_ci_low=0.0,
            price_ci_high=0.0,
            verdict="inconclusive",
        )

    expectancy_net = sum(net_pnl) / trades_count
    ci_low, ci_high = bootstrap_ci(net_pnl, iterations=iterations, alpha=alpha)
    price_ci_low, price_ci_high = bootstrap_ci(price_pnl, iterations=iterations, alpha=alpha)
    return NullTestResult(
        seeds=seeds,
        trades=trades_count,
        expectancy_price=sum(price_pnl) / trades_count,
        expectancy_market=sum(market_pnl) / trades_count,
        expectancy_net=expectancy_net,
        market_costs_per_trade=market_costs / trades_count,
        commission_per_trade=commissions / trades_count,
        market_costs_bps=sum(market_bps) / len(market_bps) if market_bps else 0.0,
        commission_bps=sum(commission_bps) / len(commission_bps) if commission_bps else 0.0,
        ci_low=ci_low,
        ci_high=ci_high,
        price_ci_low=price_ci_low,
        price_ci_high=price_ci_high,
        verdict=classify(ci_low, ci_high),
    )
