"""
Candle Patterns Strategy

Стратегия на основе свечных паттернов.
Адаптирована под архитектуру BaseStrategy.

═══════════════════════════════════════════════════════════════════════════
ЛОГИКА:
═══════════════════════════════════════════════════════════════════════════

1. Проходит окном (pattern_window_size) по всем свечам
2. Каждый включённый детектор проверяет окно на наличие паттерна
3. Найденный паттерн → Signal (BULLISH/BEARISH)
4. После валидации и тренд-фильтра → Trade с TP/SL
5. Вход: По цене Open следующей свечи после сигнала
6. Выход: По TP или SL

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import List, Optional

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal, Trade

from .config import get_config
from .signal_validator import (
    create_default_validator,
    create_strict_validator,
    create_custom_validator,
)
from .patterns import get_enabled_detectors
from .patterns.base import PatternEvent

logger = logging.getLogger(__name__)


class CandlePatternStrategy(BaseStrategy):
    """
    Стратегия на основе свечных паттернов.

    ЛОГИКА:
    - Проходит окном по всем свечам
    - Каждый включённый детектор проверяет окно на наличие паттерна
    - Найденный паттерн → Signal (BULLISH/BEARISH)
    - После валидации и тренд-фильтра → Trade с TP/SL
    """

    def __init__(self, timeframe: str, config: dict = None, debug: bool = False):
        """
        Инициализация стратегии свечных паттернов.

        Args:
            timeframe: Таймфрейм (e.g., "1h", "4h", "1d")
            config: Кастомная конфигурация (опционально)
            debug: Режим отладки
        """
        # Загружаем конфиг для таймфрейма
        strategy_config = get_config(timeframe, config)

        # Инициализируем базовый класс
        super().__init__(timeframe, strategy_config, debug)

        # TP/SL параметры
        self.tp_percent = self.config.get("tp_percent", 1.5)
        self.sl_percent = self.config.get("sl_percent", 1.0)

        # Размер окна для анализа паттернов (свечей)
        self.pattern_window_size = self.config.get("pattern_window_size", 3)

        # ─────────────────────────────────────────────────
        # ИНИЦИАЛИЗАЦИЯ ДЕТЕКТОРОВ ПАТТЕРНОВ
        # ─────────────────────────────────────────────────
        patterns_config = self.config.get("patterns", {})
        self.pattern_detectors = get_enabled_detectors(patterns_config)

        if self.debug:
            self.logger.debug(
                f"Loaded {len(self.pattern_detectors)} pattern detectors: "
                f"{[d.pattern_code for d in self.pattern_detectors]}"
            )

        # ─────────────────────────────────────────────────
        # ИНИЦИАЛИЗАЦИЯ ВАЛИДАТОРА
        # ─────────────────────────────────────────────────
        validation_config = self.config.get("validation", {})
        self.validation_enabled = validation_config.get("enabled", True)

        if self.validation_enabled:
            mode = validation_config.get("mode", "default")

            if mode == "strict":
                self.validator = create_strict_validator(debug=self.debug)
            elif mode == "custom":
                custom = validation_config.get("custom_rules", {})
                self.validator = create_custom_validator(
                    candle_direction=custom.get("candle_direction", True),
                    confirmation=custom.get("confirmation", True),
                    min_distance=custom.get("min_breakout_distance", 0.1),
                    debug=self.debug
                )
            else:  # default
                self.validator = create_default_validator(debug=self.debug)

            if self.debug:
                self.logger.debug(f"Validation enabled (mode={mode})")
                self.validator.list_rules()

        # ─────────────────────────────────────────────────
        # ФИЛЬТР ТРЕНДА
        # ─────────────────────────────────────────────────
        trend_config = self.config.get("trend_filter", {})
        self.trend_filter_enabled = trend_config.get("enabled", False)
        self.trend_sma_period = trend_config.get("sma_period", 200)

        if self.debug and self.trend_filter_enabled:
            self.logger.debug(f"Trend filter enabled: SMA{self.trend_sma_period}")

    # ═══════════════════════════════════════════════════════════════════════
    # СВОЙСТВА СТРАТЕГИИ
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def name(self) -> str:
        """Имя стратегии для UI"""
        return "Candle Patterns"

    @property
    def description(self) -> str:
        """Описание стратегии"""
        enabled = [d.pattern_code for d in self.pattern_detectors]
        return f"Candlestick patterns strategy: {', '.join(enabled) if enabled else 'none enabled'}"

    # ═══════════════════════════════════════════════════════════════════════
    # ВСПОМОГАТЕЛЬНЫЙ МЕТОД: РАСЧЁТ SMA (для тренд-фильтра)
    # ═══════════════════════════════════════════════════════════════════════

    def _calculate_sma_series(self, candles: List[Candle], period: int) -> List[Optional[float]]:
        """
        Рассчитать SMA для всех свечей.

        Args:
            candles: Список свечей
            period: Период SMA

        Returns:
            List[float | None]: Массив SMA (None если недостаточно данных)
        """
        if len(candles) < period:
            return [None] * len(candles)

        sma_series = [None] * len(candles)

        for i in range(period - 1, len(candles)):
            window = candles[i - period + 1: i + 1]
            closes = [c.close for c in window]
            sma_series[i] = sum(closes) / len(closes)

        return sma_series

    # ═══════════════════════════════════════════════════════════════════════
    # ДЕТЕКТИРОВАНИЕ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════════

    def detect_signals(self, candles: List[Candle]) -> List[Signal]:
        """
        Детектировать свечные паттерны на всей истории.

        Проходит окном (pattern_window_size) по всем свечам,
        применяет каждый включённый детектор паттернов.

        Args:
            candles: Список свечей

        Returns:
            List[Signal]: Найденные сигналы
        """
        min_required = self.pattern_window_size

        if len(candles) < min_required:
            self.logger.warning(
                f"Not enough candles: {len(candles)} < {min_required}"
            )
            return []

        if not self.pattern_detectors:
            self.logger.warning("No pattern detectors enabled!")
            return []

        # Расчёт SMA для тренд-фильтра (если включен)
        trend_sma_series = (
            self._calculate_sma_series(candles, self.trend_sma_period)
            if self.trend_filter_enabled
            else None
        )

        signals = []
        rejected_count = 0

        # Проходим окном по всем свечам
        for i in range(min_required - 1, len(candles)):
            window = candles[i - (min_required - 1): i + 1]

            # ─────────────────────────────────────────────────
            # ПРИМЕНЯЕМ КАЖДЫЙ ДЕТЕКТОР ПАТТЕРНОВ
            # ─────────────────────────────────────────────────
            for detector in self.pattern_detectors:
                # Используем detect_with_context, чтобы фильтры (volume и др.)
                # имели доступ к полной истории свечей
                pattern_events = detector.detect_with_context(
                    full_candles=candles,
                    window=window,
                    candle_index=i,
                )

                for event in pattern_events:
                    # Пропускаем нейтральные паттерны (не дают торгового сигнала)
                    if event.signal_type == "NEUTRAL":
                        if self.debug:
                            self.logger.debug(f"ℹ NEUTRAL pattern skipped: {event.name}")
                        continue

                    signal = Signal(
                        timestamp=event.timestamp,
                        candle_index=event.candle_index,
                        signal_type=event.signal_type,  # "BULLISH" | "BEARISH"
                        price=event.price,
                        metadata={
                            "pattern_name": event.name,
                            "pattern_code": event.pattern_code,
                            "pattern_metadata": event.metadata,
                        }
                    )

                    # ─────────────────────────────────────────
                    # 📈 ФИЛЬТР ТРЕНДА
                    # ─────────────────────────────────────────
                    if self.trend_filter_enabled and trend_sma_series is not None:
                        trend_sma = trend_sma_series[i]

                        if trend_sma is not None:
                            curr_close = candles[i].close
                            is_above_trend = curr_close > trend_sma
                            is_below_trend = curr_close < trend_sma

                            if signal.signal_type == "BULLISH" and not is_above_trend:
                                rejected_count += 1
                                if self.debug:
                                    self.logger.debug(
                                        f"✗ TREND FILTER: {event.name} BULLISH rejected, "
                                        f"close={curr_close:.2f} < SMA{self.trend_sma_period}={trend_sma:.2f}"
                                    )
                                continue

                            elif signal.signal_type == "BEARISH" and not is_below_trend:
                                rejected_count += 1
                                if self.debug:
                                    self.logger.debug(
                                        f"✗ TREND FILTER: {event.name} BEARISH rejected, "
                                        f"close={curr_close:.2f} > SMA{self.trend_sma_period}={trend_sma:.2f}"
                                    )
                                continue

                    # ─────────────────────────────────────────
                    # 🔍 ВАЛИДАЦИЯ СИГНАЛА
                    # ─────────────────────────────────────────
                    if self.validation_enabled:
                        is_valid = self.validator.validate(candles, signal)

                        if is_valid:
                            signals.append(signal)
                            if self.debug:
                                self.logger.debug(f"✓ ACCEPTED: {signal} ({event.name})")
                        else:
                            rejected_count += 1
                            if self.debug:
                                self.logger.debug(f"✗ REJECTED: {signal} ({event.name})")
                    else:
                        # Валидация отключена → добавляем без проверки
                        signals.append(signal)
                        if self.debug:
                            self.logger.debug(f"✓ {signal} ({event.name})")

        # ─────────────────────────────────────────────────────────
        # ФИНАЛЬНАЯ СТАТИСТИКА
        # ─────────────────────────────────────────────────────────

        total_detected = len(signals) + rejected_count
        acceptance_rate = (len(signals) / total_detected * 100) if total_detected > 0 else 0.0
        if self.validation_enabled:
            self.logger.info(
                f"Detected {total_detected} raw signals → "
                f"{len(signals)} accepted, {rejected_count} rejected "
                f"(acceptance rate: {acceptance_rate:.1f}%)"
            )
        else:
            self.logger.info(f"Detected {len(signals)} signals (validation disabled)")

        self.signals = signals
        return signals

    # ═══════════════════════════════════════════════════════════════════════
    # ПРОВЕРКА ПОСЛЕДНЕЙ СВЕЧИ (для live-торговли)
    # ═══════════════════════════════════════════════════════════════════════

    def check_last_signal(self, candles: List[Candle]) -> Optional[Signal]:
        """
        Проверить, есть ли паттерн на ПОСЛЕДНЕЙ ЗАКРЫТОЙ свече.

        Используется для live-торговли в планировщике.
        Анализирует только последние свечи, а не всю историю.

        Args:
            candles: Список свечей (минимум pattern_window_size)

        Returns:
            Signal если найден паттерн на последней свече, иначе None
        """
        min_required = self.pattern_window_size

        if len(candles) < min_required:
            self.logger.warning(
                f"Not enough candles for last signal check: {len(candles)} < {min_required}"
            )
            return None

        if not self.pattern_detectors:
            return None

        # Берём последние N свечей
        window = candles[-min_required:]
        last_idx = len(candles) - 1

        for detector in self.pattern_detectors:
            pattern_events = detector.detect_with_context(
                full_candles=candles,
                window=window,
                candle_index=last_idx,
            )

            for event in pattern_events:
                if event.signal_type == "NEUTRAL":
                    continue

                signal = Signal(
                    timestamp=event.timestamp,
                    candle_index=event.candle_index,
                    signal_type=event.signal_type,
                    price=event.price,
                    metadata={
                        "pattern_name": event.name,
                        "pattern_code": event.pattern_code,
                    }
                )

                # ─────────────────────────────────────────
                # ВАЛИДАЦИЯ СИГНАЛА
                # ─────────────────────────────────────────

                if self.validation_enabled:
                    is_valid = self.validator.validate(candles, signal)
                    if is_valid:
                        if self.debug:
                            self.logger.debug(f"✓ Last signal ACCEPTED: {signal}")
                        return signal
                    else:
                        if self.debug:
                            self.logger.debug(f"✗ Last signal REJECTED by validation")
                        return None
                else:
                    if self.debug:
                        self.logger.debug(f"✓ Last signal: {signal}")
                    return signal

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # СИМУЛЯЦИЯ ТОРГОВЛИ
    # ═══════════════════════════════════════════════════════════════════════

    def execute_trades(self, candles: List[Candle]) -> List[Trade]:
        """
        Симулировать торговлю на основе сигналов.

        Логика идентична SMA Crossover:
        - Вход на Open следующей свечи после сигнала
        - Выход по TP или SL
        - TP/SL проверяются на каждой свече

        Args:
            candles: Список свечей

        Returns:
            List[Trade]: Список сделок
        """
        if not self.signals:
            self.logger.warning("No signals to execute.")
            return []

        trades = []
        trade_id = 1
        current_trade: Optional[Trade] = None
        current_balance = self.initial_balance

        for candle_idx, candle in enumerate(candles):
            # ───────────────────────────────────────────────────────────────
            # ПРОВЕРКА TP/SL ДЛЯ ОТКРЫТОЙ ПОЗИЦИИ
            # ───────────────────────────────────────────────────────────────

            if current_trade is not None:
                # LONG: проверяем TP на High, SL на Low
                if current_trade.is_long():
                    if candle.high >= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                    if candle.low <= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                # SHORT: проверяем TP на Low, SL на High
                elif current_trade.is_short():
                    if candle.low <= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                    if candle.high >= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

            # ───────────────────────────────────────────────────────────────
            # ПРОВЕРКА НОВЫХ СИГНАЛОВ
            # ───────────────────────────────────────────────────────────────

            if current_trade is None:
                for signal in self.signals:
                    # Вход на следующей свече после сигнала
                    if signal.candle_index == candle_idx - 1:
                        entry_price = candle.open
                        position_size = current_balance

                        if signal.signal_type == "BULLISH":
                            tp_level = entry_price * (1 + self.tp_percent / 100.0)
                            sl_level = entry_price * (1 - self.sl_percent / 100.0)
                        else:
                            tp_level = entry_price * (1 - self.tp_percent / 100.0)
                            sl_level = entry_price * (1 + self.sl_percent / 100.0)

                        current_trade = Trade(
                            trade_id=trade_id,
                            entry_time=candle.open_time,
                            entry_index=candle_idx,
                            entry_price=entry_price,
                            signal_type=signal.signal_type,
                            tp_level=tp_level,
                            sl_level=sl_level,
                            quantity=position_size,
                            pattern_name=signal.metadata.get("pattern_name"),
                        )

                        trade_id += 1

                        if self.debug:
                            self.logger.debug(
                                f"ENTRY #{current_trade.trade_id}: {current_trade.signal_type} "
                                f"@ {entry_price:.2f} (SIZE: {position_size:.0f}₽) "
                                f"[{signal.metadata.get('pattern_name', '?')}]"
                            )
                        break

        # Добавляем открытую позицию (если осталась незакрытой)
        if current_trade is not None:
            trades.append(current_trade)

        self.trades = trades
        self.logger.info(
            f"Executed {len(trades)} trades "
            f"({len([t for t in trades if not t.is_open()])} closed)"
        )
        return trades

    # ═══════════════════════════════════════════════════════════════════════
    # РЕАЛЬНАЯ ТОРГОВЛЯ (не симуляция)
    # ═══════════════════════════════════════════════════════════════════════

    def execute_live_trades(
        self,
        orchestrator,
        ticker: str,
        figi: str
    ) -> List:
        """
        Выполнить реальную торговлю на основе сигналов.

        Используется вместо execute_trades() для реальной торговли.

        Args:
            orchestrator: TradingOrchestrator для исполнения сделок
            ticker: Тикер инструмента (e.g., "SBER")
            figi: FIGI код инструмента

        Returns:
            List[LiveTrade]: Список созданных реальных сделок
        """
        if not self.signals:
            self.logger.warning("No signals to execute live.")
            return []

        self.logger.info(
            f"🚀 Executing LIVE trades: {len(self.signals)} signals for {ticker}"
        )

        # Обрабатываем все сигналы через оркестратор
        live_trades = orchestrator.process_signals(
            signals=self.signals,
            ticker=ticker,
            figi=figi,
            tp_percent=self.tp_percent,
            sl_percent=self.sl_percent
        )

        self.logger.info(
            f"✅ Live trading completed: {len(live_trades)} trades executed"
        )

        return live_trades
