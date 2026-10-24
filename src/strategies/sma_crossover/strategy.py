"""
SMA Crossover Strategy V2

Стратегия пересечения цены через SMA50.
Адаптирована для работы с BaseStrategy.

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import List, Optional

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal
from .signal_validator import create_default_validator, create_strict_validator, create_custom_validator

from .config import get_config

logger = logging.getLogger(__name__)


class SMACrossoverStrategy(BaseStrategy):
    """
    Стратегия пересечения цены через SMA50.
    
    ЛОГИКА:
    - Сигнал BULLISH: Close пересекает SMA снизу вверх
    - Сигнал BEARISH: Close пересекает SMA сверху вниз
    - Вход: По цене Open следующей свечи
    - Выход: По TP или SL
    """

    def __init__(self, timeframe: str, config: dict = None, debug: bool = False):
        """
        Инициализация SMA Crossover стратегии.
        
        Args:
            timeframe: Таймфрейм (e.g., "1h", "4h", "1d")
            config: Кастомная конфигурация (опционально)
            debug: Режим отладки
        """
        # Загружаем конфиг для таймфрейма
        strategy_config = get_config(timeframe, config)
        
        # Инициализируем базовый класс
        super().__init__(timeframe, strategy_config, debug)
        
        # SMA параметры
        self.sma_period = self.config.get("sma_period", 50)
        
        # TP/SL параметры
        self.tp_percent = self.config.get("tp_percent", 1.5)
        self.sl_percent = self.config.get("sl_percent", 1.0)
        
        if self.debug:
            self.logger.debug(
                f"SMA Crossover initialized: period={self.sma_period}, "
                f"TP={self.tp_percent}%, SL={self.sl_percent}%"
            )
        

        # ✅ ИНИЦИАЛИЗАЦИЯ ВАЛИДАТОРА
        validation_config = self.config.get("validation", {})
        self.validation_enabled = validation_config.get("enabled", True)

        # ✅ ФИЛЬТР ТРЕНДА
        trend_config = self.config.get("trend_filter", {})
        self.trend_filter_enabled = trend_config.get("enabled", False)
        self.trend_sma_period = trend_config.get("sma_period", 200)

        if self.debug and self.trend_filter_enabled:
            self.logger.debug(f"Trend filter enabled: SMA{self.trend_sma_period}")
        
        if self.validation_enabled:
            mode = validation_config.get("mode", "default")
            
            if mode == "strict":
                self.validator = create_strict_validator(debug=self.debug)
            elif mode == "custom":
                custom = validation_config.get("custom_rules", {})
                self.validator = create_custom_validator(
                    candle_direction=custom.get("candle_direction", True),
                    confirmation=custom.get("confirmation", True),
                    min_distance=custom.get("min_breakout_distance", 0.2),
                    debug=self.debug
                )
            else:  # default
                self.validator = create_default_validator(debug=self.debug)
            
            if self.debug:
                self.logger.debug(f"Validation enabled (mode={mode})")
                self.validator.list_rules()

    # ═══════════════════════════════════════════════════════════════════════
    # СВОЙСТВА СТРАТЕГИИ
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def name(self) -> str:
        """Имя стратегии для UI"""
        return "SMA Crossover"

    @property
    def description(self) -> str:
        """Описание стратегии"""
        return f"Price crossing SMA{self.sma_period} strategy"

    # ═══════════════════════════════════════════════════════════════════════
    # РАСЧЁТ SMA
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_sma_series(self, candles: List[Candle], period: int = None) -> List[Optional[float]]:
        """
        Рассчитать SMA для всех свечей.
        
        Args:
            candles: Список свечей
        
        Returns:
            List[float | None]: Массив SMA (None если недостаточно данных)
        """
        period = period or self.sma_period  # ← если не передан — берём дефолтный

        if len(candles) < period:
            return [None] * len(candles)

        sma_series = [None] * len(candles)

        for i in range(period - 1, len(candles)):
            window = candles[i - period + 1 : i + 1]
            closes = [c.close for c in window]
            sma_series[i] = sum(closes) / len(closes)

        return sma_series

    # ═══════════════════════════════════════════════════════════════════════
    # ДЕТЕКТИРОВАНИЕ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════════

    def detect_signals(self, candles: List[Candle]) -> List[Signal]:
        """
        Детектировать пересечения SMA.
        
        BULLISH: Close[i-1] <= SMA[i-1] AND Close[i] > SMA[i]
        BEARISH: Close[i-1] >= SMA[i-1] AND Close[i] < SMA[i]
        
        Args:
            candles: Список свечей
            
        Returns:
            List[Signal]: Найденные сигналы
        """
        if len(candles) < self.sma_period + 1:
            self.logger.warning(
                f"Not enough candles: {len(candles)} < {self.sma_period + 1}"
            )
            return []
        
        # Расчёт SMA для всех свечей
        sma_series = self.calculate_sma_series(candles)
        # Расчёт SMA тренда (SMA200)
        trend_sma_series = (
            self.calculate_sma_series(candles, self.trend_sma_period)
            if self.trend_filter_enabled
            else None
        )
        
        signals = []
        rejected_count = 0  # Счётчик отклонённых сигналов
        
        for i in range(self.sma_period, len(candles)):
            curr_candle = candles[i]
            prev_candle = candles[i - 1]
            
            curr_close = curr_candle.close
            prev_close = prev_candle.close
            
            curr_sma = sma_series[i]
            prev_sma = sma_series[i - 1]
            
            if curr_sma is None or prev_sma is None:
                continue
            
            # ─────────────────────────────────────────────────────────
            # ДЕТЕКТИРОВАНИЕ ПЕРЕСЕЧЕНИЙ
            # ─────────────────────────────────────────────────────────
            
            signal = None
            
            # BULLISH: пересечение снизу вверх
            if prev_close <= prev_sma and curr_close > curr_sma:
                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BULLISH",
                    price=curr_close,
                    metadata={"sma_value": curr_sma}
                )
            
            # BEARISH: пересечение сверху вниз
            elif prev_close >= prev_sma and curr_close < curr_sma:
                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BEARISH",
                    price=curr_close,
                    metadata={"sma_value": curr_sma}
                )
            
            # ─────────────────────────────────────────────────────────
            # ✅ ВАЛИДАЦИЯ СИГНАЛА
            # ─────────────────────────────────────────────────────────
            
            if signal is not None:
                # ─────────────────────────────────────────────────
                # 📈 ФИЛЬТР ТРЕНДА
                # ─────────────────────────────────────────────────
                if self.trend_filter_enabled and trend_sma_series is not None:
                    trend_sma = trend_sma_series[i]

                    if trend_sma is not None:
                        is_above_trend = curr_close > trend_sma
                        is_below_trend = curr_close < trend_sma

                        if signal.signal_type == "BULLISH" and not is_above_trend:
                            if self.debug:
                                self.logger.debug(
                                    f"✗ TREND FILTER: BULLISH rejected, "
                                    f"close={curr_close:.2f} < SMA{self.trend_sma_period}={trend_sma:.2f}"
                                )
                            rejected_count += 1
                            continue  # пропускаем сигнал

                        elif signal.signal_type == "BEARISH" and not is_below_trend:
                            if self.debug:
                                self.logger.debug(
                                    f"✗ TREND FILTER: BEARISH rejected, "
                                    f"close={curr_close:.2f} > SMA{self.trend_sma_period}={trend_sma:.2f}"
                                )
                            rejected_count += 1
                            continue  # пропускаем сигнал

                # Если валидация включена, проверяем сигнал
                if self.validation_enabled:
                    is_valid = self.validator.validate(candles, signal, sma_series)
                    
                    if is_valid:
                        signals.append(signal)
                        if self.debug:
                            self.logger.debug(f"✓ ACCEPTED: {signal}")
                    else:
                        rejected_count += 1
                        if self.debug:
                            self.logger.debug(f"✗ REJECTED: {signal}")
                else:
                    # Валидация отключена → добавляем без проверки
                    signals.append(signal)
                    if self.debug:
                        self.logger.debug(f"✓ {signal}")
        
        # ─────────────────────────────────────────────────────────
        # ФИНАЛЬНАЯ СТАТИСТИКА
        # ─────────────────────────────────────────────────────────
        
        total_detected = len(signals) + rejected_count
        acceptance_rate = (len(signals)/total_detected*100) if total_detected > 0 else 0.0
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




    # Симуляция торговли (TP/SL, размер позиции, PnL) выполняется общим движком
    # `TradeSimulator` через BaseStrategy.execute_trades(). Стратегия отвечает
    # только за сигналы (detect_signals) и, при необходимости, за entry_filter.


