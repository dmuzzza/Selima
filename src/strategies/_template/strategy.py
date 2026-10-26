"""
Template Strategy

Reference implementation of the strategy contract (docs/STRATEGY_CONTRACT.md §5).

How to add a strategy:

1. Copy the package: `cp -r src/strategies/_template src/strategies/<code>`.
2. Rename the class, set `code`/`name`/`description`.
3. Implement `detect_signals()` — the only method a strategy has to write.
4. Do not override `execute_trades`, `check_last_signal`, `execute_live_trades`.
5. Register nothing manually: `@register_strategy` plus the package name is enough,
   the registry discovers all packages inside `src/strategies` on first use.

Packages starting with `_` are skipped by discovery, so this template never
reaches the registry under its own name.
"""

import logging

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal
from src.strategies.strategy_registry import register_strategy

from .config import get_config

logger = logging.getLogger(__name__)


@register_strategy
class TemplateStrategy(BaseStrategy):
    """
    Close crossing SMA: the minimal strategy that satisfies the contract.

    BULLISH — close crosses the SMA upwards, BEARISH — downwards.
    Entry/exit, commissions and statistics are handled by the base class.
    """

    code = "template"
    name = "Template Strategy"
    description = "Close crossing SMA: reference implementation for new strategies"

    def __init__(
        self, timeframe: str, config: dict | None = None, debug: bool = False
    ) -> None:
        super().__init__(timeframe, get_config(timeframe, config), debug)
        self.sma_period = self.config.get("sma_period", 50)

        if self.debug:
            self.logger.debug(f"Template strategy initialized: SMA{self.sma_period}")

    def detect_signals(self, candles: list[Candle]) -> list[Signal]:
        """Return all SMA crossings in the given candles (pure, no I/O)."""
        if len(candles) <= self.sma_period:
            logger.warning(
                f"Not enough candles for SMA{self.sma_period}: {len(candles)}"
            )
            self.signals = []
            return []

        closes = [candle.close for candle in candles]
        signals: list[Signal] = []

        for i in range(self.sma_period, len(candles)):
            prev_window = closes[i - self.sma_period:i]
            curr_window = closes[i - self.sma_period + 1:i + 1]
            sma_prev = sum(prev_window) / self.sma_period
            sma_curr = sum(curr_window) / self.sma_period

            prev_close, curr_close = closes[i - 1], closes[i]

            if prev_close <= sma_prev and curr_close > sma_curr:
                signal_type = "BULLISH"
            elif prev_close >= sma_prev and curr_close < sma_curr:
                signal_type = "BEARISH"
            else:
                continue

            signals.append(
                Signal(
                    timestamp=candles[i].open_time,
                    candle_index=i,
                    signal_type=signal_type,
                    price=curr_close,
                    metadata={"sma": sma_curr, "close": curr_close},
                )
            )

        self.signals = signals
        return signals
