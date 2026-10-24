import os
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import pytz

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.candle_loader import CandleLoader
from src.market_data.timeframe import Timeframe

# Настройка
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ReloadData")

load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")

print("=" * 80)
print("HISTORICAL DATA LOADER (Smart Chunked)")
print("=" * 80)

# Создаем компоненты
logger.info("Creating broker...")
broker = BrokerFactory.create("sdk", token)
logger.info("✓ Broker created")

logger.info("Creating HistoryManager...")
history = HistoryManager(broker)
logger.info("✓ HistoryManager created")

# ============================================================
# ОЧИЩАЕМ СТАРЫЕ ДУБЛИКАТЫ
# ============================================================

print("\n" + "=" * 80)
print("CLEANING OLD DUPLICATES")
print("=" * 80)

history.clean_duplicates()
logger.info("✓ Duplicates cleaned")

# ============================================================
# ПАРАМЕТРЫ ЗАГРУЗКИ
# ============================================================

print("\n" + "=" * 80)
print("LOADING CONFIGURATION")
print("=" * 80)
import json

with open("config\instruments.json", "r", encoding="utf-8") as f:
    data = json.load(f)

INSTRUMENTS = [item["figi"] for item in data["instruments"]]
# INSTRUMENTS =['BBG000VJ5YR4', 'BBG0013HRTL0']
INSTRUMENTS.sort()
# Выбираемые инструменты


# Выбираемые таймфреймы
TIMEFRAMES = [
    # Timeframe.M30,
    # Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
]

# ⭐ ВЫБЕРИ ПЕРИОД (в днях)
# 14, 30, 90, 180, 365, 730 и т.д.
DAYS_BACK = 2190

# ============================================================

print(f"\nInstruments: {', '.join(INSTRUMENTS)}")
print(f"Timeframes: {', '.join(tf.value for tf in TIMEFRAMES)}")
print(f"Period: {DAYS_BACK} days")

# ============================================================
# ЗАГРУЖАЕМ ДАННЫЕ
# ============================================================

print("\n" + "=" * 80)
print("STARTING LOAD")
print("=" * 80)

logger.info("Creating CandleLoader...")
try:
    loader = CandleLoader(history, broker)
    logger.info("✓ CandleLoader created")
except Exception as e:
    logger.error(f"❌ Failed to create CandleLoader: {e}", exc_info=True)
    exit(1)

logger.info("Calculating date range...")
end_date = datetime.now(timezone.utc)
start_date = end_date - timedelta(days=DAYS_BACK)
logger.info(f"✓ Date range: {start_date} → {end_date}")

total_candles = 0

for instrument in INSTRUMENTS:
    for timeframe in TIMEFRAMES:
        logger.info(f"\n{'='*80}")
        logger.info(f"Loading {instrument} {timeframe.value}")
        logger.info(f"Period: {DAYS_BACK} days")
        logger.info(f"{'='*80}")
        
        try:
            logger.info(f"Calling loader.load_period()...")
            loaded, _ = loader.load_period(
                figi=instrument,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                verbose=True
            )
            logger.info(f"✓ Loaded {loaded} candles for {instrument} {timeframe.value}")
            total_candles += loaded
        except Exception as e:
            logger.error(f"❌ Error loading {instrument} {timeframe.value}: {e}", exc_info=True)
            continue

# ============================================================
# СТАТИСТИКА
# ============================================================

print("\n" + "=" * 80)
print("DATABASE STATISTICS")
print("=" * 80)

logger.info("Fetching database statistics...")
try:
    stats = history.get_stats()
    
    print(f"\n✓ Total candles: {stats['total_candles']}")
    print(f"✓ Unique instruments: {stats['unique_tickers']}")
    
    
    # Показываем доступные комбинации
    available = history.get_available_combinations()
    
    print(f"\n✓ Available combinations:")
    for figi, timeframes_list in available.items():
        print(f"  {figi}: {', '.join(timeframes_list)}")
except Exception as e:
    logger.error(f"❌ Error fetching statistics: {e}", exc_info=True)

print("\n" + "=" * 90)
print(f"✓ DATA LOADED SUCCESSFULLY! (Total: {total_candles} candles)")
print("=" * 90)

