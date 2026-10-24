import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import func

from src.brokers.base import AbstractBroker
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe

logger = logging.getLogger(__name__)

# SQLAlchemy setup
Base = declarative_base()


class MarketData(Base):
    """Модель для хранения свечей в БД"""
    __tablename__ = "market_data"
    
    id = Column(Integer, primary_key=True)
    figi = Column(String(50), nullable=False, index=True)
    ticker = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)
    open_time = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    
    def __repr__(self):
        return f"<MarketData({self.figi}, {self.timeframe}, {self.open_time})>"


class HistoryManager:
    """
    Менеджер истории свечей.
    Хранит свечи в SQLite БД и обновляет их по требованию.
    """
    
    def __init__(self, broker: AbstractBroker, db_path: str = "marketdata.db"):
        self.broker = broker
        self.db_path = db_path
        
        # Создать папку, если её нет
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Инициализируем БД
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        # Кэш в памяти для быстрого доступа
        self._cache: Dict[str, List[Candle]] = {}
        
        logger.info(f"✓ HistoryManager initialized with DB: {db_path}")
    
    @staticmethod
    def _convert_utc_to_msk(candles: List[Candle]) -> List[Candle]:
        """
        Конвертирует время свечей из UTC в MSK (UTC+3)
        """
        converted = []
        for candle in candles:
            # Добавляем 3 часа
            msk_time = candle.open_time + timedelta(hours=3)
            
            # Создаем новую свечу с MSK временем
            converted_candle = Candle(
                open_time=msk_time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume
            )
            converted.append(converted_candle)
        
        return converted


    def get_ticker_by_figi(self, figi: str) -> str:
        """
        Получает тикер по FIGI из брокера
        """
        try:
            # Получаем инструмент от брокера
            instrument = self.broker.get_instrument_by_figi(figi)
            if instrument:
                return instrument.ticker  # или instrument.name
            return figi  # fallback на FIGI если не найдено
        except Exception as e:
            logger.warning(f"Failed to get ticker for {figi}: {e}")
            return figi

    def load_initial_history(
        self,
        ticker_list: List[str],
        timeframes: List[Timeframe],
        days_back: int = 365
    ):
        """Загрузить начальную историю для инструментов"""
        logger.info(f"Loading history for {len(ticker_list)} instruments...")

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days_back)

        for figi in ticker_list:
            # 🔑 Получаем тикер по FIGI (результат будет dict: {figi, ticker, name})
            instrument_info = self.broker.get_instrument_by_figi(figi)

            if instrument_info:
                ticker = instrument_info["ticker"]
                logger.info(f"✓ Resolved FIGI {figi} → ticker {ticker}")
            else:
                # Fallback: используем FIGI как тикер если API недоступно
                ticker = figi
                logger.warning(f"⚠ Could not resolve ticker for {figi}, using FIGI as ticker")

            # Загружаем данные для всех таймфреймов
            for tf in timeframes:
                self._fetch_and_cache_to_db(figi, ticker, tf, start_time, end_time)
        
    def get_candles(self, figi: str, timeframe: Timeframe) -> List[Candle]:
        """
        Получить свечи ИЗ БД ТОЛЬКО (без fallback на API).
        
        Args:
            figi: Инструмент (BBG004730N88, etc)
            timeframe: Таймфрейм (H1, D1, etc)
        
        Returns:
            List[Candle]: Свечи из БД, или пусто если нет данных
        """
        key = self._get_key(figi, timeframe)
        
        # Проверяем кэш в памяти
        if key in self._cache:
            return self._cache[key]
        
        # Получаем из БД
        session = self.Session()
        try:
            rows = session.query(MarketData).filter(
                MarketData.figi == figi,
                MarketData.timeframe == timeframe.value
            ).order_by(MarketData.open_time).all()
            
            candles = [
                Candle(
                    open_time=row.open_time,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume)
                )
                for row in rows
            ]
            
            # Кэшируем в памяти
            self._cache[key] = candles
            return candles
        finally:
            session.close()
    
    def get_available_combinations(self) -> Dict[str, List[str]]:
        """
        Возвращает словарь доступных комбинаций (figi, timeframe) из БД.
        Используется для построения dropdown'ов в Dash.
        
        Returns:
            Dict[str, List[str]]: {figi: [timeframe1, timeframe2, ...]}
        """
        session = self.Session()
        try:
            # Получаем уникальные (figi, timeframe) из БД
            rows = session.query(
                MarketData.figi,
                MarketData.timeframe
            ).distinct().all()
            
            result = {}
            for figi, tf in rows:
                if figi not in result:
                    result[figi] = []
                result[figi].append(tf)
            
            # Сортируем для читаемости
            for figi in result:
                result[figi].sort()
            
            return result
        finally:
            session.close()
    
    def get_last_candles(self, figi: str, timeframe: Timeframe, limit: int = 5) -> List[Candle]:
        """
        Получить последние N свечей для быстрого анализа.
        """
        all_candles = self.get_candles(figi, timeframe)
        return all_candles[-limit:] if all_candles else []
    
    def get_stats(self) -> Dict:
        """Получить статистику по всей БД"""
        session = self.Session()
        try:
            total = session.query(func.count(MarketData.id)).scalar()
            unique_figis = session.query(func.count(func.distinct(MarketData.figi))).scalar()
            
            # Размер файла БД
            import os
            db_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            
            return {
                "total_candles": total or 0,
                "unique_tickers": unique_figis or 0,
                "db_size_mb": round(db_size_mb, 2),
            }
        finally:
            session.close()
    
    def update_history(self, figi: str, timeframe: Timeframe):
        """
        Обновить историю (добавить новые свечи за последние дни).
        Вызывай периодически для обновления данных.
        """
        key = self._get_key(figi, timeframe)
        
        # Получаем все существующие свечи
        current_candles = self.get_candles(figi, timeframe)
        
        if not current_candles:
            logger.warning(f"No history for {key}. Use load_initial_history() first.")
            return
        
        # Берем время последней свечи
        last_candle_time = current_candles[-1].open_time
        
        # Если прошло мало времени, не спамим API
        if (datetime.utcnow() - last_candle_time) < timeframe.to_timedelta:
            logger.debug(f"Skipping update for {key} (too recent)")
            return
        
        logger.info(f"Updating {key} from {last_candle_time}")
        
        try:
            start_time = last_candle_time
            end_time = datetime.utcnow()
            
            new_candles = self.broker.get_candles(figi, timeframe, start_time, end_time)
            
            if not new_candles:
                logger.debug(f"No new candles for {key}")
                return
            
            # Сохраняем новые свечи в БД
            self._save_candles_to_db(figi, timeframe, new_candles)
            
            # Очищаем кэш для этого ключа
            if key in self._cache:
                del self._cache[key]
            
            logger.info(f"Added {len(new_candles)} new candles to {key}")
            
        except Exception as e:
            logger.error(f"Failed to update history for {key}: {e}")
    
    def _fetch_and_cache_to_db(
        self,
        figi: str,
        ticker: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime
    ):
        """Загружает свечи из API и сохраняет в БД"""
        try:
            logger.info(f"Fetching {figi} {timeframe.value} from API ({(end - start).days} days)...")
            
            candles = self.broker.get_candles(figi, timeframe, start, end)
            
            if not candles:
                logger.warning(f"No candles received for {figi} {timeframe.value}")
                return
            
            # Сохраняем в БД
            self._save_candles_to_db(figi, ticker, timeframe, candles)
            
            logger.info(f"✓ Loaded {len(candles)} new candles to DB")
            
        except Exception as e:
            logger.error(f"Error fetching {figi} {timeframe.value}: {e}")
    
    def _save_candles_to_db(
        self,
        figi: str,
        ticker: str,
        timeframe: Timeframe,
        candles: List[Candle]
    ):
        """Сохраняет свечи в БД, избегая дубликатов"""
        session = self.Session()
        try:
            candles = self._convert_utc_to_msk(candles)
            # Получаем существующие времена для этого (figi, timeframe)
            existing_times = set(
                row[0] for row in session.query(MarketData.open_time).filter(
                    MarketData.figi == figi,
                    MarketData.timeframe == timeframe.value
                ).all()
            )
            
            # Добавляем только новые свечи
            added_count = 0
            for candle in candles:
                if candle.open_time not in existing_times:
                    row = MarketData(
                        figi=figi,
                        ticker=ticker,
                        timeframe=timeframe.value,
                        open_time=candle.open_time,
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume
                    )
                    session.add(row)
                    added_count += 1
            
            session.commit()
            logger.debug(f"Saved {added_count} new candles for {figi} {timeframe.value}")
            
            # Очищаем кэш
            key = self._get_key(figi, timeframe)
            if key in self._cache:
                del self._cache[key]
                
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving candles: {e}")
        finally:
            session.close()
    
    @staticmethod
    def _get_key(figi: str, timeframe: Timeframe) -> str:
        """Ключ для кэша"""
        return f"{figi}_{timeframe.value}"