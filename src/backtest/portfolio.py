"""Multi-instrument run: one rule, independent accounts, pooled trades.

The question this module answers: does one fixed TP/SL rule hold across many
instruments, or does it only work on the instrument it was tuned on?

Design decisions (ADR-022):

- **the set and the parameters are frozen before the run** — `config/portfolio.json`
  carries the ticker list, the strategy, the timeframe and TP/SL. Changing either
  means a new version and a new report; a per-ticker tune is exactly what this
  run is meant to rule out;
- **independent accounts**: every ticker starts from its own `initial_balance`
  and is simulated alone. A shared account with a position limit would silently
  drop trades depending on iteration order, which measures the portfolio
  mechanics instead of the rule. The consequence is that position limits and
  capital contention are not modelled;
- **pooled trades**: results are merged into one list with a `ticker` field, so
  the statistics describe the rule, not one lucky instrument;
- **block bootstrap by time**: trades are resampled in calendar blocks that hold
  every ticker, so the common market factor moves together. Empty blocks stay in
  the population: dropping them would bias the sample towards active periods;
- **dividends are not adjusted**: candles come from the exchange raw. An
  ex-dividend gap looks like a market move and hits stops. A trade is marked
  `gap_affected` when an overnight gap inside its holding period exceeds
  `gap_threshold_percent`; the report prints totals with and without those
  trades. A real dividend calendar is a separate task (T2-22).
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

DEFAULT_MIN_TOTAL_TRADES = 200
DEFAULT_MIN_TICKER_TRADES = 30
DEFAULT_BLOCK_DAYS = 30
DEFAULT_ITERATIONS = 10_000
DEFAULT_ALPHA = 0.05
DEFAULT_SEED = 42
DEFAULT_GAP_THRESHOLD_PERCENT = 5.0


# ── configuration ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PortfolioParameters:
    """The single parameter set applied to every ticker."""

    tp_percent: float
    sl_percent: float
    position_size_percent: float
    allow_short: bool

    def describe(self) -> dict:
        return {
            "tp_percent": self.tp_percent,
            "sl_percent": self.sl_percent,
            "position_size_percent": self.position_size_percent,
            "allow_short": self.allow_short,
        }


@dataclass(frozen=True)
class PortfolioConfig:
    """Frozen instrument set plus the rule, with fingerprints for the report."""

    version: str
    strategy: str
    timeframe: str
    parameters: PortfolioParameters
    initial_balance: float
    tickers: tuple[str, ...]
    source: str = "defaults"
    fingerprint: str = "defaults"
    tickers_sha256: str = ""
    params_sha256: str = ""

    def describe(self) -> dict:
        return {
            "portfolio_version": self.fingerprint,
            "portfolio_source": self.source,
            "tickers": len(self.tickers),
            "tickers_sha256": self.tickers_sha256,
            "params_sha256": self.params_sha256,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            **self.parameters.describe(),
            "initial_balance": self.initial_balance,
        }


def load_portfolio(path: str | Path) -> PortfolioConfig:
    """Read config/portfolio.json; the fingerprint is `<version>+<sha256[:12]>`."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    tickers = tuple(str(ticker) for ticker in payload["tickers"])
    if not tickers:
        raise ValueError(f"{path}: empty ticker list")
    if len(set(tickers)) != len(tickers):
        raise ValueError(f"{path}: duplicate tickers in the set")
    parameters = payload["parameters"]
    params = PortfolioParameters(
        tp_percent=float(parameters["tp_percent"]),
        sl_percent=float(parameters["sl_percent"]),
        position_size_percent=float(parameters.get("position_size_percent", 100.0)),
        allow_short=bool(parameters.get("allow_short", True)),
    )
    if params.tp_percent <= 0 or params.sl_percent <= 0:
        raise ValueError(f"{path}: tp_percent and sl_percent must be positive")
    return PortfolioConfig(
        version=version,
        strategy=str(payload["strategy"]),
        timeframe=str(payload["timeframe"]),
        parameters=params,
        initial_balance=float(payload.get("initial_balance", 100_000.0)),
        tickers=tickers,
        source=str(path),
        fingerprint=f"{version}+{hashlib.sha256(raw).hexdigest()[:12]}",
        # Sorted: the report must not change when the list is only reordered.
        tickers_sha256=_sha256(sorted(tickers)),
        params_sha256=_sha256(
            {
                "strategy": str(payload["strategy"]),
                "timeframe": str(payload["timeframe"]),
                **params.describe(),
            }
        ),
    )


