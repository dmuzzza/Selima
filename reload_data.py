"""
Reload Market Data CLI

Скрипт для обновления/перезагрузки рыночных данных.
Использует DataUpdater с конфигурацией из config/instruments.json

Usage:
    python reload_data.py                    # Инкрементальное обновление (default)
    python reload_data.py --full             # Полная перезагрузка (14 дней)
    python reload_data.py --full --days 30   # Полная загрузка за 30 дней
    python reload_data.py --hours 6          # Инкрементальное за последние 6 часов
"""

import os
import sys
import argparse
import logging
from dotenv import load_dotenv

from src.market_data.data_updater import DataUpdater

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("ReloadData")


def main():
    """Главная функция."""
    
    # Парсинг аргументов
    parser = argparse.ArgumentParser(
        description="Market data reload utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reload_data.py                    # Quick update (last 2 hours)
  python reload_data.py --full             # Full reload (14 days)
  python reload_data.py --full --days 90   # Full reload (90 days)
  python reload_data.py --hours 12         # Update last 12 hours
        """
    )
    
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full reload instead of incremental update"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Days to load in full mode (default: 14)"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=2,
        help="Hours to check in incremental mode (default: 2)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/instruments.json",
        help="Path to instruments config (default: config/instruments.json)"
    )
    
    
    args = parser.parse_args()
    
    # Загружаем токен
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    
    if not token:
        logger.error("❌ TINKOFF_API_TOKEN not found in .env")
        sys.exit(1)
    
    # Создаём updater
    try:
        updater = DataUpdater(
            token=token, 
            config_path=args.config)
    except Exception as e:
        logger.error(f"❌ Failed to initialize DataUpdater: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Выполняем обновление
    try:
        if args.full:
            logger.info(f"🔄 Starting FULL RELOAD ({args.days} days)...")
            updater.full_reload(days_back=args.days)
        else:
            logger.info(f"📥 Starting INCREMENTAL UPDATE (last {args.hours} hours)...")
            updater.incremental_update(hours_back=args.hours)
        
        logger.info("\n✅ DATA UPDATE COMPLETED!")
    
    except KeyboardInterrupt:
        logger.warning("\n⚠️ Interrupted by user")
        sys.exit(130)
    
    except Exception as e:
        logger.error(f"❌ Update failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
