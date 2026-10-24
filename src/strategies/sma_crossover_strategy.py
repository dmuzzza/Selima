"""
SMA Crossover Strategy V1

Стратегия пересечения цены (Close свечи) линией SMA.

ЛОГИКА:
- Сигнал BULLISH: Close текущей свечи пересекает SMA снизу вверх
- Сигнал BEARISH: Close текущей свечи пересекает SMA сверху вниз
- Вход: По цене открытия следующей свечи
- Выход: По TP или SL (в процентах от цены входа)
- Позиции: Не наслаиваются (ждём закрытия предыдущей)

═══════════════════════════════════════════════════════════════════════════
DATA STRUCTURES:
═══════════════════════════════════════════════════════════════════════════

Signal:
    timestamp: datetime
    candle_index: int
    signal_type: str ("BULLISH" | "BEARISH")
    price: float (close свечи, где произошло пересечение)
    sma_value: float

Trade:
    trade_id: int
    entry_time: datetime
    entry_index: int
    entry_price: float
    signal_type: str ("BULLISH" -> LONG, "BEARISH" -> SHORT)
    
    exit_time: datetime | None
    exit_index: int | None
    exit_price: float | None
    exit_type: str | None ("TP" | "SL")
    
    tp_level: float
    sl_level: float
    
    pnl_percent: float | None
    pnl_abs: float | None
    quantity: float

StrategyStats:
    total_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    total_pnl: float
    final_balance: float

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict

from src.market_data.models import Candle
from src.strategies.sma_crossover_config import (
    get_sma_period,
    get_tp_sl,
    get_initial_balance,
    get_position_size_percent,
    get_breakout_config,  # 🔑 НОВОЕ
    is_breakout_enabled,  # 🔑 НОВОЕ
    get_breakout_rules,   # 🔑 НОВОЕ
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 📊 DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Signal:
    """Сигнал пересечения SMA"""

    timestamp: datetime
    candle_index: int
    signal_type: str  # "BULLISH" | "BEARISH"
    price: float  # Close свечи с сигналом
    sma_value: float

    def __repr__(self):
        return f"Signal({self.signal_type} @ idx={self.candle_index}, price={self.price:.2f}, sma={self.sma_value:.2f})"


@dataclass
class Trade:
    """Торговая сделка"""

    trade_id: int
    entry_time: datetime
    entry_index: int
    entry_price: float
    signal_type: str  # "BULLISH" -> LONG, "BEARISH" -> SHORT

    tp_level: float
    sl_level: float
    quantity: float

    # Выход
    exit_time: Optional[datetime] = None
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_type: Optional[str] = None  # "TP" | "SL"

    pnl_percent: Optional[float] = None
    pnl_abs: Optional[float] = None

    def is_open(self) -> bool:
        """Открыта ли сделка?"""
        return self.exit_price is None

    def is_long(self) -> bool:
        """Это LONG позиция?"""
        return self.signal_type == "BULLISH"

    def is_short(self) -> bool:
        """Это SHORT позиция?"""
        return self.signal_type == "BEARISH"

    def __repr__(self):
        status = "OPEN" if self.is_open() else f"CLOSED ({self.exit_type})"
        direction = "LONG" if self.is_long() else "SHORT"
        pnl_str = f"{self.pnl_percent:+.2f}%" if self.pnl_percent else "N/A"
        return (
            f"Trade#{self.trade_id} {direction} {status} | "
            f"Entry: {self.entry_price:.2f} | Exit: {self.exit_price or 'N/A'} | "
            f"PnL: {pnl_str}"
        )


@dataclass
class StrategyStats:
    """Статистика стратегии"""

    total_trades: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    avg_win: float = 0.0
    avg_loss: float = 0.0

    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    final_balance: float = 0.0

    def __repr__(self):
        return (
            f"StrategyStats:\n"
            f"  Total trades: {self.total_trades}\n"
            f"  Closed trades: {self.closed_trades}\n"
            f"  Winning: {self.winning_trades} | Losing: {self.losing_trades}\n"
            f"  Win rate: {self.win_rate:.2f}%\n"
            f"  Avg win: {self.avg_win:.2f}% | Avg loss: {self.avg_loss:.2f}%\n"
            f"  Total PnL: {self.total_pnl:.2f}₽ ({self.total_pnl_percent:+.2f}%)\n"
            f"  Final balance: {self.final_balance:.2f}₽"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 🎯 SMA CROSSOVER STRATEGY
# ═══════════════════════════════════════════════════════════════════════════


class SMACrossoverStrategy:
    """
    Стратегия пересечения цены через SMA.

    Основной флоу:
    1. detect_signals() — найти все пересечения SMA
    2. execute_trades() — симулировать торговлю
    3. calculate_stats() — вычислить статистику
    """

    def __init__(self, timeframe: str, debug: bool = False):
        """
        Инициализация стратегии.

        Args:
            timeframe: Таймфрейм (e.g., "1h", "4h", "1d")
            debug: Вывод debug логов
        """
        self.timeframe = timeframe
        self.debug = debug
        self.logger = logging.getLogger(__name__)

        # Параметры из конфига
        self.sma_period = get_sma_period()
        self.tp_sl_config = get_tp_sl(timeframe)
        self.initial_balance = get_initial_balance()
        self.position_size_percent = get_position_size_percent()

        # Сигналы и сделки
        self.signals: List[Signal] = []
        self.trades: List[Trade] = []
        self.stats: Optional[StrategyStats] = None

        if self.debug:
            self.logger.debug(f"Strategy initialized for {timeframe}")
            self.logger.debug(
                f"  SMA period: {self.sma_period}, TP: {self.tp_sl_config['tp_percent']}%, "
                f"SL: {self.tp_sl_config['sl_percent']}%"
            )

    # ═══════════════════════════════════════════════════════════════════════
    # 📈 РАСЧЁТ SMA И ДЕТЕКТИРОВАНИЕ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_sma_series(self, candles: List[Candle]) -> List[Optional[float]]:
        """
        Рассчитать SMA для всех свечей.

        Args:
            candles: Список свечей

        Returns:
            List[float | None]: Массив SMA (None если недостаточно данных)
        """
        if len(candles) < self.sma_period:
            return [None] * len(candles)

        sma_series = [None] * len(candles)

        for i in range(self.sma_period - 1, len(candles)):
            window = candles[i - self.sma_period + 1 : i + 1]
            closes = [c.close for c in window]
            sma_series[i] = sum(closes) / len(closes)

        return sma_series

    def detect_signals(self, candles: List[Candle]) -> List[Signal]:
        """
        Детектировать все пересечения SMA.

        BULLISH: Close[i-1] <= SMA[i-1] AND Close[i] > SMA[i]
        BEARISH: Close[i-1] >= SMA[i-1] AND Close[i] < SMA[i]

        Args:
            candles: Список свечей

        Returns:
            List[Signal]: Список найденных сигналов
        """
        if len(candles) < self.sma_period + 1:
            self.logger.warning(
                f"Not enough candles for signal detection ({len(candles)} < {self.sma_period + 1})"
            )
            return []

        sma_series = self.calculate_sma_series(candles)
        signals = []

        # Начинаем со второй свечи (нужны данные о предыдущей)
        for i in range(self.sma_period, len(candles)):
            curr_candle = candles[i]
            prev_candle = candles[i - 1]

            curr_close = curr_candle.close
            prev_close = prev_candle.close

            curr_sma = sma_series[i]
            prev_sma = sma_series[i - 1]

            if curr_sma is None or prev_sma is None:
                continue

            # BULLISH: пересечение снизу вверх
            if prev_close <= prev_sma and curr_close > curr_sma:
                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BULLISH",
                    price=curr_close,
                    sma_value=curr_sma,
                )
                signals.append(signal)
                if self.debug:
                    self.logger.debug(f"✓ {signal}")

            # BEARISH: пересечение сверху вниз
            elif prev_close >= prev_sma and curr_close < curr_sma:
                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BEARISH",
                    price=curr_close,
                    sma_value=curr_sma,
                )
                signals.append(signal)
                if self.debug:
                    self.logger.debug(f"✓ {signal}")

        self.signals = signals
        self.logger.info(f"Detected {len(signals)} signals for {self.timeframe}")
        return signals


    def validate_breakout(self, candles: List[Candle], signal: Signal) -> bool:
        """
        Валидировать пробой по правилам конфигурации.

        ЛОГИКА:
        - Свеча 1 (сигнал): должна быть направлению сигнала
        - Свеча 2 (после сигнала): должна подтвердить направление
        - Если любое условие не выполнено → False

        Args:
            candles: Список всех свечей
            signal: Сигнал для проверки

        Returns:
            bool: True если пробой валидный, False если нет
        """
        if not is_breakout_enabled():
            return True  # Если пробои отключены, всегда True

        rules = get_breakout_rules()
        candle_1_rules = rules.get("candle_1", {})
        candle_2_rules = rules.get("candle_2", {})

        # Индекс свечи с сигналом (это candle_1)
        candle_1_idx = signal.candle_index
        
        # Индекс подтверждающей свечи (candle_2 - следующая после сигнала)
        candle_2_idx = candle_1_idx + 1

        # Проверяем граничные индексы
        if candle_2_idx >= len(candles):
            if self.debug:
                self.logger.debug(f"Signal at idx {candle_1_idx}: не хватает свечи для подтверждения (candle_2)")
            return False

        candle_1 = candles[candle_1_idx]
        candle_2 = candles[candle_2_idx]

        # ─────────────────────────────────────────────────────────────────────
        # ПРОВЕРКА CANDLE_1 (свеча с сигналом)
        # ─────────────────────────────────────────────────────────────────────

        is_candle_1_bullish = candle_1.close > candle_1.open
        is_candle_1_bearish = candle_1.close < candle_1.open

        # Если BULLISH сигнал, свеча 1 должна быть бычья
        if signal.signal_type == "BULLISH":
            if candle_1_rules.get("must_be_bullish_for_bullish_signal", True):
                if not is_candle_1_bullish:
                    if self.debug:
                        self.logger.debug(
                            f"Signal BULLISH at idx {candle_1_idx}: свеча 1 не бычья "
                            f"(close {candle_1.close:.2f} <= open {candle_1.open:.2f})"
                        )
                    return False

        # Если BEARISH сигнал, свеча 1 должна быть медвежья
        elif signal.signal_type == "BEARISH":
            if candle_1_rules.get("must_be_bearish_for_bearish_signal", True):
                if not is_candle_1_bearish:
                    if self.debug:
                        self.logger.debug(
                            f"Signal BEARISH at idx {candle_1_idx}: свеча 1 не медвежья "
                            f"(close {candle_1.close:.2f} >= open {candle_1.open:.2f})"
                        )
                    return False

        # ─────────────────────────────────────────────────────────────────────
        # ПРОВЕРКА CANDLE_2 (подтверждающая свеча)
        # ─────────────────────────────────────────────────────────────────────

        is_candle_2_bullish = candle_2.close > candle_2.open
        is_candle_2_bearish = candle_2.close < candle_2.open

        if candle_2_rules.get("must_confirm_direction", True):
            # Для BULLISH: свеча 2 должна быть бычья
            if signal.signal_type == "BULLISH":
                if not is_candle_2_bullish:
                    if self.debug:
                        self.logger.debug(
                            f"Signal BULLISH at idx {candle_1_idx}: свеча 2 не подтверждает "
                            f"(close {candle_2.close:.2f} <= open {candle_2.open:.2f})"
                        )
                    return False

            # Для BEARISH: свеча 2 должна быть медвежья
            elif signal.signal_type == "BEARISH":
                if not is_candle_2_bearish:
                    if self.debug:
                        self.logger.debug(
                            f"Signal BEARISH at idx {candle_1_idx}: свеча 2 не подтверждает "
                            f"(close {candle_2.close:.2f} >= open {candle_2.open:.2f})"
                        )
                    return False

        if self.debug:
            self.logger.debug(
                f"✓ Breakout VALIDATED at idx {candle_1_idx}: "
                f"{signal.signal_type} (C1: {'bullish' if is_candle_1_bullish else 'bearish'}, "
                f"C2: {'bullish' if is_candle_2_bullish else 'bearish'})"
            )
        
        return True




    # ═══════════════════════════════════════════════════════════════════════
    # 💼 СИМУЛЯЦИЯ ТОРГОВЛИ
    # ═══════════════════════════════════════════════════════════════════════

    def execute_trades(self, candles: List[Candle]) -> List[Trade]:
        """
        Симулировать торговлю на основе сигналов.
        
        ЛОГИКА:
        1. Каждая сделка открывается на 100% от ТЕКУЩЕГО баланса
        2. После выхода баланс пересчитывается
        3. Размер позиции всегда = текущий баланс
        """
        if not self.signals:
            self.logger.warning("No signals to execute. Call detect_signals() first.")
            return []

        trades = []
        trade_id = 1
        current_trade: Optional[Trade] = None
        current_balance = self.initial_balance  # 🔑 Текущий баланс, меняется после каждой сделки

        tp_percent = self.tp_sl_config["tp_percent"]
        sl_percent = self.tp_sl_config["sl_percent"]

        # Перебираем все свечи
        for candle_idx, candle in enumerate(candles):
            # ─────────────────────────────────────────────────────────────
            # ПРОВЕРЯЕМ ОТКРЫТУЮ ПОЗИЦИЮ НА TP/SL
            # ─────────────────────────────────────────────────────────────

            if current_trade is not None:
                # Для LONG: проверяем High на TP, Low на SL
                if current_trade.is_long():
                    # TP
                    if candle.high >= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs  # 🔑 Обновляем баланс
                        trades.append(current_trade)
                        if self.debug:
                            self.logger.debug(f"  → {current_trade} | Balance: {current_balance:.2f}")
                        current_trade = None
                        continue

                    # SL
                    if candle.low <= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs  # 🔑 Обновляем баланс
                        trades.append(current_trade)
                        if self.debug:
                            self.logger.debug(f"  → {current_trade} | Balance: {current_balance:.2f}")
                        current_trade = None
                        continue

                # Для SHORT: проверяем Low на TP, High на SL
                elif current_trade.is_short():
                    # TP
                    if candle.low <= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs  # 🔑 Обновляем баланс
                        trades.append(current_trade)
                        if self.debug:
                            self.logger.debug(f"  → {current_trade} | Balance: {current_balance:.2f}")
                        current_trade = None
                        continue

                    # SL
                    if candle.high >= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs  # 🔑 Обновляем баланс
                        trades.append(current_trade)
                        if self.debug:
                            self.logger.debug(f"  → {current_trade} | Balance: {current_balance:.2f}")
                        current_trade = None
                        continue

            # ─────────────────────────────────────────────────────────────
            # ПРОВЕРЯЕМ НОВЫЕ СИГНАЛЫ И ОТКРЫВАЕМ ПОЗИЦИЮ
            # ─────────────────────────────────────────────────────────────

            if current_trade is None:
                # Ищем сигнал, который требует входа на этой свече
                for signal in self.signals:
                    # Сигнал был на предыдущей свече, входим на текущей по Open
                    if signal.candle_index == candle_idx - 1:
                        
                        # Сигнал был на предыдущей свече, входим на текущей по Open
                        entry_price = candle.open
                        position_size = current_balance  # 🔑 100% от ТЕКУЩЕГО баланса

                        if signal.signal_type == "BULLISH":
                            # LONG
                            tp_level = entry_price * (1 + tp_percent / 100.0)
                            sl_level = entry_price * (1 - sl_percent / 100.0)
                        else:
                            # SHORT
                            tp_level = entry_price * (1 - tp_percent / 100.0)
                            sl_level = entry_price * (1 + sl_percent / 100.0)

                        current_trade = Trade(
                            trade_id=trade_id,
                            entry_time=candle.open_time,
                            entry_index=candle_idx,
                            entry_price=entry_price,
                            signal_type=signal.signal_type,
                            tp_level=tp_level,
                            sl_level=sl_level,
                            quantity=position_size,  # 🔑 Вся позиция в рублях
                        )

                        trade_id += 1

                        if self.debug:
                            self.logger.debug(
                                f"ENTRY #{current_trade.trade_id}: {current_trade.signal_type} "
                                f"@ {entry_price:.2f} (SIZE: {position_size:.0f}₽) (TP: {tp_level:.2f}, SL: {sl_level:.2f})"
                            )
                        break

        # Добавляем открытую позицию (если есть)
        if current_trade is not None:
            trades.append(current_trade)
            if self.debug:
                self.logger.debug(f"⊘ Open trade: {current_trade}")

        self.trades = trades
        self.logger.info(
            f"Executed {len(trades)} trades ({len([t for t in trades if not t.is_open()])} closed)"
        )
        return trades

    def _calculate_pnl(self, trade: Trade, current_balance: float) -> None:
        """
        Рассчитать PnL для закрытой позиции.
        
        Args:
            trade: Торговая сделка
            current_balance: Баланс на момент входа (размер позиции)
        """
        if trade.exit_price is None:
            return

        if trade.is_long():
            # PnL в рублях = количество * (цена выхода - цена входа)
            # Количество акций = баланс / цена входа
            quantity = trade.quantity / trade.entry_price  # количество акций
            pnl_abs = quantity * (trade.exit_price - trade.entry_price)
            pnl_percent = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 100
        else:  # SHORT
            # Для short: прибыль когда цена падает
            quantity = trade.quantity / trade.entry_price
            pnl_abs = quantity * (trade.entry_price - trade.exit_price)
            pnl_percent = ((trade.entry_price - trade.exit_price) / trade.entry_price) * 100

        trade.pnl_percent = pnl_percent
        trade.pnl_abs = pnl_abs


    # ═══════════════════════════════════════════════════════════════════════
    # 📊 РАСЧЁТ СТАТИСТИКИ
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_stats(self) -> StrategyStats:
        """
        Рассчитать статистику стратегии.

        Returns:
            StrategyStats: Статистика торговли
        """
        if not self.trades:
            self.logger.warning("No trades to calculate stats.")
            return StrategyStats()

        closed_trades = [t for t in self.trades if not t.is_open()]
        winning_trades = [t for t in closed_trades if t.pnl_percent and t.pnl_percent > 0]
        losing_trades = [t for t in closed_trades if t.pnl_percent and t.pnl_percent <= 0]

        # 🔑 Сумма PnL по всем закрытым сделкам (в рублях)
        total_pnl = sum(t.pnl_abs for t in closed_trades if t.pnl_abs)
        total_pnl_percent = (total_pnl / self.initial_balance) * 100

        win_rate = (
            (len(winning_trades) / len(closed_trades) * 100)
            if closed_trades
            else 0.0
        )

        avg_win = (
            sum(t.pnl_percent for t in winning_trades if t.pnl_percent)
            / len(winning_trades)
            if winning_trades
            else 0.0
        )

        avg_loss = (
            sum(t.pnl_percent for t in losing_trades if t.pnl_percent)
            / len(losing_trades)
            if losing_trades
            else 0.0
        )

        final_balance = self.initial_balance + total_pnl

        stats = StrategyStats(
            total_trades=len(self.trades),
            closed_trades=len(closed_trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            final_balance=final_balance,
        )

        self.stats = stats
        self.logger.info(f"\n{stats}")
        return stats

    # ═══════════════════════════════════════════════════════════════════════
    # 🎯 ГЛАВНЫЙ МЕТОД: ЗАПУСК СТРАТЕГИИ
    # ═══════════════════════════════════════════════════════════════════════

    def run(self, candles: List[Candle]) -> Dict:
        """
        Запустить полный цикл стратегии.

        Args:
            candles: Список свечей для анализа

        Returns:
            Dict: {
                "signals": List[Signal],
                "trades": List[Trade],
                "stats": StrategyStats,
            }
        """
        self.logger.info(f"Running strategy on {len(candles)} candles...")

        signals = self.detect_signals(candles)
        trades = self.execute_trades(candles)
        stats = self.calculate_stats()

        result = {
            "signals": signals,
            "trades": trades,
            "stats": stats,
        }

        return result
