"""
Base Pattern Detector

Базовый класс для всех детекторов свечных паттернов.
Определяет единый интерфейс: detect(window) → List[PatternEvent]

═══════════════════════════════════════════════════════════════════════════
ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════

Каждый детектор наследует BasePatternDetector и реализует:
1. pattern_code — уникальный код паттерна
2. pattern_name — человекочитаемое название
3. detect(window, candle_index) — поиск паттерна в окне свечей

Пример:

    class HammerDetector(BasePatternDetector):
        @property
        def pattern_code(self) -> str:
            return "hammer"

        @property
        def pattern_name(self) -> str:
            return "Hammer"

        def detect(self, window, candle_index):
            # ... логика обнаружения молота ...
            return [PatternEvent(...)] или []

═══════════════════════════════════════════════════════════════════════════
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from src.market_data.models import Candle


@dataclass
class PatternEvent:
    """
    Событие найденного паттерна.

    Attributes:
        timestamp: Время паттерна (open_time свечи)
        candle_index: Индекс свечи в исходном массиве
        name: Название паттерна (e.g., "Doji", "Hammer")
        pattern_code: Код паттерна (e.g., "doji", "hammer")
        signal_type: "BULLISH" | "BEARISH" | "NEUTRAL"
        price: Цена, ассоциированная с паттерном (обычно close)
        metadata: Дополнительная информация о паттерне
    """
    timestamp: datetime
    candle_index: int
    name: str
    pattern_code: str
    signal_type: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    price: float
    metadata: Optional[dict] = field(default_factory=dict)

    def __repr__(self):
        return (
            f"PatternEvent({self.name} [{self.signal_type}] "
            f"@ idx={self.candle_index}, price={self.price:.2f})"
        )


class BasePatternDetector(ABC):
    """
    Базовый класс для детектора свечного паттерна.

    Каждый детектор:
    - Имеет уникальный pattern_code (совпадает с ключом в конфиге)
    - Реализует метод detect() для поиска паттерна в окне свечей
    - Может применять фильтры (body_ratio, volume, ...) через self.filters
    - Получает параметры из конфига

    Фильтры:
    - Строятся автоматически из секции "filters" в конфиге паттерна
    - Применяются методом _apply_filters() после обнаружения паттерна
    - Каждый фильтр может отклонить паттерн (is_valid=False)
    """

    def __init__(self, config: dict = None, debug: bool = False):
        """
        Args:
            config: Параметры паттерна из секции "patterns" конфига
            debug: Режим отладки
        """
        self.config = config or {}
        self.debug = debug

        # Построение фильтров из конфига
        from .filters import build_filters_from_config
        filters_config = self.config.get("filters", {})
        self.filters = build_filters_from_config(filters_config, debug=debug)

        if debug and self.filters:
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(
                f"[{self.pattern_code}] Loaded {len(self.filters)} filter(s): "
                f"{[f.name for f in self.filters]}"
            )

    @property
    @abstractmethod
    def pattern_code(self) -> str:
        """Уникальный код паттерна (e.g., 'doji', 'hammer')."""
        pass

    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """Человекочитаемое название паттерна (e.g., 'Doji', 'Hammer')."""
        pass

    def detect(self, window: List[Candle], candle_index: int) -> List[PatternEvent]:
        """
        Обнаружить паттерн в переданном окне свечей.

        По умолчанию вызывает _detect_pattern() и затем применяет фильтры.
        Переопредели этот метод только если нужна принципиально другая логика.

        Args:
            window: Окно свечей для анализа (обычно 2-3 свечи)
            candle_index: Индекс последней свечи окна в исходном массиве

        Returns:
            List[PatternEvent]: Найденные события паттерна (обычно 0 или 1)
        """
        events = self._detect_pattern(window, candle_index)
        return events

    def detect_with_context(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
    ) -> List[PatternEvent]:
        """
        Расширенный метод обнаружения с полным контекстом.

        Вызывает _detect_pattern(), затем _apply_filters() с полной историей.
        Используется стратегией, когда нужен доступ ко всей истории (фильтры объёма и т.д.).

        Args:
            full_candles: Полный массив свечей
            window: Окно свечей для анализа
            candle_index: Индекс последней свечи окна в full_candles

        Returns:
            List[PatternEvent]: События паттерна, прошедшие фильтры
        """
        events = self._detect_pattern(window, candle_index)

        if not events:
            return []

        # Применяем фильтры к каждому найденному событию
        filtered = []
        for event in events:
            if self._apply_filters(full_candles, window, candle_index, event):
                filtered.append(event)
            elif self.debug:
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"[{self.pattern_code}] Event filtered out: {event}")

        return filtered

    @abstractmethod
    def _detect_pattern(self, window: List[Candle], candle_index: int) -> List[PatternEvent]:
        """
        Чистая логика обнаружения паттерна (без фильтров).

        Реализуй этот метод в дочернем классе.

        Args:
            window: Окно свечей для анализа
            candle_index: Индекс последней свечи окна в исходном массиве

        Returns:
            List[PatternEvent]: Найденные события (сырые, до фильтрации)
        """
        pass

    def _apply_filters(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
        event: PatternEvent,
    ) -> bool:
        """
        Применить все включённые фильтры к событию паттерна.

        Args:
            full_candles: Полный массив свечей
            window: Окно свечей с паттерном
            candle_index: Индекс последней свечи окна
            event: Событие для проверки

        Returns:
            bool: True если все фильтры пройдены
        """
        for flt in self.filters:
            if not flt.enabled:
                continue

            result = flt.apply(full_candles, window, candle_index, event)

            if self.debug:
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"  [{self.pattern_code}] {result}")

            if not result.is_valid:
                return False

        return True

    def __repr__(self):
        return f"{self.pattern_name}Detector(code={self.pattern_code})"