@dataclass(frozen=True)
class MultiInstrumentThresholds:
    """Report thresholds plus provenance, printed with every report."""

    min_total_trades: int = DEFAULT_MIN_TOTAL_TRADES
    min_ticker_trades: int = DEFAULT_MIN_TICKER_TRADES
    block_days: int = DEFAULT_BLOCK_DAYS
    bootstrap_iterations: int = DEFAULT_ITERATIONS
    alpha: float = DEFAULT_ALPHA
    seed: int = DEFAULT_SEED
    gap_threshold_percent: float = DEFAULT_GAP_THRESHOLD_PERCENT
    source: str = "defaults"
    fingerprint: str = "defaults"

    def __post_init__(self) -> None:
        if self.min_total_trades < 1 or self.min_ticker_trades < 1:
            raise ValueError("trade thresholds must be positive")
        if self.block_days < 1:
            raise ValueError("block_days must be positive")
        if self.bootstrap_iterations < 1:
            raise ValueError("bootstrap_iterations must be positive")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        if self.gap_threshold_percent <= 0:
            raise ValueError("gap_threshold_percent must be positive")

    def describe(self) -> dict:
        return {
            "min_total_trades": self.min_total_trades,
            "min_ticker_trades": self.min_ticker_trades,
            "block_days": self.block_days,
            "bootstrap_iterations": self.bootstrap_iterations,
            "alpha": self.alpha,
            "seed": self.seed,
            "gap_threshold_percent": self.gap_threshold_percent,
            "validation_version": self.fingerprint,
            "validation_source": self.source,
        }


