import logging
from datetime import datetime
from typing import List, Union, Optional
import os
os.environ['SSL_TBANK_VERIFY'] = 'True'
from t_tech.invest import Client
from t_tech.invest.schemas import CandleInterval

from .base import AbstractBroker
from src.market_data.models import Candle  # ← Для работы с данными
from src.storage.models import MarketData  # ← Для БД (если понадобится)
from src.market_data.timeframe import Timeframe

logger = logging.getLogger(__name__)


class TinkoffSDKBroker(AbstractBroker):
    """Подключение через официальный SDK T-Invest"""
    
    def __init__(self, token: str):
        self.token = token
        self._instrument_cache = {}  # ← НОВОЕ: Кэш инструментов
        logger.info("✓ TinkoffSDKBroker initialized")
    
    def get_candles(
        self,
        figi: str,
        timeframe: Union[str, Timeframe],
        start_time: datetime,
        end_time: datetime,
    ) -> List[Candle]:
        """Загрузить свечи через SDK"""
        
        interval = self._map_timeframe(timeframe)
        candles = []
        
        try:
            with Client(self.token) as client:
                # ✅ Используем get_all_candles — он пагинирует и вернёт ВСЕ свечи за период
                raw_candles = list(client.get_all_candles(
                    figi=figi,
                    from_=start_time,
                    to=end_time,
                    interval=interval,
                ))

            # Преобразуем ответ API в наши модели
            for candle in raw_candles:
                if not candle.is_complete:   # 🔑 пропускаем незавершённую свечу
                    continue
                candles.append(
                    Candle(
                        open_time=candle.time,
                        open=float(candle.open.units) + float(candle.open.nano) / 1e9,
                        high=float(candle.high.units) + float(candle.high.nano) / 1e9,
                        low=float(candle.low.units) + float(candle.low.nano) / 1e9,
                        close=float(candle.close.units) + float(candle.close.nano) / 1e9,
                        volume=candle.volume,

                        is_complete=candle.is_complete,      # ✅ Как есть из API
                        candle_source=candle.candle_source   # ✅ int (0, 1, 2)
                    )
                )
            
            tf_str = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
            logger.info(f"✓ Loaded {len(candles)} candles for {figi} {tf_str}")
            candles = [c for c in candles if c.is_complete]
            return candles
        
        except Exception as e:
            logger.error(f"✗ Failed to load candles: {e}")
            raise
    
    # ✅ НОВЫЙ ВСПОМОГАТЕЛЬНЫЙ МЕТОД
    @staticmethod
    def _parse_candle_source(candle_source) -> Optional[str]:
        """
        Конвертирует CandleSource из Tinkoff API в строку.
        
        Возвращает как есть, без подмены на дефолты.
        """
        if candle_source is None:
            return None
        
        # Если это enum с атрибутом name
        if hasattr(candle_source, 'name'):
            return candle_source.name
        
        # Если это int - маппим
        if isinstance(candle_source, int):
            mapping = {
                0: "CANDLE_SOURCE_UNSPECIFIED",
                1: "CANDLE_SOURCE_EXCHANGE",
                2: "CANDLE_SOURCE_DEALER_WEEKEND"
            }
            return mapping.get(candle_source)  # ✅ Без дефолта! Вернёт None если неизвестно
        
        # Если строка - возвращаем как есть
        if isinstance(candle_source, str):
            return candle_source
        
        # Если что-то другое - None
        return None
    
    def get_accounts(self) -> list:
        """Получить список аккаунтов"""
        try:
            with Client(self.token) as client:
                response = client.users.get_accounts()
            
            accounts = [
                {
                    "id": acc.id,
                    "name": acc.name,
                }
                for acc in response.accounts
            ]
            logger.info(f"✓ Found {len(accounts)} accounts")
            return accounts
        
        except Exception as e:
            logger.error(f"✗ Failed to get accounts: {e}")
            raise
    
    
    def get_instrument_by_figi(self, figi: str) -> Optional[dict]:
        """
        Получить информацию об инструменте (тикер) по FIGI.
        Результат кэшируется для оптимизации.
        
        Args:
            figi: FIGI код инструмента (BBG004730N88 для SBER)
            
        Returns:
            dict с ключами: figi, ticker, name
            или None если не найдено
        """
        # Проверяем кэш
        if figi in self._instrument_cache:
            return self._instrument_cache[figi]

        try:
            with Client(self.token) as client:
                # Получаем инструмент по FIGI
                response = client.instruments.get_instrument_by(
                    id_type=1,  # FIGI
                    class_code="",
                    id=figi
                )

                if response and response.instrument:
                    instrument = response.instrument
                    info = {
                        "figi": instrument.figi,
                        "ticker": instrument.ticker,
                        "name": instrument.name or ""
                    }
                    # Кэшируем результат
                    self._instrument_cache[figi] = info
                    logger.info(f"✓ Found instrument: {info['ticker']} ({figi})")
                    return info
                else:
                    logger.warning(f"⚠ Instrument not found for FIGI: {figi}")
                    return None

        except Exception as e:
            logger.error(f"✗ Failed to get instrument by FIGI {figi}: {e}")
            return None




    @staticmethod
    def _map_timeframe(timeframe: Union[str, Timeframe]) -> CandleInterval:
        """
        Маппинг таймфреймов на SDK константы
        
        Args:
            timeframe: Строка ("1h", "30m") или Enum (Timeframe.H1, Timeframe.M30)
        
        Returns:
            CandleInterval константа для SDK
        """
        # Если Enum, извлекаем значение
        if isinstance(timeframe, Timeframe):
            timeframe_str = timeframe.value
        else:
            timeframe_str = timeframe
        
        mapping = {
            "30m": CandleInterval.CANDLE_INTERVAL_30_MIN,
            "1h": CandleInterval.CANDLE_INTERVAL_HOUR,
            "4h": CandleInterval.CANDLE_INTERVAL_4_HOUR,
            "1d": CandleInterval.CANDLE_INTERVAL_DAY,
            "1w": CandleInterval.CANDLE_INTERVAL_WEEK,
        }
        return mapping.get(timeframe_str, CandleInterval.CANDLE_INTERVAL_HOUR)


