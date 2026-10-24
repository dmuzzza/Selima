import os
import logging
from dotenv import load_dotenv

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe

# Настройка
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ReloadData")

load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")

print("=" * 80)
print("RELOADING DATA INTO NEW DATABASE")
print("=" * 80)

# Создаем компоненты с НОВЫМ HistoryManager
logger.info("Creating broker...")
broker = BrokerFactory.create("sdk", token)

logger.info("Creating HistoryManager with new SQLite backend...")
history = HistoryManager(broker, db_path="marketdata.db")

# ============================================================
# ОЧИЩАЕМ СТАРЫЕ ДУБЛИКАТЫ
# ============================================================
print("\n" + "=" * 80)
print("CLEANING OLD DUPLICATES")
print("=" * 80)
history.clean_duplicates()


# Инструменты и таймфреймы для загрузки
instruments = [
    "BBG004730JJ5", "BBG004S68BH6", "BBG004730ZJ9", "TCS80A107UL4", 
    "BBG004730N88", "BBG00475KKY8", "BBG004S683W7", "BBG004731032", 
    "TCS03A108X38", "BBG004S681M2", "BBG004S68614", "BBG00475K6C3",
    "TCS00A1002V2", "BBG000R04X57"
    ]  # "BBG004730N88",  SBER
timeframes = [Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1]

print("\n" + "=" * 80)
print("LOADING DATA")
print("=" * 80)

# Загружаем данные
history.load_initial_history(
    ticker_list=instruments,
    timeframes=timeframes,
    days_back=14
)

# Показываем статистику
print("\n" + "=" * 80)
print("DATABASE STATISTICS")
print("=" * 80)

stats = history.get_stats()
print(f"\n✓ Total candles: {stats['total_candles']}")
print(f"✓ Unique instruments: {stats['unique_tickers']}")
print(f"✓ DB size: {stats['db_size_mb']} MB")

# Показываем доступные комбинации
available = history.get_available_combinations()
print(f"\n✓ Available combinations:")
for figi, timeframes_list in available.items():
    print(f"  {figi}: {', '.join(timeframes_list)}")

print("\n" + "=" * 80)
print("✓ DATA RELOADED SUCCESSFULLY!")
print("=" * 80)
