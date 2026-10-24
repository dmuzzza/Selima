import logging
from typing import List
from dataclasses import dataclass
from datetime import datetime

from src.strategies.base_strategy import BaseStrategy, Signal
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.strategies.patterns_config import PATTERNS_CONFIG
from src.strategies.levels_detector import LevelsDetector

logger = logging.getLogger(__name__)

@dataclass
class PatternEvent:
    """Событие найденного паттерна для отображения на графике"""
    timestamp: datetime
    name: str
    type: str  # BULLISH, BEARISH, NEUTRAL
    price: float


class CandlePatternStrategy(BaseStrategy):
    """
    Стратегия, основанная на свечных паттернах.
    Конфигурируется через patterns_config.py
    """
    
    def __init__(self, name="CandlePatterns", timeframe=Timeframe.H1):
        super().__init__(name, timeframe)
        self.config = PATTERNS_CONFIG

        # ✅ НОВОЕ: детектор уровней
        self.levels_detector = LevelsDetector(config={
            "lookback_candles": 400,
            "clustering_range": 0.005,  # ±0.5%
            "min_touches": 3,           # Минимум 3 касания
        })
    
    def analyze(self, candles: List[Candle]) -> Signal:
        """
        Анализ с уровнями и фильтрами
        """
        if len(candles) < 3:
            return Signal("HOLD", 0, 0)
        
        # Находим уровни
        levels = self.levels_detector.find_levels(candles)
        
        # Находим паттерн
        events = self.detect_patterns([candles[-3], candles[-2], candles[-1]])
        
        if not events:
            return Signal("HOLD", 0, 0)
        
        last_event = events[-1]
        is_valid, reason = self.filter.should_enter(candles, last_event)
        
        logger.info(f"Pattern: {last_event.name} ({last_event.type}) - {reason}")
        
        if not is_valid:
            logger.debug(f"Signal filtered: {reason}")
            return Signal("HOLD", 0, 0)
        
        # ✅ ДОБАВЛЯЕМ ПРОВЕРКУ УРОВНЯ
        current_price = candles[-1].close
        
        if last_event.type == "BULLISH":
            # Проверяем близость к поддержке
            support = self.levels_detector.get_nearest_support(candles)
            
            if support:
                if self.levels_detector.is_near_level(current_price, support, tolerance_pct=0.5):
                    logger.info(f"✅ BULLISH pattern + NEAR SUPPORT {support['price']:.2f} (strength: {support['strength']})")
                    return Signal("BUY", current_price, candles[-1].open_time.timestamp())
                else:
                    logger.debug(f"BULLISH pattern but far from support {support['price']:.2f}")
                    return Signal("HOLD", 0, 0)
            else:
                logger.debug(f"BULLISH pattern but no support level found")
                return Signal("HOLD", 0, 0)
        
        elif last_event.type == "BEARISH":
            # Проверяем близость к сопротивлению
            resistance = self.levels_detector.get_nearest_resistance(candles)
            
            if resistance:
                if self.levels_detector.is_near_level(current_price, resistance, tolerance_pct=0.5):
                    logger.info(f"✅ BEARISH pattern + NEAR RESISTANCE {resistance['price']:.2f} (strength: {resistance['strength']})")
                    return Signal("SELL", current_price, candles[-1].open_time.timestamp())
                else:
                    logger.debug(f"BEARISH pattern but far from resistance {resistance['price']:.2f}")
                    return Signal("HOLD", 0, 0)
            else:
                logger.debug(f"BEARISH pattern but no resistance level found")
                return Signal("HOLD", 0, 0)
        
        return Signal("HOLD", 0, 0)

    
    def detect_all_patterns(self, candles: List[Candle]) -> List[PatternEvent]:
        """
        Пробегает по ВСЕЙ истории и находит все паттерны (для Dash).
        """
        events = []
        
        # Начинаем с 2, так как некоторым паттернам нужна предыдущая свеча или 3 свечи
        for i in range(2, len(candles)):
            window = candles[i-2:i+1]  # Берём 3 свечи
            found = self.detect_patterns(window)
            events.extend(found)
        
        return events
    
    def detect_patterns(self, window: List[Candle]) -> List[PatternEvent]:
        """Определяет паттерны на переданном окне свечей"""
        if len(window) < 3:
            return []
        
        events = []
        
        # Проверяем каждый паттерн если он включен в конфиге
        if self.config["doji"]["enabled"]:
            events.extend(self._detect_doji(window))
        
        if self.config["hammer"]["enabled"]:
            events.extend(self._detect_hammer(window))
        
        if self.config["shooting_star"]["enabled"]:
            events.extend(self._detect_shooting_star(window))
        
        if self.config["engulfing"]["enabled"]:
            events.extend(self._detect_engulfing(window))
        
        return events
    
    def _detect_doji(self, window: List[Candle]) -> List[PatternEvent]:
        """DOJI (Неопределенность) - конфигурируемый порог"""
        config = self.config["doji"]
        body_threshold = config["body_threshold"]
        
        candle_1 = window[0]
        candle_2 = window[1]
        candle_3 = window[2]
        events = []
        ts = candle_2.open_time
        
        body_2 = abs(candle_2.close - candle_2.open)
        rng_2 = candle_2.high - candle_2.low
        
        is_doji = rng_2 > 0 and body_2 <= (rng_2 * body_threshold)
        
        if is_doji:
            # СИГНАЛ ЛОНГ: 1-красная, 2-дожи, 3-зеленая
            is_red_1 = candle_1.close < candle_1.open and candle_1.open > candle_2.close
            is_green_3 = candle_3.close > candle_3.open and candle_3.close > candle_2.close
            
            if is_red_1 and is_green_3:
                events.append(PatternEvent(ts, "Doji Long", "BULLISH", candle_2.low))
            
            # СИГНАЛ ШОРТ: 1-зеленая, 2-дожи, 3-красная
            is_green_1 = candle_1.close > candle_1.open and candle_1.open < candle_2.close
            is_red_3 = candle_3.close < candle_3.open and candle_3.close < candle_2.close
            
            if is_green_1 and is_red_3:
                events.append(PatternEvent(ts, "Doji Short", "BEARISH", candle_2.high))
        
        return events
    
    def _detect_hammer(self, window: List[Candle]) -> List[PatternEvent]:
        """HAMMER (Молот - бычий разворот) - конфигурируемые пороги"""
        config = self.config["hammer"]
        shadow_ratio = config["shadow_ratio"]
        upper_shadow_max = config["upper_shadow_max"]
        
        curr = window[-1]
        events = []
        
        body = abs(curr.close - curr.open)
        rng = curr.high - curr.low
        lower_shadow = min(curr.open, curr.close) - curr.low
        upper_shadow = curr.high - max(curr.open, curr.close)
        
        is_hammer = (
            rng > 0 and
            lower_shadow >= (shadow_ratio * body) and
            upper_shadow <= (upper_shadow_max * rng)
        )
        
        if is_hammer:
            events.append(PatternEvent(curr.open_time, "Hammer", "BULLISH", curr.low))
        
        return events
    
    def _detect_shooting_star(self, window: List[Candle]) -> List[PatternEvent]:
        """SHOOTING STAR (Падающая звезда - медвежий разворот) - конфигурируемые пороги"""
        config = self.config["shooting_star"]
        shadow_ratio = config["shadow_ratio"]
        lower_shadow_max = config["lower_shadow_max"]
        
        curr = window[-1]
        events = []
        
        body = abs(curr.close - curr.open)
        rng = curr.high - curr.low
        upper_shadow = curr.high - max(curr.open, curr.close)
        lower_shadow = min(curr.open, curr.close) - curr.low
        
        is_shooting_star = (
            rng > 0 and
            upper_shadow >= (shadow_ratio * body) and
            lower_shadow <= (lower_shadow_max * rng)
        )
        
        if is_shooting_star:
            events.append(PatternEvent(curr.open_time, "Shooting Star", "BEARISH", curr.high))
        
        return events
    
    def _detect_engulfing(self, window: List[Candle]) -> List[PatternEvent]:
        """ENGULFING (Поглощение) - ИСПРАВЛЕННАЯ ЛОГИКА"""
        prev = window[-2]  # Предпоследняя
        curr = window[-1]  # Последняя
        events = []
        ts = curr.open_time
        
        # ✅ BULLISH ENGULFING
        # Предыдущая красная, текущая зеленая, полностью перекрывает тело
        is_bullish_engulfing = (
            prev.close < prev.open and  # Пред. красная (close < open)
            curr.close > curr.open and  # Тек. зеленая (close > open)
            curr.open < prev.close and  # Открылись ниже закрытия пред.
            curr.close > prev.open      # Закрылись выше открытия пред.
        )
        
        if is_bullish_engulfing:
            events.append(PatternEvent(ts, "Engulfing", "BULLISH", curr.low))
        
        # ✅ BEARISH ENGULFING
        # Предыдущая зеленая, текущая красная, полностью перекрывает тело
        is_bearish_engulfing = (
            prev.close > prev.open and  # Пред. зеленая (close > open)
            curr.close < curr.open and  # Тек. красная (close < open)
            curr.open > prev.close and  # Открылись выше закрытия пред.
            curr.close < prev.open      # Закрылись ниже открытия пред.
        )
        
        if is_bearish_engulfing:
            events.append(PatternEvent(ts, "Engulfing", "BEARISH", curr.high))
        
        return events