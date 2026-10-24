"""
Детектор уровней поддержки и сопротивления (V2 с Debug логированием).
Находит локальные экстремумы с проверкой по окну свечей.
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
        self.debug = debug  # Флаг для детальной диагностики
    
    def find_v_extremums(self, candles: List[Candle]) -> List[Extremum]:
        """
        Находит V-образные экстремумы с проверкой по окну свечей.
        
        Использует параметры из config:
        - window_size: размер окна слева и справа (по умолчанию 7)
        - threshold_percent: минимальная разница в процентах (по умолчанию 1%)
        - use_high_low: использовать high/low (True) или open/close (False, по умолчанию)
        
        Логика:
        - МАКСИМУМ: max(open, close)[i] > max(все соседи в окне) * (1 + threshold%)
        - МИНИМУМ: min(open, close)[i] < min(все соседи в окне) * (1 - threshold%)
        
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
        close_calls = []  # Для отслеживания "почти экстремумов"
        
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
            else:
                # Используем open/close
                curr_price_max = max(curr_candle.open, curr_candle.close)
                curr_price_min = min(curr_candle.open, curr_candle.close)
                
                # Левое окно: [i-window_size : i]
                left_prices = [
                    max(c.open, c.close) for c in candles[i - window_size:i]
                ]
                right_prices = [
                    max(c.open, c.close) for c in candles[i + 1:i + window_size + 1]
                ]
                left_max = max(left_prices) if left_prices else 0
                right_max = max(right_prices) if right_prices else 0
                
                # Для минимума
                left_prices_min = [
                    min(c.open, c.close) for c in candles[i - window_size:i]
                ]
                right_prices_min = [
                    min(c.open, c.close) for c in candles[i + 1:i + window_size + 1]
                ]
                left_min = min(left_prices_min) if left_prices_min else float('inf')
                right_min = min(right_prices_min) if right_prices_min else float('inf')
            
            # Вычисляем пороги
            left_max_threshold = left_max * (1 + threshold_percent)
            right_max_threshold = right_max * (1 + threshold_percent)
            left_min_threshold = left_min * (1 - threshold_percent)
            right_min_threshold = right_min * (1 - threshold_percent)
            
            # Проверяем условия
            is_max = (curr_price_max > left_max_threshold and
                      curr_price_max > right_max_threshold)
            
            is_min = (curr_price_min < left_min_threshold and
                      curr_price_min < right_min_threshold)
            
            # ===== DEBUG: АНАЛИЗ "ПОЧТИ ЭКСТРЕМУМОВ" =====
            if self.debug:
                # Вычисляем реальные разницы в процентах
                max_diff_left = ((curr_price_max - left_max) / left_max * 100) if left_max > 0 else 0
                max_diff_right = ((curr_price_max - right_max) / right_max * 100) if right_max > 0 else 0
                min_diff_left = ((left_min - curr_price_min) / left_min * 100) if left_min > 0 else 0
                min_diff_right = ((right_min - curr_price_min) / right_min * 100) if right_min > 0 else 0
                
                # Если разница БЛИЗКА к порогу (но не достаточна)
                threshold_pct = threshold_percent * 100
                
                # Для MAX: если хотя бы одна из разниц меньше порога, но больше половины
                if not is_max and (max_diff_left > threshold_pct * 0.5 or max_diff_right > threshold_pct * 0.5):
                    close_calls.append({
                        'index': i,
                        'type': 'MAX',
                        'price': curr_price_max,
                        'left_max': left_max,
                        'right_max': right_max,
                        'left_diff': max_diff_left,
                        'right_diff': max_diff_right,
                        'required': threshold_pct
                    })
                
                # Для MIN: если хотя бы одна из разниц меньше порога, но больше половины
                if not is_min and (min_diff_left > threshold_pct * 0.5 or min_diff_right > threshold_pct * 0.5):
                    close_calls.append({
                        'index': i,
                        'type': 'MIN',
                        'price': curr_price_min,
                        'left_min': left_min,
                        'right_min': right_min,
                        'left_diff': min_diff_left,
                        'right_diff': min_diff_right,
                        'required': threshold_pct
                    })
            
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
        
        self.logger.info(
            f"Found {len(extremums)} extremums (window={window_size}, threshold={threshold_percent*100}%) "
            f"[{sum(1 for e in extremums if e.extremum_type == 'MAX')} MAX, "
            f"{sum(1 for e in extremums if e.extremum_type == 'MIN')} MIN]"
        )
        
        # Выводим близкие случаи если нужна диагностика
        if self.debug and close_calls:
            self.logger.warning(f"\n⚠️ CLOSE CALLS (почти экстремумы):\n")
            for call in close_calls[:10]:  # Показываем первые 10
                self.logger.warning(
                    f"  [{call['index']}] {call['type']}: price={call['price']:.2f}, "
                    f"left_diff={call['left_diff']:.3f}%, right_diff={call['right_diff']:.3f}% "
                    f"(требуется {call['required']:.3f}%)"
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