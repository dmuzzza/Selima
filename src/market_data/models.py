from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Candle:
    """Свеча OHLCV с метаданными"""
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_complete: bool = True
    candle_source: Optional[int] = None  # 0, 1, 2 - как int из API
    
    @property
    def candle_source_name(self) -> str:
        """Человекочитаемое название источника."""
        mapping = {
            0: "UNSPECIFIED",
            1: "EXCHANGE",
            2: "DEALER_WEEKEND"
        }
        return mapping.get(self.candle_source, "UNKNOWN") if self.candle_source is not None else "UNKNOWN"
    
    def __repr__(self):
        status = "✓" if self.is_complete else "⏳"
        source = self.candle_source_name
        return (
            f"Candle({self.open_time.strftime('%Y-%m-%d %H:%M')} {status} [{source}], "
            f"O:{self.open} H:{self.high} L:{self.low} C:{self.close} V:{self.volume})"
        )
