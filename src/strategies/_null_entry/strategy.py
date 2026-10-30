"""Null-entry strategy: random, deterministic entries (T2-18).

Purpose: prove that the simulator does not create an edge out of nothing. With
entries independent of price, the expected net result per trade is the negative
of the round-trip costs. Anything significantly better means the engine leaks
optimism (look-ahead, favourable exit ordering, levels built from a better
price than the fill, and so on).

The strategy stays inside the contract:

- `detect_signals()` is pure: no network, no database, no `datetime.now()`;
- randomness is seeded, so a run is reproducible bit for bit;
- exits, sizing, costs and PnL are the shared ones (`TradeSimulator`).

This package starts with `_`, so the registry never discovers it.
"""

import logging
import random

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal

from .config import get_config

logger = logging.getLogger(__name__)


class NullEntryStrategy(BaseStrategy):
    """Random entries with the same exits as the strategy under test."""

    code = "null_entry"
    name = "Null Entry (random)"
    description = "Random deterministic entries: control for the simulator"

    def __init__(self, timeframe: str, config: dict | None = None, debug: bool = False) -> None:
        super().__init__(timeframe, get_config(config), debug)
        self.entry_rate = float(self.config["entry_rate"])
        self.seed = int(self.config["seed"])
        self.warmup_bars = int(self.config.get("warmup_bars", 0))

        if not 0.0 < self.entry_rate <= 1.0:
            raise ValueError(f"entry_rate must be in (0, 1], got {self.entry_rate}")

    def detect_signals(self, candles: list[Candle]) -> list[Signal]:
        """Return seeded random signals; direction is unbiased (or long-only)."""
        # A fresh generator per call keeps detect_signals() pure and repeatable.
        rng = random.Random(self.seed)
        signals: list[Signal] = []

        for index in range(self.warmup_bars, len(candles)):
            if rng.random() >= self.entry_rate:
                continue
            if self.allow_short:
                signal_type = "BULLISH" if rng.random() < 0.5 else "BEARISH"
            else:
                signal_type = "BULLISH"
            signals.append(
                Signal(
                    timestamp=candles[index].open_time,
                    candle_index=index,
                    signal_type=signal_type,
                    price=candles[index].close,
                    metadata={"source": "null_entry", "seed": self.seed},
                )
            )

        self.signals = signals
        return signals
