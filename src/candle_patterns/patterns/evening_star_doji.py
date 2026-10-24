"""
Evening Star Doji Pattern Detector

Вечерняя звезда Доджи — медвежий разворотный паттерн из 3 свечей.

СТРУКТУРА:
- Свеча 1: бычья (close > open), тело >= среднего тела за 100 свечей
- Свеча 2: дожи, тело <= среднего тела за 100 свечей / 2
- Свеча 3: медвежья (close < open), тело >= среднего тела за 100 свечей / 1.5

Тени (high/low) не учитываются.
"""

from typing import List, Optional

from src.market_data.models import Candle
from .base import BasePatternDetector, PatternEvent


class EveningStarDojiDetector(BasePatternDetector):
    """
    Детектор паттерна «Вечерняя звезда Доджи».
    """

    AVG_LOOKBACK: int = 100
    VOLUME_LOOKBACK: int = 30

    @property
    def pattern_code(self) -> str:
        return "evening_star_doji"

    @property
    def pattern_name(self) -> str:
        return "Evening Star Doji"

    def detect_with_context(
        self,
        full_candles: List[Candle],
        window: List[Candle],
        candle_index: int,
    ) -> List[PatternEvent]:
        """
        Расширенное обнаружение с расчётом среднего тела и объёма.
        """
        self._avg_body = self._calculate_avg_body(full_candles, candle_index)
        self._avg_volume = self._calculate_avg_volume(full_candles, candle_index)
        return super().detect_with_context(full_candles, window, candle_index)

    def _calculate_avg_body(
        self,
        candles: List[Candle],
        candle_index: int,
    ) -> float:
        """
        Среднее тело свечи |close - open| за последние AVG_LOOKBACK свечей
        (не включая текущую).
        """
        start_idx = max(0, candle_index - self.AVG_LOOKBACK)
        lookback_candles = candles[start_idx:candle_index]

        bodies = [
            abs(c.close - c.open)
            for c in lookback_candles
            if abs(c.close - c.open) > 0
        ]

        if not bodies:
            return 0.0

        return sum(bodies) / len(bodies)

    def _calculate_avg_volume(
        self,
        candles: List[Candle],
        candle_index: int,
    ) -> float:
        """
        Средний объём за последние VOLUME_LOOKBACK свечей (не включая текущую).
        """
        start_idx = max(0, candle_index - self.VOLUME_LOOKBACK)
        lookback_candles = candles[start_idx:candle_index]

        volumes = [
            c.volume
            for c in lookback_candles
            if c.volume is not None and c.volume > 0
        ]

        if not volumes:
            return 0.0

        return sum(volumes) / len(volumes)

    def _detect_pattern(self, window: List[Candle], candle_index: int) -> List[PatternEvent]:
        """
        Обнаружить паттерн Вечерняя звезда Доджи в окне из 3+ свечей.
        Свеча 2 (доджи) должна быть локальным максимумом в окне ±5 свечей.
        """
        if len(window) < 3:
            return []

        c1 = window[-3]
        c2 = window[-2]
        c3 = window[-1]

        avg_body = getattr(self, "_avg_body", 0.0)
        if avg_body <= 0:
            return []

        body_1 = c1.close - c1.open
        body_2 = abs(c2.close - c2.open)
        body_3 = c3.open - c3.close

        # ─────────────────────────────────────────────────────────
        # СВЕЧА 1 — БЫЧЬЯ, ТЕЛО >= СРЕДНЕГО
        # ─────────────────────────────────────────────────────────
        if body_1 <= 0:
            return []

        if body_1 < avg_body:
            return []

        # ─────────────────────────────────────────────────────────
        # СВЕЧА 2 — ДОДЖИ: тело <= avg_body / 2
        #           И меньше тела свечи 1 и свечи 3
        # ─────────────────────────────────────────────────────────
        if body_2 > avg_body / 2:
            return []

        if body_2 >= body_1 or body_2 >= body_3:
            return []

        # ─────────────────────────────────────────────────────────
        # СВЕЧА 3 — МЕДВЕЖЬЯ, ТЕЛО >= avg_body / 1.5
        #           И ОБЪЁМ > СРЕДНЕГО ЗА 30 СВЕЧЕЙ
        # ─────────────────────────────────────────────────────────
        if body_3 <= 0:
            return []

        if body_3 < avg_body / 1.5:
            return []

        avg_volume = getattr(self, "_avg_volume", 0.0)
        if avg_volume > 0 and (c3.volume is None or c3.volume <= avg_volume):
            return []

        # ─────────────────────────────────────────────────────────
        # СВЕЧА 2 — ЛОКАЛЬНЫЙ МАКСИМУМ (хай выше всех соседей ±5)
        # window[-3] = c1, window[-2] = c2, window[-1] = c3
        # Индекс c2 в window = len(window) - 2
        # ─────────────────────────────────────────────────────────
        c2_idx = len(window) - 2
        left  = max(0, c2_idx - 5)
        right = min(len(window), c2_idx + 5 + 1)  # +1 т.к. срез не включает правый край

        neighborhood = window[left:right]

        c2_high = c2.high
        for candle in neighborhood:
            if candle is c2:
                continue
            if candle.high >= c2_high:   # строгий максимум: хай c2 должен быть выше ВСЕХ соседей
                return []

        # ─────────────────────────────────────────────────────────
        # ПАТТЕРН НАЙДЕН
        # ─────────────────────────────────────────────────────────
        return [PatternEvent(
            timestamp=c3.open_time,
            candle_index=candle_index,
            name=self.pattern_name,
            pattern_code=self.pattern_code,
            signal_type="BEARISH",
            price=c3.close,
            metadata={
                "body_1": body_1,
                "body_2": body_2,
                "body_3": body_3,
                "avg_body": avg_body,
                "avg_volume": avg_volume,
                "c3_volume": c3.volume,
                "c2_high": c2_high,
            },
        )]