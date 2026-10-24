"""
Engulfing Pattern Detector

Бычье/Медвежье поглощение.
Вторая свеча полностью поглощает тело первой.

ПРАВИЛА:
- BULLISH: prev красная (close < open), curr зелёная (close > open)
           curr.open < prev.close И curr.close > prev.open
- BEARISH: prev зелёная (close > open), curr красная (close < open)
           curr.open > prev.close И curr.close < prev.open

ФИЛЬТРЫ (опционально, настраиваются в config["patterns"]["engulfing"]["filters"]):
- body_ratio: тело поглощающей ≥ min_ratio × тела предыдущей (по умолчанию 1.5)
- volume:     объём поглощающей ≥ min_ratio × среднего за lookback свечей (по умолчанию 1.5×/20)

═══════════════════════════════════════════════════════════════════════════
"""

from typing import List

from src.market_data.models import Candle
from .base import BasePatternDetector, PatternEvent


class EngulfingDetector(BasePatternDetector):
    """
    Детектор паттерна «Поглощение» (Engulfing).

    Вторая свеча полностью перекрывает тело первой.
    """

    @property
    def pattern_code(self) -> str:
        return "engulfing"

    @property
    def pattern_name(self) -> str:
        return "Engulfing"

    def _detect_pattern(self, window: List[Candle], candle_index: int) -> List[PatternEvent]:
        """
        Обнаружить поглощение в окне из 2+ свечей.

        Анализирует последние две свечи окна.
        """
        if len(window) < 2:
            return []

        prev = window[-2]  # Предыдущая свеча
        curr = window[-1]  # Текущая (поглощающая)

        # ─────────────────────────────────────────────────────────
        # BULLISH ENGULFING
        # Предыдущая красная, текущая зелёная, полностью перекрывает тело
        # ─────────────────────────────────────────────────────────

        is_bullish = (
            prev.close < prev.open                     # prev — красная
            and curr.close > curr.open                 # curr — зелёная
            and curr.open <= prev.close                # открылись не выше закрытия prev
            and curr.close >= prev.open                # закрылись не ниже открытия prev
        )

        if is_bullish:
            return [PatternEvent(
                timestamp=curr.open_time,
                candle_index=candle_index,
                name=self.pattern_name,
                pattern_code=self.pattern_code,
                signal_type="BULLISH",
                price=curr.close,
                metadata={
                    "prev_body": abs(prev.close - prev.open),
                    "curr_body": abs(curr.close - curr.open),
                    "body_ratio": abs(curr.close - curr.open) / max(abs(prev.close - prev.open), 1e-12),
                },
            )]

        # ─────────────────────────────────────────────────────────
        # BEARISH ENGULFING
        # Предыдущая зелёная, текущая красная, полностью перекрывает тело
        # ─────────────────────────────────────────────────────────

        is_bearish = (
            prev.close > prev.open                     # prev — зелёная
            and curr.close < curr.open                 # curr — красная
            and curr.open >= prev.close                # открылись не ниже закрытия prev
            and curr.close <= prev.open                # закрылись не выше открытия prev
        )

        if is_bearish:
            return [PatternEvent(
                timestamp=curr.open_time,
                candle_index=candle_index,
                name=self.pattern_name,
                pattern_code=self.pattern_code,
                signal_type="BEARISH",
                price=curr.close,
                metadata={
                    "prev_body": abs(prev.close - prev.open),
                    "curr_body": abs(curr.close - curr.open),
                    "body_ratio": abs(curr.close - curr.open) / max(abs(prev.close - prev.open), 1e-12),
                },
            )]

        return []
