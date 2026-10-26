"""Walk-forward validation: choose parameters on train, trade them on test.

The problem this module solves: TP/SL tuned on the whole history look great in
the backtest and fail live, because the parameters saw the future. Walk-forward
splits the history into consecutive windows and never lets a parameter see the
data it will be evaluated on:

    |---- train 1 ----|-- test 1 --|
             |---- train 2 ----|-- test 2 --|
                      |---- train 3 ----|-- test 3 --|

For every window the grid is scored on `train`, the best configuration is kept,
and only that configuration is replayed on `test`. Test results from all windows
are concatenated into the out-of-sample (OOS) series — the only numbers that say
anything about future performance.

Design notes:

- the runner is injected (`StrategyRunner`), so this module has no dependency on
  the strategy factory, the database or the clock and stays unit-testable;
- test windows start before `test_start` by `warmup_bars` candles: indicators
  need history. Trades opened during warmup are dropped, so no pre-test PnL
  leaks into the OOS result;
- each window starts from `initial_balance`; OOS PnL is the plain sum of window
  results, so compounding across windows is not modelled (conservative);
- a window where no configuration reaches `min_train_trades` is skipped instead
  of trading on a lucky single trade.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

# runner(config, candles) -> trades; config is one grid point
StrategyRunner = Callable[[dict, list[Candle]], list[Trade]]


@dataclass(frozen=True)
class WalkForwardWindow:
    """One train/test pair. `train_end` always equals `test_start`."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        if self.train_end != self.test_start:
            raise ValueError("train_end must equal test_start")
        if not self.train_start < self.train_end < self.test_end:
            raise ValueError("window must be ordered: train_start < train_end < test_end")


def generate_windows(
    start: datetime,
    end: datetime,
    *,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
) -> list[WalkForwardWindow]:
    """Build consecutive train/test windows covering [start, end).

    Args:
        start: First timestamp of the available history.
        end: End of the available history (exclusive).
        train_days: Length of the parameter-selection period.
        test_days: Length of the out-of-sample period.
        step_days: Shift between windows; defaults to `test_days`, which makes
            test periods adjacent and non-overlapping.

    Returns:
        Windows whose `test_end` does not exceed `end`. Empty when the history
        is shorter than one train+test pair.

    Raises:
        ValueError: non-positive lengths or `start >= end`.
    """
    if train_days <= 0 or test_days <= 0:
        raise ValueError("train_days and test_days must be positive")
    if step_days is not None and step_days <= 0:
        raise ValueError("step_days must be positive")
    if start >= end:
        raise ValueError("start must be earlier than end")

    shift = timedelta(days=step_days if step_days is not None else test_days)
    train_delta = timedelta(days=train_days)
    test_delta = timedelta(days=test_days)

    windows: list[WalkForwardWindow] = []
    train_start = start
    while True:
        test_start = train_start + train_delta
        test_end = test_start + test_delta
        if test_end > end:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=test_start,
                test_start=test_start,
                test_end=test_end,
            )
        )
        train_start += shift

    return windows


@dataclass
class WindowResult:
    """Outcome of one window: which config won on train and how it did on test."""

    window: WalkForwardWindow
    best_config: dict | None
    train_pnl: float
    train_trades: int
    test_pnl: float
    test_trades: int
    skipped_reason: str | None = None

    @property
    def skipped(self) -> bool:
        return self.best_config is None


@dataclass
class WalkForwardResult:
    """All windows plus the concatenated out-of-sample trades."""

    windows: list[WindowResult] = field(default_factory=list)
    oos_trades: list[Trade] = field(default_factory=list)

    @property
    def evaluated_windows(self) -> list[WindowResult]:
        return [w for w in self.windows if not w.skipped]

    @property
    def skipped_windows(self) -> list[WindowResult]:
        return [w for w in self.windows if w.skipped]

    @property
    def in_sample_pnl(self) -> float:
        """Sum of train PnL: how good the parameters looked while being chosen."""
        return sum(w.train_pnl for w in self.windows)

    @property
    def oos_pnl(self) -> float:
        """Sum of test PnL: the only number that estimates future performance."""
        return sum(w.test_pnl for w in self.windows)

    @property
    def profitable_windows(self) -> int:
        return sum(1 for w in self.evaluated_windows if w.test_pnl > 0)

    @property
    def oos_win_rate(self) -> float:
        """Share of profitable closed OOS trades, in percent."""
        closed = [t for t in self.oos_trades if not t.is_open()]
        if not closed:
            return 0.0
        winners = sum(1 for t in closed if (t.pnl_abs or 0.0) > 0)
        return winners / len(closed) * 100.0

    @property
    def degradation(self) -> float | None:
        """OOS PnL as a share of train PnL; None when train PnL is not positive.

        A value well below 100% is the usual sign of overfitting.
        """
        in_sample = self.in_sample_pnl
        if in_sample <= 0:
            return None
        return self.oos_pnl / in_sample * 100.0

    def to_dict(self) -> dict:
        return {
            "windows": [
                {
                    "train_start": w.window.train_start.isoformat(),
                    "train_end": w.window.train_end.isoformat(),
                    "test_start": w.window.test_start.isoformat(),
                    "test_end": w.window.test_end.isoformat(),
                    "best_config": w.best_config,
                    "train_pnl": round(w.train_pnl, 2),
                    "train_trades": w.train_trades,
                    "test_pnl": round(w.test_pnl, 2),
                    "test_trades": w.test_trades,
                    "skipped_reason": w.skipped_reason,
                }
                for w in self.windows
            ],
            "evaluated_windows": len(self.evaluated_windows),
            "skipped_windows": len(self.skipped_windows),
            "in_sample_pnl": round(self.in_sample_pnl, 2),
            "oos_pnl": round(self.oos_pnl, 2),
            "oos_trades": len(self.oos_trades),
            "oos_win_rate": round(self.oos_win_rate, 2),
            "profitable_windows": self.profitable_windows,
            "degradation_percent": (
                round(self.degradation, 2) if self.degradation is not None else None
            ),
        }


