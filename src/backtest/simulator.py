"""Single trade simulation engine shared by all strategies.

The engine replays signals over candles and applies exit rules (TP/SL). It is
deliberately small: strategies provide signals and, optionally, an entry filter;
everything related to position sizing, exits and PnL is handled here once.

Known limitations, kept as-is to preserve historical backtest results and to be
addressed separately (T3-5):

- one position at a time (no pyramiding, no parallel positions);
- TP is checked before SL within the same candle, which is optimistic when both
  levels are touched;
- exit time is the open time of the candle that triggered the exit, not the
  moment the level was actually touched;
- no slippage and no gap handling (exit is always at the exact level).
"""

from collections.abc import Callable
from dataclasses import dataclass

from src.market_data.models import Candle
from src.strategies.base_strategy import Signal, Trade

# entry_filter(candle_index, candle, signal) -> True to allow the entry
EntryFilter = Callable[[int, Candle, Signal], bool]
# on_close(trade, balance_at_entry) -> None; fills pnl_abs/pnl_percent on the trade
CloseHandler = Callable[[Trade, float], None]


@dataclass(frozen=True)
class ExitRules:
    """Take profit and stop loss as percentages from the entry price."""

    tp_percent: float
    sl_percent: float

    def levels(self, entry_price: float, is_long: bool) -> tuple[float, float]:
        """Return (tp_level, sl_level) for the given entry."""
        if is_long:
            tp_level = entry_price * (1 + self.tp_percent / 100.0)
            sl_level = entry_price * (1 - self.sl_percent / 100.0)
        else:
            tp_level = entry_price * (1 - self.tp_percent / 100.0)
            sl_level = entry_price * (1 + self.sl_percent / 100.0)
        return tp_level, sl_level


@dataclass(frozen=True)
class SizingRules:
    """Position size as a share of the current balance."""

    initial_balance: float = 100_000.0
    position_size_percent: float = 100.0

    def position_size(self, balance: float) -> float:
        """Money amount allocated to the next position."""
        return balance * (self.position_size_percent / 100.0)


class TradeSimulator:
    """Replay signals over candles and produce closed/open trades."""

    def __init__(
        self,
        *,
        exit_rules: ExitRules,
        sizing: SizingRules,
        on_close: CloseHandler,
        entry_filter: EntryFilter | None = None,
        allow_short: bool = True,
        logger=None,
        debug: bool = False,
    ) -> None:
        self.exit_rules = exit_rules
        self.sizing = sizing
        self.on_close = on_close
        self.entry_filter = entry_filter
        self.allow_short = allow_short
        self.logger = logger
        self.debug = debug

    def run(self, candles: list[Candle], signals: list[Signal]) -> list[Trade]:
        """Simulate trades for the given candles and signals."""
        trades: list[Trade] = []
        trade_id = 1
        current: Trade | None = None
        balance = self.sizing.initial_balance

        for candle_idx, candle in enumerate(candles):
            if current is not None and self._try_exit(current, candle, candle_idx, balance):
                balance += current.pnl_abs or 0.0
                trades.append(current)
                current = None
                continue

            if current is None:
                current = self._try_entry(candle_idx, candle, signals, balance, trade_id)
                if current is not None:
                    trade_id += 1

        # Position still open at the end of the period
        if current is not None:
            trades.append(current)

        return trades

    def _try_exit(self, trade: Trade, candle: Candle, candle_idx: int, balance: float) -> bool:
        """Close the trade if TP or SL was touched by this candle."""
        if trade.is_long():
            hit_tp = candle.high >= trade.tp_level
            hit_sl = candle.low <= trade.sl_level
        else:
            hit_tp = candle.low <= trade.tp_level
            hit_sl = candle.high >= trade.sl_level

        if hit_tp:
            exit_type, exit_price = "TP", trade.tp_level
        elif hit_sl:
            exit_type, exit_price = "SL", trade.sl_level
        else:
            return False

        trade.exit_time = candle.open_time
        trade.exit_index = candle_idx
        trade.exit_price = exit_price
        trade.exit_type = exit_type
        self.on_close(trade, balance)

        if self.debug and self.logger:
            self.logger.debug(f"→ {trade} | Balance: {balance + (trade.pnl_abs or 0.0):.2f}")
        return True

    def _try_entry(
        self,
        candle_idx: int,
        candle: Candle,
        signals: list[Signal],
        balance: float,
        trade_id: int,
    ) -> Trade | None:
        """Open a trade on the candle that follows the signal candle."""
        for signal in signals:
            if signal.candle_index != candle_idx - 1:
                continue
            if signal.signal_type == "BEARISH" and not self.allow_short:
                continue
            if self.entry_filter is not None and not self.entry_filter(candle_idx, candle, signal):
                continue

            entry_price = candle.open
            position_size = self.sizing.position_size(balance)
            tp_level, sl_level = self.exit_rules.levels(
                entry_price, is_long=signal.signal_type == "BULLISH"
            )

            trade = Trade(
                trade_id=trade_id,
                entry_time=candle.open_time,
                entry_index=candle_idx,
                entry_price=entry_price,
                signal_type=signal.signal_type,
                tp_level=tp_level,
                sl_level=sl_level,
                quantity=position_size,
            )

            if self.debug and self.logger:
                self.logger.debug(
                    f"ENTRY #{trade.trade_id}: {trade.signal_type} @ {entry_price:.2f} "
                    f"(SIZE: {position_size:.0f}₽)"
                )
            return trade

        return None
