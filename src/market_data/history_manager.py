import logging
from datetime import datetime, timedelta, timezone
import pytz
from typing import Dict, List, Optional
import os

from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, UniqueConstraint, BigInteger, Numeric
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import func, text

from src.brokers.base import AbstractBroker
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe

logger = logging.getLogger(__name__)

# SQLAlchemy setup
Base = declarative_base()


class MarketData(Base):
    __tablename__ = "market_data"

    id           = Column(BigInteger, primary_key=True)
    figi         = Column(String(50), nullable=False, index=True)
    ticker       = Column(String(50), nullable=False, index=True)
    timeframe    = Column(String(10), nullable=False, index=True)
    open_time    = Column(DateTime(timezone=True), nullable=False, index=True)  # TIMESTAMPTZ
    open         = Column(Numeric(14, 6), nullable=False)
    high         = Column(Numeric(14, 6), nullable=False)
    low          = Column(Numeric(14, 6), nullable=False)
    close        = Column(Numeric(14, 6), nullable=False)
    volume       = Column(BigInteger, nullable=False)
    is_complete  = Column(Boolean, nullable=False, default=True)  # нативный bool
    candle_source = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint('figi', 'timeframe', 'open_time', name='uq_market_data'),
    )
    
    def __repr__(self):
        return f"<MarketData({self.figi}, {self.timeframe}, {self.open_time})>"


class HistoryManager:
    """
    Менеджер истории свечей.
    Хранит свечи в SQLite БД и обновляет их по требованию.
    """
    
    def __init__(self, broker: AbstractBroker):
        self.broker = broker

        db_url = (
            f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
            f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
            f"?sslmode=require"
        )

        self.engine = create_engine(db_url, pool_pre_ping=True)
        # Base.metadata.create_all НЕ НУЖЕН — таблица уже создана через DBeaver
        self.Session = sessionmaker(bind=self.engine)

        self._cache: Dict[str, List[Candle]] = {}
        logger.info("✓ HistoryManager initialized with PostgreSQL")

    
    
    @staticmethod
    def _convert_utc_to_msk(candles: List[Candle]) -> List[Candle]:
        """
        Конвертирует время свечей из UTC в MSK (UTC+3)
        """
        msk_tz = pytz.timezone("Europe/Moscow")
        converted = []
        for candle in candles:
            # Правильная конвертация час. поясов через astimezone()
            # Если candle.open_time уже aware — просто переводим
            # Если naive — считаем что это UTC и переводим в MSK
            if candle.open_time.tzinfo is not None:
                msk_time = candle.open_time.astimezone(msk_tz)
            else:
                msk_time = msk_tz.localize(candle.open_time)
            
            converted_candle = Candle(
                open_time=msk_time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                is_complete=candle.is_complete,        
                candle_source=candle.candle_source     
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

        # Берём UTC — API Tinkoff ожидает UTC-времена
        now_utc = datetime.now(timezone.utc)
        end_time = now_utc
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
                    volume=int(row.volume),
                    is_complete=bool(row.is_complete),
                    candle_source=row.candle_source
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
        session = self.Session()
        try:
            total = session.query(func.count(MarketData.id)).scalar()
            unique_figis = session.query(func.count(func.distinct(MarketData.figi))).scalar()
            return {
                "total_candles": total or 0,
                "unique_tickers": unique_figis or 0,
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
        
        # Последняя свеча хранится в БД как MSK — переводим в UTC для API
        last_candle_time = current_candles[-1].open_time
        if last_candle_time.tzinfo is not None:
            last_candle_utc = last_candle_time.astimezone(timezone.utc)
        else:
            last_candle_utc = last_candle_time.replace(tzinfo=timezone.utc)
        
        now_utc = datetime.now(timezone.utc)

        time_diff = now_utc - last_candle_utc
        
        # Если прошло мало времени, не спамим API
        if time_diff < timeframe.to_timedelta():
            logger.debug(f"Skipping update for {key} (too recent)")
            return
        
        logger.info(f"Updating {key} from {last_candle_utc}")
        
        try:
            start_time = last_candle_utc
            end_time = now_utc
            
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
    
    def _save_candles_to_db(self, figi: str, ticker: str, timeframe: Timeframe, candles: List[Candle]):
        """
        Сохраняет свечи в БД, игнорируя дубликаты на уровне БД.
        Использует INSERT OR IGNORE для эффективной обработки.
        """
        session = self.Session()
        try:
            candles = self._convert_utc_to_msk(candles)
            
            if not candles:
                logger.debug(f"No candles to save for {figi} {timeframe.value}")
                return
            
            # Подготавливаем данные для bulk insert
            rows_data = [
                {
                    'figi': figi,
                    'ticker': ticker,
                    'timeframe': timeframe.value,
                    'open_time': candle.open_time,
                    'open': candle.open,
                    'high': candle.high,
                    'low': candle.low,
                    'close': candle.close,
                    'volume': candle.volume,
                    'is_complete': candle.is_complete,  
                    'candle_source': candle.candle_source  
                }
                for candle in candles
            ]
            
            # UPSERT: если строка уже есть — обновляем все поля (включая is_complete)
            sql = text("""
                INSERT INTO market_data
                (figi, ticker, timeframe, open_time, open, high, low, close, volume,
                is_complete, candle_source)
                VALUES
                (:figi, :ticker, :timeframe, :open_time, :open, :high, :low, :close, :volume,
                :is_complete, :candle_source)
                ON CONFLICT (figi, timeframe, open_time) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    is_complete = EXCLUDED.is_complete,
                    candle_source = EXCLUDED.candle_source
            """)
            
            session.execute(sql, rows_data)
            session.commit()
            
            logger.info(
                f"✓ Processed {len(candles)} candles for {figi} {timeframe.value} "
                f"(duplicates auto-ignored)"
            )
            
            # Очищаем кэш
            key = self._get_key(figi, timeframe)
            if key in self._cache:
                del self._cache[key]
        
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving candles: {e}")
        
        finally:
            session.close()



    def clean_duplicates(self):
        """
        Удаляет дубликаты из БД.
        Оставляет только первую свечу для каждого времени (figi, timeframe, open_time).
        """
        session = self.Session()
        try:
            logger.info("🧹 Cleaning duplicates from database...")
            
            # Получаем все данные
            all_rows = session.query(MarketData).order_by(
                MarketData.figi,
                MarketData.timeframe,
                MarketData.open_time,
                MarketData.id
            ).all()
            
            # Отслеживаем какие (figi, timeframe, open_time) видели
            seen = set()
            to_delete = []
            
            for row in all_rows:
                key = (row.figi, row.timeframe, row.open_time)
                
                if key in seen:
                    # Это дубликат - помечаем на удаление
                    to_delete.append(row.id)
                else:
                    # Первый раз видим эту свечу
                    seen.add(key)
            
            # Удаляем дубликаты
            if to_delete:
                session.query(MarketData).filter(MarketData.id.in_(to_delete)).delete()
                session.commit()
                logger.info(f"✓ Removed {len(to_delete)} duplicate candles")
            else:
                logger.info("✓ No duplicates found")
            
            # Очищаем кэш полностью
            self._cache.clear()
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error cleaning duplicates: {e}")
        finally:
            session.close()
        


    @staticmethod
    def _get_key(figi: str, timeframe: Timeframe) -> str:
        """Ключ для кэша"""
        return f"{figi}_{timeframe.value}"