"""
Pattern-Specific Filters

Расширяемая система фильтров для детекторов свечных паттернов.
Каждый фильтр — независимый класс, наследующий PatternFilter.

═══════════════════════════════════════════════════════════════════════════
КАК ДОБАВИТЬ НОВЫЙ ФИЛЬТР:
═══════════════════════════════════════════════════════════════════════════

1. Создать класс-наследник PatternFilter
2. Реализовать apply(full_candles, window, candle_index, event) → FilterResult
3. Добавить секцию в config паттерна
4. Подключить в детекторе через _build_filters()

═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from src.market_data.models import Candle

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# РЕЗУЛЬТАТ ФИЛЬТРА
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FilterResult:
    """Результат применения фильтра."""
    is_valid: bool
    filter_name: str
    reason: Optional[str] = None

    def __repr__(self):
        if self.is_valid:
            return f"✓ {self.filter_name}: PASSED"
        return f"✗ {self.filter_name}: FAILED ({self.reason})"


# ═══════════════════════════════════════════════════════════════════════════
# БАЗОВЫЙ КЛАСС ФИЛЬТРА
# ═══════════════════════════════════════════════════════════════════════════

class PatternFilter(ABC):
    """
    Базовый класс для фильтра паттерна.

    Каждый фильтр:
    - Имеет уникальное имя (filter_name)
    - Может быть включён/выключен через конфиг (enabled)
    - Получает параметры из секции фильтра в конфиге паттерна
    - Проверяет одно конкретное условие
    """

    def __init__(self, name: str, config: dict = None, debug: bool = False):
        """
        Args:
            name: Уникальное имя фильтра (ключ в конфиге)
            config: Параметры фильтра из конфига паттерна
            debug: Режим отладки
        """
        self.name = name
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.debug = debug

    @abstractmethod
    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event: "PatternEvent",  # type: ignore
    ) -> FilterResult:
        """
        Применить фильтр к найденному паттерну.

        Args:
            full_candles: Полный массив свечей (для расчёта индикаторов)
            window: Окно свечей, в котором найден паттерн
            candle_index: Индекс последней свечи окна в full_candles
            event: Событие найденного паттерна

        Returns:
            FilterResult: Результат фильтрации
        """
        pass

    def __repr__(self):
        status = "ON" if self.enabled else "OFF"
        return f"{self.name} [{status}]"


# ═══════════════════════════════════════════════════════════════════════════
# КОНКРЕТНЫЕ ФИЛЬТРЫ
# ═══════════════════════════════════════════════════════════════════════════

class BodyRatioFilter(PatternFilter):
    """
    Фильтр соотношения тел свечей.

    Проверяет, что тело поглощающей свечи ≥ min_ratio × тела предыдущей.
    Используется для Engulfing и подобных двухсвечных паттернов.

    Config keys:
        min_ratio: float — минимальное соотношение тел (по умолчанию 1.5)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="body_ratio", config=config, debug=debug)
        self.min_ratio = self.config.get("min_ratio", 1.5)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        if len(window) < 2:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason="Need at least 2 candles for body ratio check"
            )

        prev = window[-2]
        curr = window[-1]

        prev_body = abs(prev.close - prev.open)
        curr_body = abs(curr.close - curr.open)

        if prev_body == 0:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason="Previous candle has zero body (doji)"
            )

        actual_ratio = curr_body / prev_body

        if actual_ratio < self.min_ratio:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason=(
                    f"Body ratio too low: {actual_ratio:.2f}x < {self.min_ratio}x "
                    f"(engulfing={curr_body:.4f}, prev={prev_body:.4f})"
                )
            )

        return FilterResult(is_valid=True, filter_name=self.name)