def load_multi_instrument_thresholds(path: str | Path) -> MultiInstrumentThresholds:
    """Read the `multi_instrument` section of config/validation.json."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    section = payload.get("multi_instrument")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: missing 'multi_instrument' section")
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    return MultiInstrumentThresholds(
        min_total_trades=int(section["min_total_trades"]),
        min_ticker_trades=int(section["min_ticker_trades"]),
        block_days=int(section["block_days"]),
        bootstrap_iterations=int(section.get("bootstrap_iterations", DEFAULT_ITERATIONS)),
        alpha=float(section.get("alpha", DEFAULT_ALPHA)),
        seed=int(section.get("seed", DEFAULT_SEED)),
        gap_threshold_percent=float(
            section.get("gap_threshold_percent", DEFAULT_GAP_THRESHOLD_PERCENT)
        ),
        source=str(path),
        fingerprint=f"{version}+{hashlib.sha256(raw).hexdigest()[:12]}",
    )


def _sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


# ── pooling and gap marking ────────────────────────────────────────────────


@dataclass(frozen=True)
class PooledTrade:
    """A closed trade together with the instrument it belongs to."""

    ticker: str
    trade: Trade
    gap_affected: bool = False

    @property
    def bps(self) -> float:
        """Net PnL per trade in bps of the position size."""
        if self.trade.quantity <= 0:
            return 0.0
        return (self.trade.pnl_net or 0.0) / self.trade.quantity * 10_000.0


def gap_series(candles: Sequence[Candle]) -> dict[datetime, float]:
    """Overnight gap in percent for every candle that has a predecessor.

    `gap = (open / prev_close - 1) * 100`. No dividend calendar is involved: the
    value is measured from the data, which is why it also catches unadjusted
    splits and genuine market gaps.
    """
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    gaps: dict[datetime, float] = {}
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.close <= 0:
            continue
        gaps[current.open_time] = (current.open - previous.close) / previous.close * 100.0
    return gaps


def mark_gap_affected(
    trades: Iterable[Trade],
    gaps: Mapping[datetime, float],
    *,
    threshold_percent: float,
) -> list[bool]:
    """True for trades whose holding period contains a gap above the threshold."""
    if threshold_percent <= 0:
        raise ValueError("threshold_percent must be positive")
    flags: list[bool] = []
    for trade in trades:
        if trade.exit_time is None:
            flags.append(False)
            continue
        flags.append(
            any(
                abs(gap) > threshold_percent
                for moment, gap in gaps.items()
                if trade.entry_time < moment <= trade.exit_time
            )
        )
    return flags


def pool_trades(
    runs: Mapping[str, Sequence[Trade]],
    gaps_by_ticker: Mapping[str, Mapping[datetime, float]],
    *,
    gap_threshold_percent: float,
) -> list[PooledTrade]:
    """Merge per-ticker runs into one list, marking gap-affected trades.

    Only closed trades enter the pool: an open position has no realized PnL and
    would dilute the per-trade expectancy.
    """
    pooled: list[PooledTrade] = []
    for ticker, trades in runs.items():
        closed = [trade for trade in trades if not trade.is_open()]
        flags = mark_gap_affected(
            closed,
            gaps_by_ticker.get(ticker, {}),
            threshold_percent=gap_threshold_percent,
        )
        pooled.extend(
            PooledTrade(ticker=ticker, trade=trade, gap_affected=flag)
            for trade, flag in zip(closed, flags, strict=True)
        )
    return pooled


# ── summaries ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TickerSummary:
    """Per-instrument contribution to the pooled result."""

    ticker: str
    trades: int
    net_rub: float
    net_bps: float
    win_rate: float
    share_of_pool: float
    contribution_percent: float | None
    thin: bool

    def describe(self) -> dict:
        return {
            "ticker": self.ticker,
            "trades": self.trades,
            "net_rub": round(self.net_rub, 2),
            "net_bps": round(self.net_bps, 2),
            "win_rate": round(self.win_rate, 2),
            "share_of_pool": round(self.share_of_pool, 4),
            "contribution_percent": (
                round(self.contribution_percent, 2)
                if self.contribution_percent is not None
                else None
            ),
            "thin": self.thin,
        }


@dataclass(frozen=True)
class PortfolioSummary:
    """Pooled result plus the per-ticker breakdown."""

    trades: int
    net_rub: float
    net_bps: float
    win_rate: float
    tickers_with_trades: int
    tickers_profitable: int
    tickers_above_min: int
    share_profitable: float | None
    best_ticker: str | None
    best_contribution_percent: float | None
    thin_tickers: tuple[str, ...] = ()
    per_ticker: tuple[TickerSummary, ...] = ()
    gap_affected_trades: int = 0
    gap_affected_net_rub: float = 0.0
    gap_affected_share: float | None = None

    def describe(self) -> dict:
        return {
            "trades": self.trades,
            "net_rub": round(self.net_rub, 2),
            "net_bps": round(self.net_bps, 2),
            "win_rate": round(self.win_rate, 2),
            "tickers_with_trades": self.tickers_with_trades,
            "tickers_profitable": self.tickers_profitable,
            "tickers_above_min": self.tickers_above_min,
            "share_profitable": (
                round(self.share_profitable, 4)
                if self.share_profitable is not None
                else None
            ),
            "best_ticker": self.best_ticker,
            "best_contribution_percent": (
                round(self.best_contribution_percent, 2)
                if self.best_contribution_percent is not None
                else None
            ),
            "thin_tickers": list(self.thin_tickers),
            "gap_affected_trades": self.gap_affected_trades,
            "gap_affected_net_rub": round(self.gap_affected_net_rub, 2),
            "gap_affected_share": (
                round(self.gap_affected_share, 4)
                if self.gap_affected_share is not None
                else None
            ),
        }


def summarize(pooled: Sequence[PooledTrade], thresholds: MultiInstrumentThresholds) -> PortfolioSummary:
    """Pooled totals plus per-ticker breakdown.

    `share_profitable` counts only tickers at or above `min_ticker_trades`: a
    ticker with four trades cannot be called profitable or unprofitable, and
    including it would let noise set the share. Thin tickers stay in the totals
    and are listed explicitly.
    """
    if not pooled:
        return PortfolioSummary(
            trades=0,
            net_rub=0.0,
            net_bps=0.0,
            win_rate=0.0,
            tickers_with_trades=0,
            tickers_profitable=0,
            tickers_above_min=0,
            share_profitable=None,
            best_ticker=None,
            best_contribution_percent=None,
        )

    by_ticker: dict[str, list[PooledTrade]] = {}
    for item in pooled:
        by_ticker.setdefault(item.ticker, []).append(item)

    total_net = sum(item.trade.pnl_net or 0.0 for item in pooled)
    total_bps = sum(item.bps for item in pooled)
    winners = sum(1 for item in pooled if (item.trade.pnl_net or 0.0) > 0)
    gap_items = [item for item in pooled if item.gap_affected]

    summaries: list[TickerSummary] = []
    for ticker, items in sorted(by_ticker.items()):
        net = sum(item.trade.pnl_net or 0.0 for item in items)
        bps = sum(item.bps for item in items)
        wins = sum(1 for item in items if (item.trade.pnl_net or 0.0) > 0)
        summaries.append(
            TickerSummary(
                ticker=ticker,
                trades=len(items),
                net_rub=net,
                net_bps=bps / len(items),
                win_rate=wins / len(items) * 100.0,
                share_of_pool=len(items) / len(pooled),
                contribution_percent=(net / total_net * 100.0 if total_net else None),
                thin=len(items) < thresholds.min_ticker_trades,
            )
        )

    eligible = [summary for summary in summaries if not summary.thin]
    profitable = sum(1 for summary in eligible if summary.net_bps > 0)
    best = max(summaries, key=lambda summary: summary.net_rub) if summaries else None
    return PortfolioSummary(
        trades=len(pooled),
        net_rub=total_net,
        net_bps=total_bps / len(pooled),
        win_rate=winners / len(pooled) * 100.0,
        tickers_with_trades=len(summaries),
        tickers_profitable=profitable,
        tickers_above_min=len(eligible),
        share_profitable=(profitable / len(eligible) if eligible else None),
        best_ticker=best.ticker if best is not None else None,
        best_contribution_percent=best.contribution_percent if best is not None else None,
        thin_tickers=tuple(summary.ticker for summary in summaries if summary.thin),
        per_ticker=tuple(summaries),
        gap_affected_trades=len(gap_items),
        gap_affected_net_rub=sum(item.trade.pnl_net or 0.0 for item in gap_items),
        gap_affected_share=(len(gap_items) / len(pooled) if pooled else None),
    )


# ── block bootstrap ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BlockBootstrap:
    """Percentile interval of the pooled expectancy, resampling time blocks."""

    blocks: int
    non_empty_blocks: int
    trades: int
    net_bps: float
    ci_low: float
    ci_high: float
    iterations: int
    empty_iterations: int
    seed: int
    block_days: int

    @property
    def significant_positive(self) -> bool:
        return self.ci_low > 0.0

    @property
    def significant_negative(self) -> bool:
        return self.ci_high < 0.0

    def describe(self) -> dict:
        return {
            "blocks": self.blocks,
            "non_empty_blocks": self.non_empty_blocks,
            "trades": self.trades,
            "net_bps": round(self.net_bps, 2),
            "ci95": [round(self.ci_low, 2), round(self.ci_high, 2)],
            "iterations": self.iterations,
            "empty_iterations": self.empty_iterations,
            "seed": self.seed,
            "block_days": self.block_days,
        }


def assign_blocks(
    pooled: Sequence[PooledTrade], *, block_days: int
) -> list[list[PooledTrade]]:
    """Split trades into consecutive calendar blocks, empty blocks included.

    The first block starts at the first entry time, so the split is deterministic
    and does not depend on the calendar. Empty blocks matter: they represent
    periods when the rule did not trade, and dropping them would overstate
    activity.
    """
    if block_days < 1:
        raise ValueError("block_days must be positive")
    if not pooled:
        return []
    start = min(item.trade.entry_time for item in pooled)
    indices = [
        (item.trade.entry_time - start).days // block_days for item in pooled
    ]
    blocks: list[list[PooledTrade]] = [[] for _ in range(max(indices) + 1)]
    for index, item in zip(indices, pooled, strict=True):
        blocks[index].append(item)
    return blocks


def block_bootstrap(
    pooled: Sequence[PooledTrade],
    *,
    block_days: int = DEFAULT_BLOCK_DAYS,
    iterations: int = DEFAULT_ITERATIONS,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> BlockBootstrap:
    """Resample calendar blocks and report the pooled net expectancy interval.

    The statistic is `sum(bps) / trades` — a ratio, not the mean of per-ticker
    means: a ticker with 200 trades must weigh more than one with 35. Blocks hold
    every ticker, so the market-wide factor is resampled together instead of
    being averaged away.
    """
    blocks = assign_blocks(pooled, block_days=block_days)
    if not blocks:
        return BlockBootstrap(
            blocks=0,
            non_empty_blocks=0,
            trades=0,
            net_bps=0.0,
            ci_low=0.0,
            ci_high=0.0,
            iterations=iterations,
            empty_iterations=0,
            seed=seed,
            block_days=block_days,
        )

    rng = random.Random(seed)
    size = len(blocks)
    means: list[float] = []
    empty = 0
    for _ in range(iterations):
        sample = [blocks[rng.randrange(size)] for _ in range(size)]
        count = sum(len(block) for block in sample)
        if count == 0:
            empty += 1
            continue
        means.append(sum(item.bps for block in sample for item in block) / count)

    if not means:
        return BlockBootstrap(
            blocks=size,
            non_empty_blocks=sum(1 for block in blocks if block),
            trades=len(pooled),
            net_bps=sum(item.bps for item in pooled) / len(pooled),
            ci_low=0.0,
            ci_high=0.0,
            iterations=iterations,
            empty_iterations=empty,
            seed=seed,
            block_days=block_days,
        )

    means.sort()
    low_index = int((alpha / 2.0) * len(means))
    high_index = int((1.0 - alpha / 2.0) * len(means)) - 1
    return BlockBootstrap(
        blocks=size,
        non_empty_blocks=sum(1 for block in blocks if block),
        trades=len(pooled),
        net_bps=sum(item.bps for item in pooled) / len(pooled),
        ci_low=means[max(low_index, 0)],
        ci_high=means[min(high_index, len(means) - 1)],
        iterations=iterations,
        empty_iterations=empty,
        seed=seed,
        block_days=block_days,
    )


def classify_portfolio(
    summary: PortfolioSummary,
    bootstrap: BlockBootstrap,
    thresholds: MultiInstrumentThresholds,
) -> str:
    """Verdict of the multi-instrument run.

    `insufficient_trades` comes first: with fewer than `min_total_trades` the
    interval is anecdotal and no direction should be reported.
    """
    if summary.trades < thresholds.min_total_trades:
        return "insufficient_trades"
    if bootstrap.significant_positive:
        return "positive"
    if bootstrap.significant_negative:
        return "negative"
    return "inconclusive"


# ── reports ────────────────────────────────────────────────────────────────


@dataclass
class PortfolioReport:
    """Everything the Markdown/CSV writers need, built once."""

    config: PortfolioConfig
    thresholds: MultiInstrumentThresholds
    summary: PortfolioSummary
    bootstrap: BlockBootstrap
    verdict: str
    without_gaps_summary: PortfolioSummary
    without_gaps_bootstrap: BlockBootstrap
    without_gaps_verdict: str
    without_thin_summary: PortfolioSummary
    without_thin_bootstrap: BlockBootstrap
    without_thin_verdict: str
    extra_meta: dict = field(default_factory=dict)
    snapshot_meta: dict = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            **self.config.describe(),
            **self.thresholds.describe(),
            **self.extra_meta,
            # Raw per-ticker cost maps and candle counts go to the snapshot only:
            # they are needed for reproduction but unreadable in Markdown.
            **self.snapshot_meta,
            "verdict": self.verdict,
            "summary": self.summary.describe(),
            "bootstrap": self.bootstrap.describe(),
            "tickers": [summary.describe() for summary in self.summary.per_ticker],
            "without_gap_affected": {
                "verdict": self.without_gaps_verdict,
                "summary": self.without_gaps_summary.describe(),
                "bootstrap": self.without_gaps_bootstrap.describe(),
            },
            "without_thin_tickers": {
                "verdict": self.without_thin_verdict,
                "summary": self.without_thin_summary.describe(),
                "bootstrap": self.without_thin_bootstrap.describe(),
            },
        }


def build_report(
    pooled: Sequence[PooledTrade],
    *,
    config: PortfolioConfig,
    thresholds: MultiInstrumentThresholds,
    extra_meta: Mapping[str, object] | None = None,
    snapshot_meta: Mapping[str, object] | None = None,
) -> PortfolioReport:
    """Summarise the pool three ways: all trades, without gaps, without thin tickers."""

    def evaluate(items: Sequence[PooledTrade]) -> tuple[PortfolioSummary, BlockBootstrap, str]:
        summary = summarize(items, thresholds)
        bootstrap = block_bootstrap(
            items,
            block_days=thresholds.block_days,
            iterations=thresholds.bootstrap_iterations,
            alpha=thresholds.alpha,
            seed=thresholds.seed,
        )
        return summary, bootstrap, classify_portfolio(summary, bootstrap, thresholds)

    summary, bootstrap, verdict = evaluate(pooled)
    clean = [item for item in pooled if not item.gap_affected]
    clean_summary, clean_bootstrap, clean_verdict = evaluate(clean)
    thin = set(summary.thin_tickers)
    solid = [item for item in pooled if item.ticker not in thin]
    solid_summary, solid_bootstrap, solid_verdict = evaluate(solid)

    return PortfolioReport(
        config=config,
        thresholds=thresholds,
        summary=summary,
        bootstrap=bootstrap,
        verdict=verdict,
        without_gaps_summary=clean_summary,
        without_gaps_bootstrap=clean_bootstrap,
        without_gaps_verdict=clean_verdict,
        without_thin_summary=solid_summary,
        without_thin_bootstrap=solid_bootstrap,
        without_thin_verdict=solid_verdict,
        extra_meta=dict(extra_meta or {}),
        snapshot_meta=dict(snapshot_meta or {}),
    )


def render_markdown(report: PortfolioReport) -> str:
    """Markdown report: config, thresholds, verdict, per-ticker table, gaps."""
    config = report.config
    thresholds = report.thresholds
    lines: list[str] = [
        "# Multi-instrument run — one rule, independent accounts",
        "",
        "## Configuration",
        "",
    ]
    for label, value in config.describe().items():
        lines.append(f"- **{label}**: {value}")
    for label, value in report.extra_meta.items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")
    lines.append("## Thresholds")
    lines.append("")
    for label, value in thresholds.describe().items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")

    summary = report.summary
    bootstrap = report.bootstrap
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- **verdict**: `{report.verdict}`")
    lines.append(f"- **trades**: {summary.trades} (minimum {thresholds.min_total_trades})")
    lines.append(f"- **net**: {summary.net_rub:+.2f} RUB, {summary.net_bps:+.2f} bps/trade")
    lines.append(
        f"- **block bootstrap** ({bootstrap.block_days}-day blocks, "
        f"{bootstrap.iterations} iterations): "
        f"CI95 [{bootstrap.ci_low:+.2f}; {bootstrap.ci_high:+.2f}] bps"
    )
    lines.append(
        f"- **profitable tickers**: {summary.tickers_profitable}/"
        f"{summary.tickers_above_min}"
        + (
            f" ({summary.share_profitable:.0%})"
            if summary.share_profitable is not None
            else ""
        )
    )
    if summary.best_ticker is not None:
        lines.append(
            f"- **best ticker**: {summary.best_ticker}, "
            f"{summary.best_contribution_percent:+.1f}% of the total net"
            if summary.best_contribution_percent is not None
            else f"- **best ticker**: {summary.best_ticker}"
        )
    if summary.thin_tickers:
        lines.append(
            f"- **thin tickers** (<{thresholds.min_ticker_trades} trades, "
            f"excluded from the share): {', '.join(summary.thin_tickers)}"
        )
    lines.append("")

    lines.append("## Sensitivity")
    lines.append("")
    lines.append("| variant | trades | net RUB | net bps | CI95 | verdict |")
    lines.append("|---|---|---|---|---|---|")
    for name, block in (
        ("all trades", (report.summary, report.bootstrap, report.verdict)),
        (
            "without gap-affected",
            (
                report.without_gaps_summary,
                report.without_gaps_bootstrap,
                report.without_gaps_verdict,
            ),
        ),
        (
            "without thin tickers",
            (
                report.without_thin_summary,
                report.without_thin_bootstrap,
                report.without_thin_verdict,
            ),
        ),
    ):
        variant_summary, variant_bootstrap, variant_verdict = block
        lines.append(
            f"| {name} | {variant_summary.trades} | {variant_summary.net_rub:+.2f} | "
            f"{variant_summary.net_bps:+.2f} | "
            f"[{variant_bootstrap.ci_low:+.2f}; {variant_bootstrap.ci_high:+.2f}] | "
            f"`{variant_verdict}` |"
        )
    lines.append("")

    lines.append("## Gaps")
    lines.append("")
    lines.append(
        f"Overnight gaps above {thresholds.gap_threshold_percent:g}% inside the holding "
        "period. Candles are not dividend-adjusted, so an ex-dividend gap looks like a "
        "market move: a short collects phantom profit and a long takes a phantom loss. "
        "The table shows the effect; it does not correct it (T2-22)."
    )
    lines.append("")
    lines.append(f"- **gap-affected trades**: {summary.gap_affected_trades}")
    lines.append(f"- **their net**: {summary.gap_affected_net_rub:+.2f} RUB")
    lines.append(
        f"- **share of the pool**: "
        f"{summary.gap_affected_share:.1%}"
        if summary.gap_affected_share is not None
        else "- **share of the pool**: —"
    )
    lines.append("")

    lines.append("## Per ticker")
    lines.append("")
    lines.append("| ticker | trades | net RUB | net bps | win % | share of pool | contribution % |")
    lines.append("|---|---|---|---|---|---|---|")
    for ticker in summary.per_ticker:
        contribution = (
            f"{ticker.contribution_percent:+.1f}"
            if ticker.contribution_percent is not None
            else "—"
        )
        name = f"{ticker.ticker} (thin)" if ticker.thin else ticker.ticker
        lines.append(
            f"| {name} | {ticker.trades} | {ticker.net_rub:+.2f} | "
            f"{ticker.net_bps:+.2f} | {ticker.win_rate:.1f} | "
            f"{ticker.share_of_pool:.1%} | {contribution} |"
        )
    lines.append("")
    lines.append(
        "> Independent accounts: every ticker starts from its own balance, so position "
        "limits and capital contention are not modelled (ADR-022). The set is chosen by "
        "today's liquidity, which carries survivorship bias."
    )
    lines.append("")
    return "\n".join(lines)


def write_ticker_csv(path: str | Path, summary: PortfolioSummary) -> Path:
    """Per-ticker CSV with the flags used in the report."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "ticker",
                "trades",
                "net_rub",
                "net_bps",
                "win_rate_percent",
                "share_of_pool",
                "contribution_percent",
                "thin",
            ]
        )
        for ticker in summary.per_ticker:
            writer.writerow(
                [
                    ticker.ticker,
                    ticker.trades,
                    f"{ticker.net_rub:.2f}",
                    f"{ticker.net_bps:.4f}",
                    f"{ticker.win_rate:.2f}",
                    f"{ticker.share_of_pool:.6f}",
                    (
                        f"{ticker.contribution_percent:.4f}"
                        if ticker.contribution_percent is not None
                        else ""
                    ),
                    int(ticker.thin),
                ]
            )
    return out
