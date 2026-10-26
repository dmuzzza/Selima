"""Single trade simulation engine shared by all strategies.

The engine replays signals over candles and applies exit rules (TP/SL). It is
deliberately small: strategies provide signals and, optionally, an entry filter;
everything related to position sizing, exits and PnL is handled here once.

Known limitations, kept as-is to preserve historical backtest results and to be
addressed separately (T3-5):

- one position at a time (no pyramiding, no parallel positions);
- when a single candle touches both TP and SL, SL wins (pessimistic assumption:
  the intrabar order of events is unknown, so the worse outcome is reported);
- exit time is the close of the candle that triggered the exit (open time plus
  `bar_duration`), not the moment the level was actually touched;
- gaps are handled at the candle open only: if the candle opens beyond a level,
  the fill is the open price, not the level; intrabar slippage is not modelled.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

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
        bar_duration: timedelta | None = None,
        logger=None,
        debug: bool = False,
    ) -> None:
        self.exit_rules = exit_rules
        self.sizing = sizing
        self.on_close = on_close
        self.entry_filter = entry_filter
        self.allow_short = allow_short
        # Duration of one candle: exit_time is reported as the close of the bar
        # that triggered the exit. None keeps the raw candle open time.
        self.bar_duration = bar_duration
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
        """Close the trade if TP or SL was reached by this candle."""
        exit = self._detect_exit(trade, candle)
        if exit is None:
            return False

        exit_type, exit_price = exit
        trade.exit_time = self._exit_time(candle)
        trade.exit_index = candle_idx
        trade.exit_price = exit_price
        trade.exit_type = exit_type
        self.on_close(trade, balance)

        if self.debug and self.logger:
            self.logger.debug(f"→ {trade} | Balance: {balance + (trade.pnl_abs or 0.0):.2f}")
        return True

    def _exit_time(self, candle: Candle):
        """Exit timestamp: the close of the bar that triggered the exit.

        OHLC data does not tell when inside the bar the level was reached, so the
        end of the bar is used (conservative: hold time is never understated).
        """
        if self.bar_duration is None:
            return candle.open_time
        return candle.open_time + self.bar_duration

    def _detect_exit(self, trade: Trade, candle: Candle) -> tuple[str, float] | None:
        """Return (exit_type, exit_price) if the candle closed the trade.

        Two cases are distinguished:

        1. Gap: the candle opened beyond a level. That level was never traded,
           so the fill is the open price (worse than the level for SL, better
           for TP). SL is checked first to stay pessimistic.
        2. Intrabar touch: the level was reached inside the candle. When both
           levels are reached, SL wins because the intrabar order is unknown.
        """
        is_long = trade.is_long()

        # 1. Gap at the candle open
        if is_long:
            if candle.open <= trade.sl_level:
                return "SL", candle.open
            if candle.open >= trade.tp_level:
                return "TP", candle.open
        else:
            if candle.open >= trade.sl_level:
                return "SL", candle.open
            if candle.open <= trade.tp_level:
                return "TP", candle.open

        # 2. Intrabar touch
        if is_long:
            hit_tp = candle.high >= trade.tp_level
            hit_sl = candle.low <= trade.sl_level
        else:
            hit_tp = candle.low <= trade.tp_level
            hit_sl = candle.high >= trade.sl_level

        if hit_sl:
            return "SL", trade.sl_level
        if hit_tp:
            return "TP", trade.tp_level
        return None

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
