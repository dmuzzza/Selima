"""
Детектор уровней поддержки и сопротивления (V5 - FINAL CORRECT LOGIC).
Находит локальные экстремумы с проверкой по окну свечей.

✅ ПРАВИЛЬНАЯ ЛОГИКА:
МАКСИМУМ = текущая свеча выше МИНИМУМА окнов + threshold% И выше ВСЕХ соседей
МИНИМУМ = текущая свеча ниже МАКСИМУМА окнов - threshold% И ниже ВСЕХ соседей
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List
from src.market_data.models import Candle

logger = logging.getLogger(__name__)


@dataclass
class Extremum:
    """Представление экстремума (максимум или минимум)"""
    index: int              # Индекс свечи в массиве
    price: float            # Цена экстремума
    candle_time: datetime   # Время открытия свечи
    extremum_type: str      # "MAX" или "MIN"
    
    def __repr__(self):
        return f"{self.extremum_type}({self.price:.2f}) @ {self.candle_time.strftime('%Y-%m-%d %H:%M')}"


class LevelsDetector:
    """Детектор локальных экстремумов с окном проверки"""
    
    def __init__(self, config: dict = None, debug: bool = False):
        self.logger = logging.getLogger(__name__)
        self.config = config or {}
        self.debug = debug
    
    def find_v_extremums(self, candles: List[Candle]) -> List[Extremum]:
        """
        Находит V-образные экстремумы с проверкой по окну свечей.
        
        ✅ ПРАВИЛЬНАЯ ЛОГИКА (V5):
        
        МАКСИМУМ - должна выполниться ОБА условия:
        1. max(open, close)[i] > min(min(o,c) всех соседей) * (1 + threshold%)
        2. max(open, close)[i] > max(max(o,c) всех соседей)
        
        МИНИМУМ - должна выполниться ОБА условия:
        1. min(open, close)[i] < max(max(o,c) всех соседей) * (1 - threshold%)
        2. min(open, close)[i] < min(min(o,c) всех соседей)
        
        Использует параметры из config:
        - window_size: размер окна слева и справа (по умолчанию 7)
        - threshold_percent: минимальная разница в процентах (по умолчанию 1%)
        - use_high_low: использовать high/low (True) или open/close (False)
        
        Args:
            candles: Список свечей (должны быть отсортированы по времени)
        
        Returns:
            List[Extremum]: Найденные экстремумы с условием по окну
        """
        
        # Извлекаем параметры из конфига с дефолтами
        window_size = self.config.get("window_size", 7)
        threshold_percent = self.config.get("threshold_percent", 1.0) / 100.0
        use_high_low = self.config.get("use_high_low", False)
        
        if len(candles) < window_size * 2 + 1:
            self.logger.warning(
                f"Not enough candles for extremum detection with window_size={window_size}: {len(candles)}"
            )
            return []
        
        extremums = []
        
        # Проходим по всем свечам, где можно проверить окно слева и справа
        for i in range(window_size, len(candles) - window_size):
            curr_candle = candles[i]
            
            # Выбираем метод: high/low или open/close
            if use_high_low:
                curr_price_max = curr_candle.high
                curr_price_min = curr_candle.low
                
                # Левое окно: [i-window_size : i]
                left_max = max(c.high for c in candles[i - window_size:i])
                left_min = min(c.low for c in candles[i - window_size:i])
                
                # Правое окно: [i+1 : i+window_size+1]
                right_max = max(c.high for c in candles[i + 1:i + window_size + 1])
                right_min = min(c.low for c in candles[i + 1:i + window_size + 1])
                
                # Находим экстремальные значения
                smallest_min = min(left_min, right_min)  # Самый низкий минимум
                biggest_max = max(left_max, right_max)   # Самый высокий максимум
                
            else:
                # Используем open/close
                curr_price_max = max(curr_candle.open, curr_candle.close)
                curr_price_min = min(curr_candle.open, curr_candle.close)
                
                # Левое окно: [i-window_size : i]
                left_min_prices = [
                    min(c.open, c.close) for c in candles[i - window_size:i]
                ]
                left_max_prices = [
                    max(c.open, c.close) for c in candles[i - window_size:i]
                ]
                
                # Правое окно: [i+1 : i+window_size+1]
                right_min_prices = [
                    min(c.open, c.close) for c in candles[i + 1:i + window_size + 1]
                ]
                right_max_prices = [
                    max(c.open, c.close) for c in candles[i + 1:i + window_size + 1]
                ]
                
                # Находим экстремальные значения в окнах
                left_min = min(left_min_prices) if left_min_prices else float('inf')
                left_max = max(left_max_prices) if left_max_prices else 0
                right_min = min(right_min_prices) if right_min_prices else float('inf')
                right_max = max(right_max_prices) if right_max_prices else 0
                
                smallest_min = min(left_min, right_min)  # Самый низкий минимум в окнах
                biggest_max = max(left_max, right_max)   # Самый высокий максимум в окнах
            
            # ✅ ПРАВИЛЬНЫЕ УСЛОВИЯ:
            # МАКСИМУМ: выше самого низкого + threshold% И выше всех соседей
            is_max = (curr_price_max > smallest_min * (1 + threshold_percent)) and \
                     (curr_price_max >= biggest_max)
            
            # МИНИМУМ: ниже самого высокого - threshold% И ниже всех соседей
            is_min = (curr_price_min < biggest_max * (1 - threshold_percent)) and \
                     (curr_price_min <= smallest_min)
            
            # DEBUG информация
            if self.debug and (is_max or is_min):
                if is_max:
                    cond1 = curr_price_max > smallest_min * (1 + threshold_percent)
                    cond2 = curr_price_max > biggest_max
                    self.logger.debug(
                        f"[{i}] MAX: {curr_price_max:.2f} | "
                        f"Условие 1 (>{smallest_min:.2f}*1.01={smallest_min*(1+threshold_percent):.2f}): {cond1} | "
                        f"Условие 2 (>{biggest_max:.2f}): {cond2}"
                    )
                elif is_min:
                    cond1 = curr_price_min < biggest_max * (1 - threshold_percent)
                    cond2 = curr_price_min < smallest_min
                    self.logger.debug(
                        f"[{i}] MIN: {curr_price_min:.2f} | "
                        f"Условие 1 (<{biggest_max:.2f}*0.99={biggest_max*(1-threshold_percent):.2f}): {cond1} | "
                        f"Условие 2 (<{smallest_min:.2f}): {cond2}"
                    )
            
            # Добавляем найденные экстремумы
            if is_max:
                extremums.append(Extremum(
                    index=i,
                    price=curr_price_max,
                    candle_time=curr_candle.open_time,
                    extremum_type="MAX"
                ))
            elif is_min:
                extremums.append(Extremum(
                    index=i,
                    price=curr_price_min,
                    candle_time=curr_candle.open_time,
                    extremum_type="MIN"
                ))
        
        # ИТОГОВЫЙ ЛОГ
        max_count = sum(1 for e in extremums if e.extremum_type == "MAX")
        min_count = sum(1 for e in extremums if e.extremum_type == "MIN")
        
        self.logger.info(
            f"✅ Found {len(extremums)} extremums (window={window_size}, threshold={threshold_percent*100:.1f}%) "
            f"[{max_count} MAX, {min_count} MIN]"
        )
        
        return extremums
    
    def get_maxima(self, extremums: List[Extremum]) -> List[Extremum]:
        """Фильтр: только максимумы"""
        return [e for e in extremums if e.extremum_type == "MAX"]
    
    def get_minima(self, extremums: List[Extremum]) -> List[Extremum]:
        """Фильтр: только минимумы"""
        return [e for e in extremums if e.extremum_type == "MIN"]
    
    def get_last_extremum(self, extremums: List[Extremum]) -> Extremum | None:
        """Получить последний найденный экстремум"""
        return extremums[-1] if extremums else None
    
    def get_extremums_in_range(self, extremums: List[Extremum], 
                               start_time: datetime, 
                               end_time: datetime) -> List[Extremum]:
        """Получить экстремумы в диапазоне времени"""
        return [e for e in extremums 
                if start_time <= e.candle_time <= end_time]
