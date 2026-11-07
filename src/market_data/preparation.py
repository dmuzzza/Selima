"""One candle preparation for the backtest and live paths (T3-8b, ADR-028).

Indicators are computed over a sliding window, so one extra or missing bar moves
the moving averages and can move the crossover to another bar. Backtest and live
must therefore see the same series. The rules live here once, instead of being
repeated (and drifting) at each entry point:

- `BaseStrategy.run()` prepares its input before `detect_signals()`;
- `BaseStrategy.check_last_signal()` prepares its input the same way;
- `scheduler._process_instrument()` calls the function explicitly to report
  "insufficient data" before the strategy is asked anything.

The function is idempotent: preparing an already prepared series returns the
same candles, so the extra call in the scheduler changes nothing.

`min_bars` is always passed by the caller: the warmup belongs to the strategy
(`BaseStrategy.min_candles`), not to the data layer.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.market_data.models import Candle


def prepare_candles(
    candles: Iterable[Candle],
    *,
    min_bars: int,
    drop_zero_volume: bool = True,
) -> list[Candle]:
    """Return the series both paths work with.

    Rules:
    1. Bars are ordered by `open_time`: indicators depend on the sequence.
    2. Bars with `volume <= 0` are dropped when `drop_zero_volume` is on. Such a
       bar had no trades, its OHLC repeats the previous price, and it would add
       a duplicate point to every moving-average window. The rule is applied to
       the backtest as well, so zero-volume bars cannot make the paths diverge.
    3. Fewer than `min_bars` bars after the filter returns an empty list: the
       caller reports "insufficient data" instead of trading on a warmup the
       indicators cannot compute yet.
    """
    prepared = sorted(candles, key=lambda candle: candle.open_time)

    if drop_zero_volume:
        prepared = [candle for candle in prepared if candle.volume and candle.volume > 0]

    if len(prepared) < min_bars:
        return []

    return prepared
