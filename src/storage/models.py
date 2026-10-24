from sqlalchemy import Column, String, DateTime, Float, Integer, UniqueConstraint, Index
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class MarketData(Base):
    """Таблица котировок всех инструментов, всех таймфреймов"""
    __tablename__ = "marketdata"
    
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False)          # SBER, GAZP, MOEX
    timeframe = Column(String(10), nullable=False)       # 30m, 1h, 4h, 1d, 1w
    time = Column(DateTime, nullable=False)              # Время открытия свечи (UTC)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer)
    
    # Уникальное ограничение: одна свеча на инструмент+таймфрейм+время
    __table_args__ = (
        UniqueConstraint('ticker', 'timeframe', 'time', name='uix_ticker_tf_time'),
        Index('idx_ticker_timeframe_time', 'ticker', 'timeframe', 'time'),
    )
    
    def __repr__(self):
        return (
            f"<MarketData({self.ticker} {self.timeframe} {self.time.strftime('%Y-%m-%d %H:%M')}, "
            f"O:{self.open} H:{self.high} L:{self.low} C:{self.close})>"
        )
