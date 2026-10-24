# src/market_data/candle_loader.py

"""
Умный загрузчик исторических свечей с поддержкой лимитов API.
Автоматически дробит большие периоды на части.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Tuple
import pytz

from src.brokers.base import AbstractBroker
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe

logger = logging.getLogger(__name__)


# API лимиты для T-банка (максимум свечей за один запрос)
API_LIMITS = {
    #Timeframe.M1: 2300,      # 1 минута: максимум 1 свеча
    #Timeframe.M5: 2300,     # 5 минут: максимум 10 свечей
    #Timeframe.M15: 2300,    # 15 минут: максимум 30 свечей
    Timeframe.M30: 1100,   # 30 минут: максимум 150 свечей
    Timeframe.H1: 2300,    # 1 час: максимум 500 свечей
    Timeframe.H4: 600,    # 4 часа: максимум 500 свечей
    Timeframe.D1: 2300,  # 1 день: максимум 10000 свечей
}


class CandleLoader:
    """
    Загрузчик свечей с учетом API лимитов.
    """
    
    def __init__(self, history_manager: HistoryManager, broker: AbstractBroker):
        self.history = history_manager
        self.broker = broker
    
    def load_period(
        self,
        figi: str,
        timeframe: Timeframe,
        start_date: datetime,
        end_date: datetime,
        verbose: bool = True
    ) -> Tuple[int, int]:
        """
        Загрузить свечи за период, автоматически дробя на chunks.
        
        Args:
            figi: ID инструмента
            timeframe: Таймфрейм (M30, H1, D1 и т.д.)
            start_date: Дата начала (UTC)
            end_date: Дата конца (UTC)
            verbose: Выводить ли логи прогресса
        
        Returns:
            (total_loaded, total_skipped) - количество загруженных и пропущенных свечей
        """
        
        logger.info(f"[load_period START] figi={figi}, timeframe={timeframe.value}, start={start_date}, end={end_date}")
        
        # Вычисляем chunks на основе лимитов
        logger.info(f"[load_period] Calculating chunks...")
        try:
            chunks = self._calculate_chunks(timeframe, start_date, end_date)
            logger.info(f"[load_period] ✓ Chunks calculated: {len(chunks)} chunks")
        except Exception as e:
            logger.error(f"[load_period] ❌ Error calculating chunks: {e}", exc_info=True)
            raise
        
        if verbose:
            print(f"\n{'='*80}")
            print(f"Loading {figi} {timeframe.value}")
            print(f"Period: {start_date} → {end_date}")
            print(f"Total chunks: {len(chunks)}")
            print(f"{'='*80}")
        
        total_loaded = 0
        total_skipped = 0
        
        # Загружаем каждый chunk
        for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
            logger.info(f"[chunk {i}/{len(chunks)}] Start loading: {chunk_start} → {chunk_end}")
            
            if verbose:
                print(f"\n[{i}/{len(chunks)}] Loading {chunk_start} → {chunk_end}")
            
            try:
                # Получаем свечи от API
                logger.info(f"[chunk {i}] Calling broker.get_candles()...")
                candles = self.broker.get_candles(figi, timeframe, chunk_start, chunk_end)
                logger.info(f"[chunk {i}] ✓ Got {len(candles) if candles else 0} candles from API")
                
                if not candles:
                    if verbose:
                        print(f"  ⚠️  No candles received")
                    logger.warning(f"[chunk {i}] No candles received")
                    continue
                
                # Получаем тикер
                logger.info(f"[chunk {i}] Getting ticker for {figi}...")
                ticker = self._get_ticker(figi)
                logger.info(f"[chunk {i}] ✓ Ticker: {ticker}")
                
                # Сохраняем в БД (INSERT OR IGNORE автоматически пропустит дубликаты)
                logger.info(f"[chunk {i}] Saving {len(candles)} candles to DB...")
                self.history._save_candles_to_db(figi, ticker, timeframe, candles)
                logger.info(f"[chunk {i}] ✓ Saved to DB")
                
                loaded = len(candles)
                total_loaded += loaded
                
                if verbose:
                    print(f"  ✅ Loaded {loaded} candles")
                
                logger.info(f"[chunk {i}] ✓ Chunk complete. Total loaded so far: {total_loaded}")
            
            except Exception as e:
                logger.error(f"[chunk {i}] ❌ Error: {e}", exc_info=True)
                if verbose:
                    print(f"  ❌ Error: {e}")
                continue
        
        if verbose:
            print(f"\n{'='*80}")
            print(f"Total loaded: {total_loaded} candles")
            print(f"{'='*80}\n")
        
        logger.info(f"[load_period END] Total loaded: {total_loaded}")
        
        return total_loaded, total_skipped
    
    def _calculate_chunks(
        self,
        timeframe: Timeframe,
        start_date: datetime,
        end_date: datetime
    ) -> List[Tuple[datetime, datetime]]:
        """
        Разбить период на chunks размером по лимиту API.
        
        Возвращает список пар (start, end) для каждого chunk.
        """
        
        logger.info(f"[_calculate_chunks] START: timeframe={timeframe.value}, start={start_date}, end={end_date}")
        
        limit = API_LIMITS.get(timeframe, 2400)
        logger.info(f"[_calculate_chunks] API limit for {timeframe.value}: {limit}")
        
        tf_delta = timeframe.to_timedelta
        logger.info(f"[_calculate_chunks] ✓ to_timedelta: {tf_delta}")
        
        chunk_duration = tf_delta * (limit - 1)
        logger.info(f"[_calculate_chunks] ✓ chunk_duration: {chunk_duration}")
        
        # ⚠️ API Tinkoff лимит: для 4H период запроса ≤ 3 месяца
        if timeframe == Timeframe.H4:
            chunk_duration = min(chunk_duration, timedelta(days=89))
        
        logger.info(f"[_calculate_chunks] ✓ chunk_duration (after limits): {chunk_duration}")
        
        logger.info(f"[_calculate_chunks] Starting chunk calculation loop...")
        chunks = []
        current_start = start_date
        iteration = 0
        
        while current_start < end_date:
            iteration += 1
            
            chunk_end = min(current_start + chunk_duration, end_date)
            chunks.append((current_start, chunk_end))
            
            logger.info(f"[_calculate_chunks] Iteration {iteration}: {current_start} → {chunk_end}")
            
            # ⭐ ВАЖНО: Если достигли конца - выходим
            if chunk_end >= end_date:
                logger.info(f"[_calculate_chunks] Reached end_date, breaking loop")
                break
            
            # Смещаемся на новый chunk с перекрытием в 5 свечей
            overlap = tf_delta * 5
            current_start = chunk_end - overlap
            
            # Защита от бесконечного цикла
            if iteration > 10000:
                logger.error(f"[_calculate_chunks] ❌ Too many iterations! Breaking loop.")
                break
        
        logger.info(f"[_calculate_chunks] ✓ END: Total chunks calculated: {len(chunks)}")
        
        return chunks
    
    def _get_ticker(self, figi: str) -> str:
        """Получить тикер по FIGI"""
        try:
            instrument = self.broker.get_instrument_by_figi(figi)
            if instrument:
                return instrument.get("ticker", figi)
        except Exception as e:
            logger.warning(f"Could not get ticker for {figi}: {e}")
        
        return figi


def load_historical_data(
    history_manager: HistoryManager,
    broker: AbstractBroker,
    instruments: List[str],
    timeframes: List[Timeframe],
    days_back: int = 365,
    verbose: bool = True
):
    """
    Удобная функция для загрузки исторических данных.
    
    Args:
        history_manager: Экземпляр HistoryManager
        broker: Экземпляр брокера
        instruments: Список FIGI для загрузки
        timeframes: Список таймфреймов
        days_back: Количество дней назад для загрузки
        verbose: Выводить логи
    """
    
    loader = CandleLoader(history_manager, broker)
    
    msk_tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(msk_tz)      
    end_date = now
    start_date = end_date - timedelta(days=days_back)
    
    total_stats = {"loaded": 0, "instruments": len(instruments), "timeframes": len(timeframes)}
    
    for figi in instruments:
        for tf in timeframes:
            loaded, _ = loader.load_period(
                figi=figi,
                timeframe=tf,
                start_date=start_date,
                end_date=end_date,
                verbose=verbose
            )
            total_stats["loaded"] += loaded
    
    return total_stats
