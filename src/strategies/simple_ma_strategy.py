from typing import List
from src.strategies.base_strategy import BaseStrategy, Signal
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe


class SimpleMomentumStrategy(BaseStrategy):
    """
    Простая стратегия на основе momentum (скорость изменения цены).
    
    BUY: если цена растет быстро (close > open + порог)
    SELL: если цена падает (close < open)
    HOLD: иначе
    """
    
    def __init__(self, 
                 name: str = "SimpleMomentum",
                 timeframe: Timeframe = Timeframe.H1,
                 momentum_threshold: float = 0.5):  # % изменения
        super().__init__(name, timeframe)
        self.momentum_threshold = momentum_threshold
    
    def analyze(self, candles: List[Candle]) -> Signal:
        """
        Анализирует последнюю свечу на основе momentum.
        """
        if not self.validate_candles(candles, min_count=1):
            return Signal("HOLD", 0, 0)
        
        current_candle = candles[-1]
        
        # Вычисляем momentum (% изменение за свечу)
        price_change = current_candle.close - current_candle.open
        change_percent = (price_change / current_candle.open) * 100
        
        # Преобразуем время в unix timestamp
        timestamp = current_candle.open_time.timestamp()
        
        # Логика сигнала
        if change_percent > self.momentum_threshold:
            # Цена растет сильно
            confidence = min(abs(change_percent) / 2.0, 1.0)
            return Signal("BUY", current_candle.close, timestamp, confidence)
        
        elif change_percent < -self.momentum_threshold:
            # Цена падает сильно
            confidence = min(abs(change_percent) / 2.0, 1.0)
            return Signal("SELL", current_candle.close, timestamp, confidence)
        
        else:
            # Малое изменение цены
            return Signal("HOLD", current_candle.close, timestamp, 0.5)