class CandleSizeFilter(PatternFilter):
    """
    Фильтр размера тела свечи паттерна.

    Проверяет, что размер тела текущей свечи (|close - open|)
    не меньше min_ratio × среднего размера тела за последние N свечей.

    Config keys:
        min_ratio: float — минимальное соотношение к среднему размеру тела (по умолчанию 1.5)
        lookback: int — сколько свечей назад для расчёта среднего размера тела (по умолчанию 100)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="candle_size", config=config, debug=debug)
        self.min_ratio = self.config.get("min_ratio", 1.5)
        self.lookback = self.config.get("lookback", 100)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        curr_candle = window[-1]
        curr_body = abs(curr_candle.close - curr_candle.open)

        if curr_body <= 0:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason="Current candle has zero body",
            )

        lookback_start = max(0, candle_index - self.lookback)
        lookback_end = candle_index
        lookback_candles = full_candles[lookback_start:lookback_end]

        bodies = [
            abs(c.close - c.open)
            for c in lookback_candles
            if abs(c.close - c.open) > 0
        ]

        if len(bodies) < max(5, self.lookback // 4):
            return FilterResult(is_valid=True, filter_name=self.name)

        avg_body = sum(bodies) / len(bodies)
        actual_ratio = curr_body / avg_body

        if actual_ratio < self.min_ratio:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason=(
                    f"Candle body too small: {actual_ratio:.2f}x < {self.min_ratio}x "
                    f"(body={curr_body:.4f}, avg={avg_body:.4f} over {len(bodies)} bars)"
                ),
            )

        if self.debug:
            logger.debug(
                f"Candle size filter passed: {actual_ratio:.2f}x "
                f"(body={curr_body:.4f}, avg={avg_body:.4f})"
            )

        return FilterResult(is_valid=True, filter_name=self.name)


class VolumeFilter(PatternFilter):
    """
    Фильтр объёма.

    Проверяет, что объём свечи паттерна ≥ min_ratio × средний объём
    за последние N свечей.

    Config keys:
        min_ratio: float — минимальное соотношение к среднему объёму (по умолчанию 1.5)
        lookback: int — сколько свечей назад для расчёта среднего объёма (по умолчанию 20)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="volume", config=config, debug=debug)
        self.min_ratio = self.config.get("min_ratio", 1.5)
        self.lookback = self.config.get("lookback", 20)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        curr_candle = window[-1]

        # Если у свечи нет объёма — пропускаем фильтр
        if curr_candle.volume is None or curr_candle.volume <= 0:
            if self.debug:
                logger.debug(f"Volume filter skipped: no volume data for candle at idx={candle_index}")
            return FilterResult(is_valid=True, filter_name=self.name)

        # Берём lookback свечей ДО текущей (не включая текущую)
        lookback_start = max(0, candle_index - self.lookback)
        lookback_end = candle_index  # не включая текущую
        lookback_candles = full_candles[lookback_start:lookback_end]

        if len(lookback_candles) < max(5, self.lookback // 4):
            # Недостаточно исторических данных — пропускаем
            return FilterResult(is_valid=True, filter_name=self.name)

        # Считаем средний объём
        volumes = [c.volume for c in lookback_candles if c.volume is not None and c.volume > 0]
        if not volumes:
            return FilterResult(is_valid=True, filter_name=self.name)

        avg_volume = sum(volumes) / len(volumes)
        actual_ratio = curr_candle.volume / avg_volume

        if actual_ratio < self.min_ratio:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason=(
                    f"Volume too low: {actual_ratio:.2f}x < {self.min_ratio}x "
                    f"(vol={curr_candle.volume:.0f}, avg={avg_volume:.0f} over {len(volumes)} bars)"
                )
            )

        if self.debug:
            logger.debug(
                f"Volume filter passed: {actual_ratio:.2f}x "
                f"(vol={curr_candle.volume:.0f}, avg={avg_volume:.0f})"
            )

        return FilterResult(is_valid=True, filter_name=self.name)