def run_walk_forward(
    candles: list[Candle],
    windows: Iterable[WalkForwardWindow],
    *,
    runner: StrategyRunner,
    grid: Iterable[dict],
    warmup_bars: int = 0,
    min_train_trades: int = 1,
) -> WalkForwardResult:
    """Score the grid on each train period and replay the winner on the test one.

    Args:
        candles: Full history, unsorted input is sorted by `open_time`.
        windows: Windows from `generate_windows()` (or any custom sequence).
        runner: Builds a strategy for a config and returns its trades.
        grid: Candidate configurations; each is a dict merged into strategy config.
        warmup_bars: Candles prepended to each test period so indicators are
            warm. Trades opened before `test_start` are dropped.
        min_train_trades: Minimum trades on train for a config to be eligible.
            Windows where every config fails this are skipped, not traded.

    Returns:
        Per-window results plus the concatenated OOS trades.
    """
    if warmup_bars < 0:
        raise ValueError("warmup_bars must be non-negative")
    if min_train_trades < 0:
        raise ValueError("min_train_trades must be non-negative")

    ordered = sorted(candles, key=lambda candle: candle.open_time)
    candidates = list(grid)
    if not candidates:
        raise ValueError("grid must contain at least one configuration")

    result = WalkForwardResult()

    for window in windows:
        train_candles = _slice(ordered, window.train_start, window.train_end)

        best_config: dict | None = None
        best_pnl = 0.0
        best_trades = 0

        for config in candidates:
            trades = runner(config, train_candles)
            trades_count = len(trades)
            if trades_count < min_train_trades:
                continue
            pnl = _total_pnl(trades)
            # Strict `>` keeps the first config on ties: the result is
            # deterministic and does not depend on dict ordering.
            if best_config is None or pnl > best_pnl:
                best_config = config
                best_pnl = pnl
                best_trades = trades_count

        if best_config is None:
            result.windows.append(
                WindowResult(
                    window=window,
                    best_config=None,
                    train_pnl=0.0,
                    train_trades=0,
                    test_pnl=0.0,
                    test_trades=0,
                    skipped_reason=f"no config reached {min_train_trades} train trades",
                )
            )
            continue

        test_candles = _slice_with_warmup(
            ordered, window.test_start, window.test_end, warmup_bars
        )
        test_trades = runner(best_config, test_candles)
        # Warmup trades belong to the train period, not to the OOS result.
        oos_trades = [t for t in test_trades if t.entry_time >= window.test_start]

        result.windows.append(
            WindowResult(
                window=window,
                best_config=best_config,
                train_pnl=best_pnl,
                train_trades=best_trades,
                test_pnl=_total_pnl(oos_trades),
                test_trades=len(oos_trades),
            )
        )
        result.oos_trades.extend(oos_trades)

    return result


def _total_pnl(trades: Iterable[Trade]) -> float:
    """Sum of realized PnL; still-open trades contribute nothing."""
    return sum(trade.pnl_abs or 0.0 for trade in trades)


def _slice(candles: list[Candle], start: datetime, end: datetime) -> list[Candle]:
    """Candles with `start <= open_time < end`."""
    return [candle for candle in candles if start <= candle.open_time < end]


def _slice_with_warmup(
    candles: list[Candle], start: datetime, end: datetime, warmup_bars: int
) -> list[Candle]:
    """Candles in [start, end) plus up to `warmup_bars` candles right before it."""
    indices = [i for i, candle in enumerate(candles) if start <= candle.open_time < end]
    if not indices:
        return []
    from_index = max(0, indices[0] - warmup_bars)
    return candles[from_index:indices[-1] + 1]
