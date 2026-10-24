from enum import Enum
from datetime import timedelta
from t_tech.invest.schemas import CandleInterval


class Timeframe(Enum):
    """Перечисление поддерживаемых таймфреймов"""
    
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    
    @property
    def to_timedelta(self) -> timedelta:
        """Преобразовать в timedelta"""
        mapping = {
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
            "1w": timedelta(weeks=1),
        }
        return mapping[self.value]
    
    @property
    def to_api_interval(self) -> CandleInterval:
        """Преобразовать в CandleInterval для T-Invest API"""
        mapping = {
            "30m": CandleInterval.CANDLE_INTERVAL_30_MIN,
            "1h": CandleInterval.CANDLE_INTERVAL_HOUR,
            "4h": CandleInterval.CANDLE_INTERVAL_4_HOUR,
            "1d": CandleInterval.CANDLE_INTERVAL_DAY,
            "1w": CandleInterval.CANDLE_INTERVAL_WEEK,
        }
        return mapping[self.value]
    
    @property
    def minutes(self) -> int:
        """Получить количество минут в таймфрейме"""
        return int(self.to_timedelta.total_seconds() / 60)
    
    def __str__(self) -> str:
        """Строковое представление"""
        return self.value
    
    def __repr__(self) -> str:
        """Представление для отладки"""
        return f"Timeframe.{self.name}"


# Примеры использования:
# tf = Timeframe.H1
# tf.value → "1h"
# tf.to_api_interval → CandleInterval.CANDLE_INTERVAL_HOUR
# tf.to_timedelta → timedelta(hours=1)
# tf.minutes → 60
# str(tf) → "1h"