class MA100TrendFilter(PatternFilter):
    """
    Фильтр глобального тренда по MA100.

    Определяет направление MA(period) за последние N свечей по разности
    первого и последнего значения MA в окне.

    Правила:
        - Восходящий тренд (MA растёт) → пропускаем только LONG сигналы
        - Нисходящий тренд (MA падает) → пропускаем только SHORT сигналы
        - Боковик / неопределённость → сигнал отсекается

    Config keys:
        period: int — период скользящей средней (по умолчанию 100)
        lookback: int — сколько свечей назад анализировать направление MA (по умолчанию 30)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="ma_trend100", config=config, debug=debug)
        self.period = self.config.get("period", 100)
        self.lookback = self.config.get("lookback", 30)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        required = self.period + self.lookback - 1
        if candle_index < required:
            return FilterResult(is_valid=True, filter_name=self.name)

        start_idx = candle_index - required
        end_idx = candle_index + 1
        ma_window = full_candles[start_idx:end_idx]

        ma_values = self._calc_sma_series(ma_window, self.period)
        valid_values = [v for v in ma_values if v is not None]

        if len(valid_values) < 2:
            return FilterResult(is_valid=True, filter_name=self.name)

        ma_first = valid_values[0]
        ma_last = valid_values[-1]
        delta = ma_last - ma_first
        signal_type = event.signal_type

        if delta > 0:
            trend = "uptrend"
            if signal_type != "BULLISH":
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"MA{self.period} is rising over {self.lookback} bars: "
                        f"first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f}. "
                        f"Only LONG signals allowed."
                    ),
                )
        elif delta < 0:
            trend = "downtrend"
            if signal_type != "BEARISH":
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"MA{self.period} is declining over {self.lookback} bars: "
                        f"first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f}. "
                        f"Only SHORT signals allowed."
                    ),
                )
        else:
            return FilterResult(
                is_valid=False,
                filter_name=self.name,
                reason=(
                    f"MA{self.period} is flat over {self.lookback} bars: "
                    f"first={ma_first:.4f}, last={ma_last:.4f}. Signal rejected."
                ),
            )

        if self.debug:
            logger.debug(
                f"MA100 trend filter passed: {signal_type} in {trend} "
                f"(first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f})"
            )

        return FilterResult(is_valid=True, filter_name=self.name)

    def _calc_sma_series(
        self,
        candles: List[Candle],
        period: int,
    ) -> List[Optional[float]]:
        """Рассчитать SMA для каждой свечи в окне."""
        if len(candles) < period:
            return [None] * len(candles)

        result = [None] * len(candles)
        for i in range(period - 1, len(candles)):
            window = candles[i - period + 1: i + 1]
            result[i] = sum(c.close for c in window) / period

        return result


class MATrendFilter(PatternFilter):
    """
    Фильтр направления скользящей средней.

    Проверяет общее направление MA(period) за последние N свечей.
    Направление определяется по разности между первым и последним значением MA
    в окне lookback свечей: положительная разность = рост, отрицательная = падение.

    Для бычьих паттернов требуется, чтобы MA снижалась (перепроданность перед разворотом).
    Для медвежьих паттернов требуется, чтобы MA росла (перекупленность перед разворотом).

    Config keys:
        period: int — период скользящей средней (по умолчанию 9)
        lookback: int — сколько свечей назад анализировать направление MA (по умолчанию 15)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="ma_trend", config=config, debug=debug)
        self.period = self.config.get("period", 9)
        self.lookback = self.config.get("lookback", 15)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        # Нужно минимум period + lookback свечей до текущей включительно
        required = self.period + self.lookback - 1
        if candle_index < required:
            return FilterResult(is_valid=True, filter_name=self.name)

        start_idx = candle_index - required
        end_idx = candle_index + 1  # не включительно
        ma_window = full_candles[start_idx:end_idx]

        ma_values = self._calc_sma_series(ma_window, self.period)
        valid_values = [v for v in ma_values if v is not None]

        if len(valid_values) < 2:
            return FilterResult(is_valid=True, filter_name=self.name)

        ma_first = valid_values[0]
        ma_last = valid_values[-1]
        delta = ma_last - ma_first
        signal_type = event.signal_type

        if signal_type == "BULLISH":
            if delta >= 0:
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"MA{self.period} not declining over {self.lookback} bars: "
                        f"first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f}"
                    ),
                )

        elif signal_type == "BEARISH":
            if delta <= 0:
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"MA{self.period} not rising over {self.lookback} bars: "
                        f"first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f}"
                    ),
                )

        if self.debug:
            direction = "rising" if delta > 0 else "declining"
            logger.debug(
                f"MA trend filter passed: MA{self.period} is {direction} "
                f"(first={ma_first:.4f}, last={ma_last:.4f}, delta={delta:.4f})"
            )

        return FilterResult(is_valid=True, filter_name=self.name)

    def _calc_sma_series(
        self,
        candles: List[Candle],
        period: int,
    ) -> List[Optional[float]]:
        """Рассчитать SMA для каждой свечи в окне."""
        if len(candles) < period:
            return [None] * len(candles)

        result = [None] * len(candles)
        for i in range(period - 1, len(candles)):
            window = candles[i - period + 1: i + 1]
            result[i] = sum(c.close for c in window) / period

        return result


