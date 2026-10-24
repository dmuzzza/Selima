"""
Детектор уровней поддержки и сопротивления (Level Builder V2 - FIXED v2).

НОВАЯ ЛОГИКА:
1. Находит уровни на ВСЕЙ истории (все экстремумы)
2. Группирует экстремумы по цене (±tolerance)
3. Берёт СРЕДНЕЕ из групп экстремумов
4. Строит уровень от первого экстремума до пробоя

═══════════════════════════════════════════════════════════════

📊 АЛГОРИТМ:

═══════════════════════════════════════════════════════════════

1️⃣ ВХОДНЫЕ ДАННЫЕ:
   - Экстремумы: массив со всеми касаниями
   - Свечи: полная история для поиска пробоев

2️⃣ ГРУППИРОВКА В ПОЛОСЫ:
   Экстремумы MAX: 3180, 3180.5, 3181 (±tolerance)
   → Одна полоса (3 касания)
   → Уровень: avg(3180, 3180.5, 3181) = 3180.5 (СРЕДНЕЕ!)

3️⃣ ПОИСК ИНДЕКСОВ:
   - start_index: индекс ПЕРВОГО экстремума в полосе (по времени)
   - end_index: индекс свечи, где произошёл пробой

4️⃣ ОТРИСОВКА:
   fig.add_shape(x0=start_index, x1=end_index, y0=avg_price, y1=avg_price)
   → Отрезок от начала до конца

═══════════════════════════════════════════════════════════════
FIXES:
✅ 1. Цена уровня = СРЕДНЕЕ (average) из всех экстремумов в группе
✅ 2. start_index берётся из первого экстремума по времени (index)
✅ 3. Использует breakout_tolerance (0.001) для определения пробоя
═══════════════════════════════════════════════════════════════
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional

from src.market_data.models import Candle
from src.strategies.levels_detector_v5 import Extremum
from src.strategies.levels_config import (
    get_clustering_tolerance,
    get_category_config,
    get_all_categories,
    BREAKOUT_CONFIG,
)

logger = logging.getLogger(__name__)


@dataclass
class Level:
    """Представление уровня поддержки/сопротивления (ОТРЕЗОК)"""

    price: float  # Цена уровня (среднее из группы экстремумов)
    level_type: str  # "SUPPORT" или "RESISTANCE"
    category: str  # "SWING", "INTRADAY", "MICROSTRUCTURE"

    # 🔴 НОВОЕ: Индексы для отрисовки отрезка
    start_index: Optional[int] = None  # Индекс первого касания в массиве свечей
    end_index: Optional[int] = None  # Индекс пробоя (если был)

    touches: List[Extremum] = field(default_factory=list)  # История касаний
    touches_count: int = 0  # Количество касаний
    first_touch_time: datetime = None  # Время первого касания
    last_touch_time: datetime = None  # Время последнего касания

    breakout_count: int = 0  # Количество пробоев
    breakout_time: datetime = None  # Время последнего пробоя
    status: str = "ACTIVE"  # "ACTIVE", "BROKEN", "INVALID"

    # Метаданные для отрисовки
    color: str = "red"
    priority: int = 10
    line_width: int = 2
    opacity: float = 1.0
    line_dash: str = "solid"

    def __repr__(self):
        return (
            f"{self.level_type}({self.price:.2f}) "
            f"[{self.category}] "
            f"touches={self.touches_count} "
            f"[{self.start_index}:{self.end_index}]"
        )

    def add_touch(self, extremum: Extremum):
        """Добавить касание к уровню"""
        self.touches.append(extremum)
        self.touches_count += 1
        self.last_touch_time = extremum.candle_time
        if self.first_touch_time is None:
            self.first_touch_time = extremum.candle_time

    def register_breakout(self, candle_index: int, candle_time: datetime):
        """Зарегистрировать пробой уровня"""
        self.breakout_count += 1
        self.breakout_time = candle_time
        # Сохраняем индекс первого пробоя как end_index
        if self.end_index is None:
            self.end_index = candle_index

    def is_broken(self, max_breakouts: int) -> bool:
        """Проверить, "мёртв" ли уровень"""
        return self.breakout_count >= max_breakouts


class LevelsBuilder:
    """Строитель уровней из экстремумов (V2 - ОТРЕЗКИ, FIXED v2)"""

    def __init__(self, timeframe: str = "1h", debug: bool = False):
        self.logger = logging.getLogger(__name__)
        self.timeframe = timeframe
        self.debug = debug

        # Получаем tolerance для этого ТФ
        self.clustering_tolerance = get_clustering_tolerance(timeframe)
        self.logger.info(
            f"LevelsBuilder initialized for {timeframe} TF "
            f"with clustering_tolerance={self.clustering_tolerance*100:.2f}%"
        )

    def build_levels(
        self, extremums: List[Extremum], candles: List[Candle]
    ) -> List[Level]:
        """
        Построить уровни из экстремумов.

        ✅ Работает на ВСЕЙ истории (все экстремумы)
        ✅ Каждый уровень - отрезок от start до end
        ✅ end_index = момент пробоя или последняя свеча

        Args:
            extremums: Список экстремумов из LevelsDetector (на всей истории!)
            candles: Список свечей (на всей истории!)

        Returns:
            List[Level]: Список уровней с start/end индексами
        """

        if not extremums or not candles:
            self.logger.warning("No extremums or candles provided")
            return []

        self.logger.info(
            f"Building levels from {len(extremums)} extremums, {len(candles)} candles"
        )

        # ═══════════════════════════════════════════════════════════
        # ШАГ 1: РАЗДЕЛЯЕМ НА MAX И MIN
        # ═══════════════════════════════════════════════════════════

        max_extremums = [e for e in extremums if e.extremum_type == "MAX"]
        min_extremums = [e for e in extremums if e.extremum_type == "MIN"]

        self.logger.info(f"Found {len(max_extremums)} MAX, {len(min_extremums)} MIN extremums")

        # ═══════════════════════════════════════════════════════════
        # ШАГ 2: ИЩЕМ УРОВНИ ДЛЯ КАЖДОГО ТИПА
        # ═══════════════════════════════════════════════════════════

        resistance_levels = self._find_levels_for_type(
            max_extremums, "RESISTANCE", candles
        )
        support_levels = self._find_levels_for_type(min_extremums, "SUPPORT", candles)

        levels = resistance_levels + support_levels

        self.logger.info(f"✅ Created {len(levels)} levels")

        return levels

    def _find_levels_for_type(
        self, extremums: List[Extremum], level_type: str, candles: List[Candle]
    ) -> List[Level]:
        """
        Найти уровни для одного типа экстремумов.

        Алгоритм:
        1. Сортируем по цене
        2. Группируем в полосы (по tolerance)
        3. Для каждой полосы:
           - Находим СРЕДНЕЕ (average) цены всех экстремумов
           - Находим start_index (индекс первого экстремума по времени)
           - Ищем end_index (момент пробоя этого среднего уровня)

        Args:
            extremums: Экстремумы одного типа
            level_type: "RESISTANCE" или "SUPPORT"
            candles: Свечи для определения индексов

        Returns:
            List[Level]: Уровни с индексами
        """

        if not extremums:
            return []

        # Сортируем по цене
        sorted_extremums = sorted(extremums, key=lambda e: e.price)

        levels = []
        processed = set()

        for i, extremum in enumerate(sorted_extremums):
            if i in processed:
                continue

            # ───────────────────────────────────────────────────────
            # ГРУППИРУЕМ В ПОЛОСУ
            # ───────────────────────────────────────────────────────

            band = [extremum]
            band_indices = {i}
            band_price = extremum.price

            # Ищем соседей по цене
            for j in range(i + 1, len(sorted_extremums)):
                if j in processed:
                    continue
                
                next_ext = sorted_extremums[j]
                distance_percent = abs(next_ext.price - band_price) / band_price
                
                if distance_percent <= self.clustering_tolerance:
                    band.append(next_ext)
                    band_indices.add(j)

            processed.update(band_indices)

            # ───────────────────────────────────────────────────────
            # ОПРЕДЕЛЯЕМ КАТЕГОРИЮ
            # ───────────────────────────────────────────────────────

            category = self._determine_category(len(band))

            if category is None:
                if self.debug:
                    self.logger.debug(
                        f"Band with {len(band)} touches doesn't match any category"
                    )
                continue

            # ───────────────────────────────────────────────────────
            # ✅ ИСПРАВЛЕНИЕ 1: ЦЕНА = СРЕДНЕЕ (AVERAGE)
            # ───────────────────────────────────────────────────────

            level_price = sum(e.price for e in band) / len(band)

            if self.debug:
                prices_str = ", ".join([f"{e.price:.2f}" for e in band])
                self.logger.debug(
                    f"Band prices [{level_type}]: {prices_str} → avg: {level_price:.2f}"
                )

            # ───────────────────────────────────────────────────────
            # ✅ ИСПРАВЛЕНИЕ 2: START_INDEX = ПЕРВЫЙ ПО ВРЕМЕНИ
            # ───────────────────────────────────────────────────────

            # Берём экстремум с МИНИМАЛЬНЫМ ИНДЕКСОМ (он раньше всех по времени)
            first_touch_extremum = min(band, key=lambda e: e.index)
            start_index = first_touch_extremum.index

            # ───────────────────────────────────────────────────────
            # СОЗДАЁМ УРОВЕНЬ
            # ───────────────────────────────────────────────────────

            cat_config = get_category_config(category)

            level = Level(
                price=level_price,
                level_type=level_type,
                category=category,
                touches_count=len(band),
                start_index=start_index,
                end_index=None,  # Пока None, заполним при поиске пробоя
                color=cat_config["color"],
                priority=cat_config["priority"],
                line_width=cat_config["line_width"],
                opacity=cat_config["opacity"],
                line_dash=cat_config["line_dash"],
            )

            # Добавляем касания
            for extremum in band:
                level.add_touch(extremum)

            # ───────────────────────────────────────────────────────
            # ИЩЕМ ПРОБОЙ (end_index)
            # ───────────────────────────────────────────────────────

            self._find_breakout_index(level, candles, start_index)

            # Если пробоя не нашли → end_index = последняя свеча
            if level.end_index is None:
                level.end_index = len(candles) - 1
                level.status = "ACTIVE"
            else:
                level.status = "BROKEN"

            levels.append(level)

            if self.debug:
                self.logger.debug(
                    f"✅ Created {level_type} level at {level_price:.2f} "
                    f"with {len(band)} touches [{start_index}:{level.end_index}] ({category})"
                )

        return levels

    def _find_breakout_index(
        self, level: Level, candles: List[Candle], start_index: int
    ):
        """
        Найти индекс пробоя уровня в свечах.

        Ищет ПОСЛЕ start_index момент, когда цена закрылась вне уровня.

        ✅ УЛУЧШЕНИЕ: Использует breakout_tolerance из конфига

        Args:
            level: Уровень для проверки
            candles: Все свечи
            start_index: С какого индекса начинать поиск
        """

        tolerance = BREAKOUT_CONFIG["breakout_tolerance"]

        min_bars_after_extremum = 5  # Пропускаем 2 свечи после экстремума
        for idx in range(start_index + min_bars_after_extremum, len(candles)):
            candle = candles[idx]

            if level.level_type == "RESISTANCE":
                # Пробой вверх: close > level + tolerance
                if candle.close > level.price * (1 + tolerance):
                    level.register_breakout(idx, candle.open_time)
                    return

            elif level.level_type == "SUPPORT":
                # Пробой вниз: close < level - tolerance
                if candle.close < level.price * (1 - tolerance):
                    level.register_breakout(idx, candle.open_time)
                    return

        # Пробоя не найдено → end_index останется None

    def _determine_category(self, touches_count: int) -> Optional[str]:
        """
        Определить категорию на основе количества касаний.

        Args:
            touches_count: Количество касаний в полосе

        Returns:
            Название категории или None
        """

        categories = get_all_categories()
        candidates = []

        for category in categories:
            config = get_category_config(category)

            # ✅ ПРОВЕРЯЕМ: Включена ли эта категория?
            if not config.get("enabled", True):  # По умолчанию True (если параметра нет)
                if self.debug:
                    self.logger.debug(f"Category {category} is disabled (enabled=False), skipping")
                continue

            min_touches = config["min_touches"]

            if touches_count >= min_touches:
                candidates.append((category, min_touches))

        if not candidates:
            return None

        # Выбираем категорию с наибольшим min_touches
        best_category = max(candidates, key=lambda x: x[1])[0]

        return best_category
