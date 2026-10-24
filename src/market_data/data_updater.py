"""
Data Updater

Умная обёртка над HistoryManager для обновления данных:
- Загрузка конфигурации из JSON
- Инкрементальные обновления (только новые свечи)
- Полная перезагрузка истории
- Статистика и логирование
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from src.brokers.base import AbstractBroker 

from src.market_data.timeframe import Timeframe
from src.market_data.history_manager import HistoryManager
from src.brokers.broker_factory import BrokerFactory
from datetime import datetime, timedelta, timezone 
logger = logging.getLogger(__name__)


class InstrumentConfig:
    """Конфигурация инструмента из JSON."""
    
    def __init__(self, data: dict):
        self.figi: str = data['figi']
        self.ticker: str = data['ticker']
        self.enabled: bool = data.get('enabled', True)
        self.timeframes: List[str] = data.get('timeframes', ['1h', '4h', '1d'])
    
    def __repr__(self):
        status = "✓" if self.enabled else "✗"
        return f"{status} {self.ticker} ({self.figi})"


class DataUpdater:
    """
    Умный апдейтер рыночных данных.
    
    Использует HistoryManager который уже имеет защиту от дубликатов
    через UniqueConstraint и INSERT OR IGNORE.
    """
    
    def __init__(
        self,
        token: str,
        config_path: str = "config/instruments.json",
        history_manager: Optional[HistoryManager] = None,  # 🔑 добавили
        broker: Optional[AbstractBroker] = None,            # 🔑 добавили
    ):
        """
        Инициализация.

        Args:
            token: API токен
            config_path: Путь к конфигу инструментов
            history_manager: Общий экземпляр HistoryManager (DI). Если None —
                создаётся новый (для обратной совместимости).
            broker: Общий экземпляр брокера (DI). Если None — создаётся новый.
        """
        self.token = token

        self.config_path = Path(config_path)

        # Переиспользуем переданный broker, если он есть
        self.broker = broker or BrokerFactory.create("sdk", token)

        # 🔑 КЛЮЧЕВОЙ ФИКС: используем переданный HistoryManager вместо
        # создания нового со своим изолированным _cache
        self.history = history_manager or HistoryManager(self.broker)

        # Загружаем конфигурацию
        self.instruments: List[InstrumentConfig] = []
        self._load_config()

        logger.info(
            f"✓ DataUpdater initialized: "
            f"{len(self.instruments)} instruments loaded "
            f"(shared_history_manager={history_manager is not None})"
        )
    
    def _load_config(self):
        """Загрузить конфигурацию инструментов из JSON."""
        if not self.config_path.exists():
            logger.error(f"Config not found: {self.config_path}")
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Парсим инструменты
        for item in config.get('instruments', []):
            inst = InstrumentConfig(item)
            if inst.enabled:
                self.instruments.append(inst)
        
        logger.info(f"✓ Loaded {len(self.instruments)} enabled instruments")
        
        # Показываем список
        for inst in self.instruments:
            logger.debug(f"   {inst}")
    
    def get_enabled_instruments(self) -> List[InstrumentConfig]:
        """Получить список активных инструментов."""
        return self.instruments
    
    def full_reload(self, days_back: int = 14):
        """
        ПОЛНАЯ ПЕРЕЗАГРУЗКА данных.
        
        Для первичной загрузки или полного обновления.
        
        Args:
            days_back: Сколько дней истории загружать
        """
        logger.info("=" * 80)
        logger.info(f"🔄 FULL RELOAD: Loading {days_back} days of history")
        logger.info("=" * 80)
        
        # Очищаем дубликаты перед загрузкой
        logger.info("🧹 Cleaning existing duplicates...")
        self.history.clean_duplicates()
        
        # Собираем все уникальные FIGI и таймфреймы
        all_figis = set()
        all_timeframes = set()
        
        for inst in self.get_enabled_instruments():
            all_figis.add(inst.figi)
            for tf_str in inst.timeframes:
                all_timeframes.add(self._str_to_timeframe(tf_str))
        
        logger.info(f"📊 Configuration:")
        logger.info(f"   Instruments: {len(all_figis)}")
        logger.info(f"   Timeframes: {[tf.value for tf in sorted(all_timeframes, key=lambda x: x.value)]}")
        logger.info(f"   Period: {days_back} days back")
        
        # ✅ ИСПОЛЬЗУЕМ СУЩЕСТВУЮЩИЙ МЕТОД HistoryManager
        # Он уже имеет защиту от дубликатов через INSERT OR IGNORE
        self.history.load_initial_history(
            ticker_list=list(all_figis),
            timeframes=list(all_timeframes),
            days_back=days_back
        )
        
        # Статистика
        self._print_stats()
    
    def incremental_update(self, hours_back: int = 2):
        """
        ИНКРЕМЕНТАЛЬНОЕ ОБНОВЛЕНИЕ (только новые свечи).
        
        Загружает только недостающие данные с последнего обновления.
        Оптимально для частых запусков (каждые 15 минут).
        
        Args:
            hours_back: Сколько часов назад проверять (для перестраховки)
        """
        logger.info("=" * 80)
        logger.info(f"📥 INCREMENTAL UPDATE: Last {hours_back} hours")
        logger.info("=" * 80)
        
        total_updated = 0
        total_skipped = 0
        
        for inst in self.get_enabled_instruments():
            logger.info(f"\n📈 {inst.ticker} ({inst.figi})...")
            
            for tf_str in inst.timeframes:
                # 🔧 ВРЕМЕННО: загружаем только 4h
                if tf_str != "4h":
                    continue
                timeframe = self._str_to_timeframe(tf_str)

                try:
                    # Получаем последнюю свечу из БД
                    existing_candles = self.history.get_candles(inst.figi, timeframe)
                    
                    if not existing_candles:
                        logger.warning(f"  ⚠️ {tf_str}: No data. Run full_reload first.")
                        continue
                    
                    last_candle = existing_candles[-1]

                    last_time = last_candle.open_time

                    # open_time из БД хранится в MSK (см. _convert_utc_to_msk).
                    # Для запроса к API нужен UTC — убираем 3 часа и tzinfo.
                    if last_time.tzinfo is not None:
                        utc_time = last_time.astimezone(timezone.utc)
                    else:
                        utc_time = last_time.replace(tzinfo=timezone.utc)

                    from_date = utc_time - timedelta(hours=hours_back)
                    to_date = datetime.now(timezone.utc)
                    
                    # DEBUG: временно добавь эти строки
                    # logger.info(f"  🕐 last_candle.open_time (raw from DB): {last_candle.open_time} (tzinfo={last_candle.open_time.tzinfo})")
                    # logger.info(f"  🕐 from_date: {from_date}")
                    # logger.info(f"  🕐 to_date:   {to_date}")
                    # logger.info(f"  🕐 from > to: {from_date > to_date}")

                    # Загружаем новые свечи через broker
                    new_candles = self.broker.get_candles(
                        figi=inst.figi,
                        timeframe=timeframe,
                        start_time=from_date,
                        end_time=to_date
                    )
                    
                    if not new_candles:
                        logger.debug(f"  ℹ️ {tf_str}: No new candles")
                        total_skipped += 1
                        continue
                    
                    # Считаем сколько было до
                    before_count = len(existing_candles)
                    
                    # ✅ СОХРАНЯЕМ через HistoryManager (с защитой от дубликатов)
                    self.history._save_candles_to_db(
                        figi=inst.figi,
                        ticker=inst.ticker,
                        timeframe=timeframe,
                        candles=new_candles
                    )
                    
                    # Считаем сколько добавилось
                    updated_candles = self.history.get_candles(inst.figi, timeframe)
                    after_count = len(updated_candles)
                    added = after_count - before_count
                    
                    if added > 0:
                        logger.info(f"  ✓ {tf_str}: +{added} new candles")
                        total_updated += added
                    else:
                        logger.debug(f"  ℹ️ {tf_str}: Already up-to-date")
                        total_skipped += 1
                
                except Exception as e:
                    logger.error(f"  ✗ {tf_str}: Error - {e}")
        
        logger.info(f"\n{'='*80}")
        logger.info(f"📊 Update completed:")
        logger.info(f"   New candles added: {total_updated}")
        logger.info(f"   Already up-to-date: {total_skipped}")
        logger.info(f"{'='*80}")
    
    def _print_stats(self):
        """Вывести статистику БД."""
        logger.info("\n" + "=" * 80)
        logger.info("📊 DATABASE STATISTICS")
        logger.info("=" * 80)
        
        stats = self.history.get_stats()
        logger.info(f"\n✓ Total candles: {stats['total_candles']:,}")
        logger.info(f"✓ Unique instruments: {stats['unique_tickers']}")
        
        
        # Доступные комбинации
        available = self.history.get_available_combinations()
        logger.info(f"\n✓ Available combinations ({len(available)} instruments):")
        
        for figi, timeframes_list in sorted(available.items()):
            # Находим тикер
            ticker = "???"
            for inst in self.instruments:
                if inst.figi == figi:
                    ticker = inst.ticker
                    break
            
            logger.info(f"   {ticker:6} ({figi}): {', '.join(sorted(timeframes_list))}")
        
        logger.info("=" * 80)
    
    @staticmethod
    def _str_to_timeframe(tf_str: str) -> Timeframe:
        """Конвертировать строку в Timeframe enum."""
        mapping = {
            "30m": Timeframe.M30,
            "1h": Timeframe.H1,
            "4h": Timeframe.H4,
            "1d": Timeframe.D1,
        }
        result = mapping.get(tf_str)
        if not result:
            raise ValueError(f"Unknown timeframe: {tf_str}")
        return result