class RSIFilter(PatternFilter):
    """
    Фильтр RSI 14 — зональный.

    Пропускает сигнал только если RSI в экстремальной зоне:
    - BULLISH: RSI должен быть ≤ oversold_threshold (по умолчанию 30 — зона перепроданности)
    - BEARISH: RSI должен быть ≥ overbought_threshold (по умолчанию 70 — зона перекупленности)

    Если RSI между 30 и 70 — сигнал отсекается независимо от типа.

    Config keys:
        period: int — период RSI (по умолчанию 14)
        oversold_threshold: float — граница перепроданности для BULLISH (по умолчанию 30)
        overbought_threshold: float — граница перекупленности для BEARISH (по умолчанию 70)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        super().__init__(name="rsi", config=config, debug=debug)
        self.period = self.config.get("period", 14)
        self.oversold_threshold = self.config.get("oversold_threshold", 30)
        self.overbought_threshold = self.config.get("overbought_threshold", 70)

    def apply(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event,
    ) -> FilterResult:
        # Нужно period+1 свечей для расчёта RSI
        if len(full_candles) < self.period + 1:
            return FilterResult(is_valid=True, filter_name=self.name)

        rsi = self._calc_rsi(full_candles, candle_index)

        if rsi is None:
            return FilterResult(is_valid=True, filter_name=self.name)

        signal_type = event.signal_type

        if signal_type == "BULLISH":
            if rsi > self.oversold_threshold:
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"RSI not in oversold zone for BULLISH: {rsi:.1f} > {self.oversold_threshold} "
                        f"(RSI{self.period})"
                    ),
                )

        elif signal_type == "BEARISH":
            if rsi < self.overbought_threshold:
                return FilterResult(
                    is_valid=False,
                    filter_name=self.name,
                    reason=(
                        f"RSI not in overbought zone for BEARISH: {rsi:.1f} < {self.overbought_threshold} "
                        f"(RSI{self.period})"
                    ),
                )

        if self.debug:
            logger.debug(
                f"RSI filter passed: RSI{rsi:.1f} in zone [{signal_type}] "
                f"(oversold≤{self.oversold_threshold}, overbought≥{self.overbought_threshold})"
            )

        return FilterResult(is_valid=True, filter_name=self.name)

    def _calc_rsi(self, candles: List[Candle], index: int) -> Optional[float]:
        """
        Рассчитать RSI для свечи по индексу.

        Использует стандартную формулу Wilder's RSI.
        """
        if index < self.period:
            return None

        # Берём period+1 свечей: [index-period : index+1]
        window = candles[index - self.period: index + 1]

        gains = 0.0
        losses = 0.0

        for i in range(1, len(window)):
            change = window[i].close - window[i - 1].close
            if change > 0:
                gains += change
            else:
                losses += abs(change)

        avg_gain = gains / self.period
        avg_loss = losses / self.period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi


# ═══════════════════════════════════════════════════════════════════════════
# РЕЕСТР ДОСТУПНЫХ ФИЛЬТРОВ (для ссылок из детекторов)
# ═══════════════════════════════════════════════════════════════════════════

# Словарь: имя_фильтра → класс фильтра
# Используется детекторами для построения цепочки фильтров из конфига
FILTER_REGISTRY = {
    "body_ratio": BodyRatioFilter,
    "candle_size": CandleSizeFilter,
    "volume": VolumeFilter,
    "ma_trend": MATrendFilter,
    "ma_trend100": MA100TrendFilter,
    "rsi": RSIFilter,
}


def build_filters_from_config(filters_config: dict, debug: bool = False) -> List[PatternFilter]:
    """
    Построить список фильтров из конфига паттерна.

    Берёт секцию "filters" из конфига паттерна. Для каждого указанного фильтра
    создаёт экземпляр с параметрами из конфига.

    Пример конфига:
        "filters": {
            "body_ratio": {"enabled": True, "min_ratio": 1.5},
            "volume": {"enabled": True, "min_ratio": 1.5, "lookback": 20},
        }

    Args:
        filters_config: Секция "filters" из конфига паттерна
        debug: Режим отладки

    Returns:
        List[PatternFilter]: Список инициализированных фильтров
    """
    filters = []
    for filter_name, filter_class in FILTER_REGISTRY.items():
        filter_cfg = filters_config.get(filter_name, {})
        if filter_cfg.get("enabled", True):
            filters.append(filter_class(config=filter_cfg, debug=debug))
    return filters


__all__ = [
    "PatternFilter",
    "FilterResult",
    "BodyRatioFilter",
    "CandleSizeFilter",
    "VolumeFilter",
    "MATrendFilter",
    "MA100TrendFilter",
    "RSIFilter",
    "FILTER_REGISTRY",
    "build_filters_from_config",
]
