"""
Trading Scheduler - PRODUCTION

Планировщик для автоматической торговли с приоритетами:
- 🔴 P0 (КРИТИЧНО): Обновление данных + Открытие сделок (каждые 30 мин)
- 🟡 P1 (ВАЖНО): Сбор стоп-ордеров (каждые 2 мин)
- 🟢 P2 (ФОНОВОЕ): Мониторинг позиций (каждые 15 мин)

Работает: 05:00 - 00:30 MSK
"""

import os
import sys
import logging
import threading
import signal
import pytz
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from enum import IntEnum
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# Импорты из проекта
from src.market_data.data_updater import DataUpdater
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe
from src.backtest.exit_policy import MissingExitPolicyError
from src.brokers.broker_factory import BrokerFactory
from src.trading.account_selector import AccountSelector
from src.trading.orchestrator import TradingOrchestrator
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy
from config.trading_mode import resolve_trading_mode

# ═══════════════════════════════════════════════════════════════════════════
# РЕЖИМ ТОРГОВЛИ (только через окружение, см. config/trading_mode.py)
# ═══════════════════════════════════════════════════════════════════════════

TRADING_MODE = resolve_trading_mode()  # default: dry_run, real orders need LIVE_CONFIRM=yes

# ═══════════════════════════════════════════════════════════════════════════
# 🧪 ТЕСТОВЫЙ РЕЖИМ (генерация фиктивных сигналов)
# ═══════════════════════════════════════════════════════════════════════════
TEST_MODE = (os.getenv("TEST_MODE") or "").strip().lower() in {"1", "true", "yes"}
TEST_SIGNAL_TYPE = "BULLISH"  # "BULLISH" или "BEARISH"
TEST_INSTRUMENT = "T"  # Тикер для тестового сигнала (или None = все)
TEST_TIMEFRAME = "4h"  # Таймфрейм для тестового сигнала (или None = все)
TEST_SIGNALS_BY_TIMEFRAME = {
    "1h": [
        # {"ticker": "T", "signal_type": "BULLISH"},
        # {"ticker": "PIKK", "signal_type": "BEARISH"},
        # {"ticker": "VTBR", "signal_type": "BULLISH"},
        # {"ticker": "X5", "signal_type": "BEARISH"},
    ],
    "4h": [
        {"ticker": "PIKK", "signal_type": "BULLISH"},
        {"ticker": "GAZP", "signal_type": "BEARISH"},
    ],
    "1d": [
        # {"ticker": "LKOH", "signal_type": "BULLISH"},
    ],
}

# Если True — используем TEST_SIGNALS_BY_TIMEFRAME, а не одиночный TEST_INSTRUMENT/TEST_SIGNAL_TYPE
USE_MULTI_TEST_SIGNALS = True
# ═══════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/scheduler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("TradingScheduler")

# Создаём папку для логов
Path("logs").mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════════════════

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# Рабочее время планировщика
SCHEDULER_START_HOUR = 5  # 05:00
SCHEDULER_END_HOUR = 0     # 00:30 (следующий день)

# Таймфреймы для торговли
TRADING_TIMEFRAMES = ["1h", "4h", "1d"]

# Конфигурация
CONFIG_PATH = "config/instruments.json"
DB_PATH = "marketdata.db"

# ═══════════════════════════════════════════════════════════════════════════
# СИСТЕМА ПРИОРИТЕТОВ ЗАДАЧ
# ═══════════════════════════════════════════════════════════════════════════

class TaskPriority(IntEnum):
    """Приоритеты задач."""
    CRITICAL = 0      # Обновление данных + открытие сделок (ВСЕГДА выполняется)
    IMPORTANT = 1     # Сбор стоп-ордеров (откладывается если идёт P0)
    BACKGROUND = 2    # Мониторинг позиций (откладывается если идёт P0 или P1)

# Блокировки для разных приоритетов
critical_lock = threading.Lock()    # Только для критичных задач
important_lock = threading.Lock()   # Для важных задач

def can_run_task(priority: TaskPriority) -> bool:
    """
    Проверить, можно ли запустить задачу с данным приоритетом.
    
    Args:
        priority: Приоритет задачи
    
    Returns:
        bool: True если задачу можно запустить
    """
    if priority == TaskPriority.CRITICAL:
        # Критичные задачи ВСЕГДА выполняются
        return True
    
    elif priority == TaskPriority.IMPORTANT:
        # Важные задачи запускаются если НЕ идёт критичная
        if critical_lock.locked():
            logger.info("⏸️  Critical task running, postponing important task")
            return False
        return True
    
    elif priority == TaskPriority.BACKGROUND:
        # ✅ ФИКС: блокируем только P0, P1 не мешает P2
        if critical_lock.locked():
            logger.info("⏸️ Critical task running, skipping background task")
            return False
        return True
    
    return False

# ═══════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════

def get_reload_params() -> Dict[str, any]:
    """
    Определить параметры для reload_data в зависимости от дня недели.
    
    Returns:
        dict: {"hours_back": int, "reason": str}
    """
    now = datetime.now(MOSCOW_TZ)
    
    # Понедельник → догружаем выходные (72 часа = 3 дня)
    if now.weekday() == 0:  # Monday
        return {
            "hours_back": 72,
            "reason": "Monday - loading weekend data (3 days)"
        }
    
    # Остальные дни → последние 2 часа
    else:
        return {
            "hours_back": 2,
            "reason": "Regular update (2 hours)"
        }


def should_run_timeframe(timeframe: str, now: datetime) -> bool:
    """
    Определяет, нужно ли запускать стратегию для таймфрейма.
    Запускается только когда закрылась новая свеча.
    
    Args:
        timeframe: "1h", "4h", "1d"
        now: Текущее время (с учётом часового пояса)
    
    Returns:
        bool: True если нужно запустить стратегию
    """
    hour = now.hour
    minute = now.minute
    
    # 1h → запускается каждый час на границе (XX:00:15)
    if timeframe == "1h":
        return minute == 0  # 10:00:15, 11:00:15, 12:00:15, ...
    
    # 4h → запускается каждые 4 часа на границе (XX:00:15)
    elif timeframe == "4h":
        # hour % 4 == 3 → 3, 7, 11, 15, 19, 23
        return hour % 4 == 3 and minute == 0
    
    # 1d → запускается после закрытия дня (23:59)
    elif timeframe == "1d":
        # Ближайшее обновление после 23:59 → это 00:00:15
        return hour == 0 and minute == 0
    
    return False


def is_weekend() -> bool:
    """Проверка, является ли сегодня выходным."""
    now = datetime.now(MOSCOW_TZ)
    return now.weekday() >= 5  # Суббота=5, Воскресенье=6


# ═══════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ КЛАСС ПЛАНИРОВЩИКА
# ═══════════════════════════════════════════════════════════════════════════

class TradingScheduler:
    """
    Планировщик торговых задач (ПРОДАКШН).
    
    Управляет:
    - Обновлением рыночных данных
    - Запуском торговых стратегий
    - Сбором стоп-ордеров
    - Мониторингом открытых позиций
    """
    
    def __init__(self):
        """Инициализация планировщика."""
        logger.info("="*80)
        logger.info("🚀 Initializing Trading Scheduler")
        logger.info("="*80)

        if TRADING_MODE.is_live:
            logger.warning("🔴 LIVE TRADING ENABLED - REAL MONEY AT RISK")
        else:
            logger.warning("⚠️ DRY RUN MODE - signals are logged, NO REAL TRADES")

        logger.info("="*80)
        
        # Загружаем переменные окружения
        load_dotenv()
        
        self.token = os.getenv("TINKOFF_API_TOKEN")
        if not self.token:
            raise ValueError("❌ TINKOFF_API_TOKEN not found in .env")
        
        # Инициализируем компоненты
        self.broker = BrokerFactory.create("sdk", self.token)
        self.history_manager = HistoryManager(self.broker)
        self.data_updater = DataUpdater(
            token=self.token,
            config_path=CONFIG_PATH
        )
        self.account_selector = AccountSelector()

        # PostgreSQL-репозитории сделок (создаются лениво, один раз за процесс)
        self._trade_repo = None
        self._order_repo = None
        
        # Планировщик APScheduler
        self.scheduler = BlockingScheduler(timezone=MOSCOW_TZ)
        
        # Статистика
        self.stats = {
            "updates_completed": 0,
            "strategies_run": 0,
            "trades_executed": 0,
            "orders_collected": 0,
            "positions_closed": 0,
            "errors": 0,
            "last_update": None,
            "last_strategy_run": None
        }
        
        logger.info("✓ Trading Scheduler initialized")
        logger.info(f"  Config: {CONFIG_PATH}")
        logger.info(f"  Timezone: {MOSCOW_TZ}")
        logger.info(f"  Trading mode: {TRADING_MODE.value}")
        logger.info(f"  Test mode: {TEST_MODE}")
        logger.info("="*80)
    
    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 P0: ОБНОВЛЕНИЕ РЫНОЧНЫХ ДАННЫХ (КРИТИЧНО)
    # ═══════════════════════════════════════════════════════════════════════
    
    def update_market_data(self):
        """
        🔴 P0: Инкрементальное обновление БД (каждую минуту).
        ТОЛЬКО загрузка данных — стратегии запускаются отдельной джобой.
        """
        if not can_run_task(TaskPriority.CRITICAL):
            return
        
        with critical_lock:
            try:
                now = datetime.now(MOSCOW_TZ)
                
                # Пропускаем выходные
                if is_weekend():
                    return
                
                # Определяем параметры обновления
                params = get_reload_params()
                hours_back = params["hours_back"]
                
                # Выполняем обновление с retry
                max_retries = 3
                for attempt in range(1, max_retries + 1):
                    try:
                        self.data_updater.incremental_update(hours_back=hours_back)
                        self.stats["updates_completed"] += 1
                        self.stats["last_update"] = now
                        logger.info(f"✅ DB updated ({params['reason']})")
                        break
                        
                    except Exception as e:
                        logger.error(f"❌ Attempt {attempt}/{max_retries}: {e}")
                        if attempt < max_retries:
                            import time
                            time.sleep(10)
                        else:
                            self.stats["errors"] += 1
                            raise
            
            except Exception as e:
                logger.error(f"❌ DB update failed: {e}", exc_info=True)
                self.stats["errors"] += 1
    
    def run_trading_strategies(self):
        """
        🔴 P0: Запуск торговых стратегий для всех инструментов и таймфреймов.
        Вызывается отдельной джобой каждые 30 минут.
        """
        try:
            now = datetime.now(MOSCOW_TZ)
            logger.info("\n" + "─"*60)
            logger.info(f"🎯 TRADING STRATEGIES EXECUTION")
            logger.info(f"   Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info("─"*60)
            
            # Счётчики
            total_signals = 0
            total_trades = 0
            instruments_processed = 0
            
            # Получаем список активных инструментов
            instruments = self.data_updater.get_enabled_instruments()
            logger.info(f"📋 Instruments to process: {len(instruments)}")
            
            # Обрабатываем каждый таймфрейм
            for timeframe in TRADING_TIMEFRAMES:
                # Проверяем, нужно ли запускать этот таймфрейм
                if not TEST_MODE and not should_run_timeframe(timeframe, now):
                    logger.info(f"⏭️ Skipping {timeframe} (no new candle)")
                    continue

                if TEST_MODE:
                    logger.warning(f"🧪 TEST MODE: Processing {timeframe} (ignoring candle timing)")

                
                logger.info(f"\n{'·'*40}")
                logger.info(f"📈 Timeframe: {timeframe}")
                logger.info(f"{'·'*40}")
                
                # Выбираем счёт для таймфрейма
                # try:
                #     account_id = self.account_selector.get_account_id(
                #         timeframe=timeframe,
                #         strategy="least_loaded"
                #     )
                #     logger.info(f"💼 Account: ***{account_id[-4:]}")
                # except Exception as e:
                #     logger.error(f"❌ Failed to select account for {timeframe}: {e}")
                #     continue
                
                # Обрабатываем каждый инструмент
                for instrument in instruments:
                    # Проверяем, есть ли этот таймфрейм у инструмента
                    if timeframe not in instrument.timeframes:
                        continue
                    
                    try:
                        instruments_processed += 1
                        logger.info(f"\n🔍 {instrument.ticker} ({instrument.figi})")
                        
                        # Обрабатываем инструмент
                        result = self._process_instrument(
                            instrument=instrument,
                            timeframe=timeframe,
                            account_id=None
                        )
                        
                        total_signals += result["signals_count"]
                        total_trades += result["trades_count"]
                        
                    except Exception as e:
                        logger.error(f"❌ Error processing {instrument.ticker}: {e}")
                        continue
            
            # Итоговая статистика
            logger.info("\n" + "─"*60)
            logger.info(f"📊 STRATEGIES COMPLETED")
            logger.info(f"   Instruments: {instruments_processed}")
            logger.info(f"   Signals: {total_signals}")
            logger.info(f"   Trades: {total_trades}")
            logger.info("─"*60)
            
            self.stats["strategies_run"] += 1
            self.stats["trades_executed"] += total_trades
            self.stats["last_strategy_run"] = now
        
        except Exception as e:
            logger.error(f"❌ Strategy execution failed: {e}")
            import traceback
            traceback.print_exc()
            self.stats["errors"] += 1
    
    def _trade_repositories(self):
        """
        Репозитории сделок и стоп-ордеров в PostgreSQL.

        SQLite больше не используется в production-пути: данные сделок живут
        в той же БД, что и рыночные данные.
        """
        from src.trading.pg_repository import PgLiveTradeRepository, PgStopOrderRepository

        if self._trade_repo is None:
            engine = self.history_manager.engine
            self._trade_repo = PgLiveTradeRepository(engine)
            self._order_repo = PgStopOrderRepository(engine)
        return self._trade_repo, self._order_repo

    def _process_instrument(
        self,
        instrument,
        timeframe: str,
        account_id: Optional[str]
    ) -> Dict[str, int]:
        """
        Обработка одного инструмента: поиск сигналов + открытие сделок.
        
        Args:
            instrument: InstrumentConfig
            timeframe: Таймфрейм ("1h", "4h", "1d")
            account_id: ID счёта для торговли
        
        Returns:
            dict: {"signals_count": int, "trades_count": int}
        """
        # Маппинг таймфреймов
        timeframe_map = {
            "30m": Timeframe.M30,
            "1h": Timeframe.H1,
            "4h": Timeframe.H4,
            "1d": Timeframe.D1,
        }
        
        tf_enum = timeframe_map.get(timeframe)
        if not tf_enum:
            logger.warning(f"⚠️  Unknown timeframe: {timeframe}")
            return {"signals_count": 0, "trades_count": 0}
        
        # Загружаем свечи из БД
        candles = self.history_manager.get_candles(instrument.figi, tf_enum)
        
        # Фильтруем пустые свечи
        candles = [c for c in candles if c.volume and c.volume > 0]
        
        if not candles or len(candles) < 51:  # Минимум для SMA50 + 1
            logger.warning(f"⚠️  Insufficient data: {len(candles)} candles")
            return {"signals_count": 0, "trades_count": 0}
        
        logger.info(f"   📊 Candles: {len(candles)}")
        
        # Создаём стратегию. Ticker обязателен: TP/SL берутся из
        # config/tp_sl.json по (strategy, timeframe, ticker), дефолта нет (T3-4).
        try:
            strategy = DualMACrossoverStrategy(
                timeframe=timeframe,
                config={"ticker": instrument.ticker},
                debug=False
            )
        except MissingExitPolicyError as exc:
            logger.error(f"⛔ TP/SL not configured for {instrument.ticker} {timeframe}: {exc}")
            return {"signals_count": 0, "trades_count": 0}
        
        signal = strategy.check_last_signal(candles)
        
        # ═══════════════════════════════════════════════════════════════
        # 🧪 ТЕСТОВЫЙ РЕЖИМ: Генерация фиктивного сигнала
        # ═══════════════════════════════════════════════════════════════
        if TEST_MODE:
            from src.strategies.base_strategy import Signal

            if USE_MULTI_TEST_SIGNALS:
                tf_list = TEST_SIGNALS_BY_TIMEFRAME.get(timeframe, [])
                wanted = next((x for x in tf_list if x.get("ticker") == instrument.ticker), None)
                if not wanted:
                    logger.info(f" ⏭️ Skipped (MULTI_TEST no signal configured for {instrument.ticker} on {timeframe})")
                    return {"signals_count": 0, "trades_count": 0}

                signal_type = wanted.get("signal_type", "BULLISH")
                signal = Signal(
                    timestamp=datetime.now(),
                    candle_index=len(candles) - 1,
                    signal_type=signal_type,
                    price=candles[-1].close,
                    metadata={"test_mode": True, "source": "multi_test", "timeframe": timeframe},
                )
                logger.warning(f" 🧪 MULTI TEST MODE: Generated {signal_type} for {instrument.ticker} ({timeframe})")

            else:
                # Старый одиночный режим (как было)
                if TEST_INSTRUMENT and instrument.ticker != TEST_INSTRUMENT:
                    logger.info(f" ⏭️ Skipped (TEST_MODE filter: {TEST_INSTRUMENT})")
                    return {"signals_count": 0, "trades_count": 0}

                if TEST_TIMEFRAME and timeframe != TEST_TIMEFRAME:
                    logger.info(f" ⏭️ Skipped (TEST_MODE filter: {TEST_TIMEFRAME})")
                    return {"signals_count": 0, "trades_count": 0}

                signal = Signal(
                    timestamp=datetime.now(),
                    candle_index=len(candles) - 1,
                    signal_type=TEST_SIGNAL_TYPE,
                    price=candles[-1].close,
                    metadata={"sma_value": candles[-1].close * 0.99, "test_mode": True},
                )
                logger.warning(f" 🧪 TEST MODE: Generated {TEST_SIGNAL_TYPE} signal")


        # ═══════════════════════════════════════════════════════════════
        # 🎯 ПРОДАКШН: Реальный поиск сигнала
        # ═══════════════════════════════════════════════════════════════
        else:
            signal = strategy.check_last_signal(candles)
            if not signal:
                logger.info(f"  ✓ No signal")
                return {"signals_count": 0, "trades_count": 0}

        logger.info(f"  🔔 Signal: {signal.signal_type}")


        # direction для дубль-чека = сторона сделки
        direction = "LONG" if signal.signal_type == "BULLISH" else "SHORT"

        trade_repo, _order_repo = self._trade_repositories()
        all_accounts = self.account_selector.get_all_accounts()

        if trade_repo.has_open_trade_on_timeframe_accounts(
            ticker=instrument.ticker,
            timeframe=timeframe,
            direction=direction,
            account_ids=all_accounts,
        ):
            logger.warning(
                f"🚫 DUPLICATE PREVENTED: {instrument.ticker} {timeframe} {direction} already OPEN "
                f"on one of accounts"
            )
            return {"signals_count": 1, "trades_count": 0}


        if account_id is None:
            account_id = self.account_selector.get_account_id(direction)
            logger.info(f"💼 Account selected for trade: ***{account_id[-4:]} ({direction})")

        # ═══════════════════════════════════════════════════════════════
        # 🧪 DRY RUN: сигнал найден, но ордера НЕ отправляются
        # ═══════════════════════════════════════════════════════════════
        if not TRADING_MODE.is_live:
            logger.warning(
                f"🧪 DRY RUN: {signal.signal_type} {instrument.ticker} ({timeframe}) "
                f"not executed (TRADING_MODE={TRADING_MODE.value})"
            )
            return {"signals_count": 1, "trades_count": 0}

        # Создаём оркестратор для торговли
        orchestrator = TradingOrchestrator(
            token=self.token,
            account_id=account_id,
            strategy_name=strategy.name,
            timeframe=timeframe,
            repository=trade_repo,
        )
        
        # Выполняем торговлю
        strategy.signals = [signal]
        live_trades = strategy.execute_live_trades(
            orchestrator=orchestrator,
            ticker=instrument.ticker,
            figi=instrument.figi
        )
        
        logger.info(f"   ✅ Executed: {len(live_trades)} trade(s)")
        
        return {
            "signals_count": 1,
            "trades_count": len(live_trades)
        }
    
    # ═══════════════════════════════════════════════════════════════════════
    # 🟡 P1: СБОР СТОП-ОРДЕРОВ (ВАЖНО)
    # ═══════════════════════════════════════════════════════════════════════
    
    def collect_stop_orders(self):
        """
        🟡 ВАЖНО: Сбор стоп-ордеров для новых сделок.
        Запускается каждые 2 минуты.
        Откладывается если идёт критичная задача.
        """
        # Проверяем приоритет
        if not can_run_task(TaskPriority.IMPORTANT):
            return
        
        # Захватываем блокировку важных задач
        with important_lock:
            try:
                logger.info("\n" + "─"*60)
                logger.info("🟡 P1-IMPORTANT: COLLECTING STOP ORDERS")
                logger.info("─"*60)
                
                from src.trading.order_collector import OrderCollector

                trade_repo, order_repo = self._trade_repositories()
                collector = OrderCollector(
                    token=self.token,
                    trade_repo=trade_repo,
                    order_repo=order_repo,
                )
                stats = collector.collect_pending_orders(wait_seconds=0)
                
                if stats["collected"] > 0:
                    self.stats["orders_collected"] += stats["collected"]
                
                logger.info(
                    f"✓ Collected: {stats['collected']} orders"
                )
                logger.info("─"*60 + "\n")
                
            except Exception as e:
                logger.error(f"❌ Order collection failed: {e}")
                import traceback
                traceback.print_exc()
                self.stats["errors"] += 1
    
    # ═══════════════════════════════════════════════════════════════════════
    # 🟢 P2: МОНИТОРИНГ ПОЗИЦИЙ (ФОНОВОЕ)
    # ═══════════════════════════════════════════════════════════════════════
    
    def monitor_open_positions(self):
        """
        🟢 P2: Сверка статусов стоп-ордеров + закрытий позиций (API -> БД).
        """
        if not can_run_task(TaskPriority.BACKGROUND):
            return

        # Зависшие PENDING не видны сверке (у них нет стоп-ордеров)
        self._recover_pending_trades()

        try:
            from src.trading.reconciler import TradingReconciler

            trade_repo, order_repo = self._trade_repositories()
            reconciler = TradingReconciler(
                token=self.token,
                timezone=MOSCOW_TZ,
                log=logger,
                trade_repo=trade_repo,
                order_repo=order_repo,
            )

            stats = reconciler.reconcile(skip_weekends=True, is_weekend_fn=is_weekend)

            if stats.positions_closed_total > 0:
                self.stats["positions_closed"] += stats.positions_closed_total
            if stats.errors > 0:
                self.stats["errors"] += stats.errors

        except Exception as e:
            logger.error(f"❌ Position monitoring failed: {e}", exc_info=True)
            self.stats["errors"] += 1




    
    # ═══════════════════════════════════════════════════════════════════════
    # УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ
    # ═══════════════════════════════════════════════════════════════════════
    
    def setup_jobs(self):
        """Настройка задач планировщика с приоритетами."""
        logger.info("⚙️  Setting up scheduler jobs...")
        
# ═══════════════════════════════════════════════════════════════════
        # 🔴 P0: Обновление данных — каждую минуту, секунда 5
        # ═══════════════════════════════════════════════════════════════════
        
        self.scheduler.add_job(
            func=self.update_market_data,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*",
                second="5",
                timezone=MOSCOW_TZ
            ),
            id="update_market_data",
            name="[P0-CRITICAL] DB Update (every 1 min)",
            max_instances=1,
            coalesce=True
        )
        
        logger.info("   ✓ [P0-CRITICAL] DB Update (every 1 min, sec 5)")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 P0: Запуск стратегий — каждые 30 минут
        # ═══════════════════════════════════════════════════════════════════
        
        self.scheduler.add_job(
            func=self.run_trading_strategies,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/30",
                second="15",
                timezone=MOSCOW_TZ
            ),
            id="run_strategies",
            name="[P0-CRITICAL] Trading Strategies (every 30 min)",
            max_instances=1,
            coalesce=True
        )
        
        logger.info("   ✓ [P0-CRITICAL] Trading Strategies (every 30 min)")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🟡 P1: Сбор стоп-ордеров каждые 2 минуты
        # ═══════════════════════════════════════════════════════════════════
        
        self.scheduler.add_job(
            func=self.collect_stop_orders,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/2",  # Каждые 2 минуты
                timezone=MOSCOW_TZ
            ),
            id="collect_stop_orders",
            name="[P1-IMPORTANT] Collect Stop Orders",
            max_instances=1,
            coalesce=True
        )
        
        logger.info("   ✓ [P1-IMPORTANT] Collect Stop Orders (every 2 min)")
        
        # ═══════════════════════════════════════════════════════════════════
        # 🟢 P2: Мониторинг позиций каждые 15 минут
        # ═══════════════════════════════════════════════════════════════════
        
        self.scheduler.add_job(
            func=self.monitor_open_positions,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/3",  # Каждые 15 минут
                timezone=MOSCOW_TZ
            ),
            id="monitor_positions",
            name="[P2-BACKGROUND] Monitor Positions",
            max_instances=1,
            coalesce=True
        )
        
        logger.info("   ✓ [P2-BACKGROUND] Monitor Positions (every 15 min)")
        
        logger.info("✅ All jobs configured with priorities")
    
    def print_schedule(self):
        """Вывести расписание задач."""
        logger.info("\n" + "="*80)
        logger.info("📅 SCHEDULER CONFIGURATION")
        logger.info("="*80)
        logger.info(f"Timezone: {MOSCOW_TZ}")
        logger.info(f"Active hours: {SCHEDULER_START_HOUR:02d}:00 - 00:30 (next day)")
        logger.info(f"Timeframes: {', '.join(TRADING_TIMEFRAMES)}")
        
        logger.info(f"Trading mode: {TRADING_MODE.value}")
        if TRADING_MODE.is_live:
            logger.info("🔴 LIVE TRADING ENABLED")
        else:
            logger.info("⚠️  DRY RUN: signals only, no orders")
        
        logger.info("\n📋 Scheduled Jobs:")
        for job in self.scheduler.get_jobs():
            logger.info(f"   • {job.name}")
            logger.info(f"     ID: {job.id}")
            logger.info(f"     Trigger: {job.trigger}")
        
        logger.info("="*80 + "\n")
    
    def print_stats(self):
        """Вывести статистику работы."""
        logger.info("\n" + "="*80)
        logger.info("📊 SCHEDULER STATISTICS")
        logger.info("="*80)
        logger.info(f"Updates completed: {self.stats['updates_completed']}")
        logger.info(f"Strategies run: {self.stats['strategies_run']}")
        logger.info(f"Trades executed: {self.stats['trades_executed']}")
        logger.info(f"Orders collected: {self.stats['orders_collected']}")
        logger.info(f"Positions closed: {self.stats['positions_closed']}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        if self.stats['last_update']:
            logger.info(f"Last update: {self.stats['last_update'].strftime('%Y-%m-%d %H:%M:%S')}")
        if self.stats['last_strategy_run']:
            logger.info(f"Last strategy run: {self.stats['last_strategy_run'].strftime('%Y-%m-%d %H:%M:%S')}")
        
        logger.info("="*80 + "\n")


    def _recover_pending_trades(self):
        """
        Дозакрыть зависшие PENDING-записи (хвост T2-6).

        Процесс мог упасть между вставкой PENDING и записью OPEN: тогда на бирже
        позиция есть (или была), а в БД сделка висит без position_id и держит
        signal_key. `PendingRecovery` сверяет состояние с биржей и либо переводит
        запись в OPEN, либо отменяет её и освобождает ключ.
        """
        from src.trading.pending_recovery import PendingRecovery

        try:
            trade_repo, _ = self._trade_repositories()
            recovery = PendingRecovery(token=self.token, trade_repo=trade_repo, log=logger)
            stats = recovery.recover()
            if stats.errors:
                self.stats["errors"] += stats.errors
            return stats
        except Exception as e:
            logger.error(f"❌ Pending recovery failed: {e}", exc_info=True)
            self.stats["errors"] += 1
            return None

    def _startup_reconcile(self):
        """
        Однократная сверка при старте — закрывает позиции,
        которые исполнились пока программа не работала.
        """
        logger.info("🔄 STARTUP: Running initial reconciliation...")
        # Сначала разбираем зависшие PENDING: у них нет stop_orders, сверка их не видит
        self._recover_pending_trades()
        try:
            from src.trading.reconciler import TradingReconciler
            trade_repo, order_repo = self._trade_repositories()
            reconciler = TradingReconciler(
                token=self.token,
                timezone=MOSCOW_TZ,
                log=logger,
                trade_repo=trade_repo,
                order_repo=order_repo,
            )
            stats = reconciler.reconcile(skip_weekends=False)  # ← skip_weekends=False: всегда выполнять при старте
            logger.info(
                f"✅ Startup reconcile done: "
                f"executed={stats.executed_positions_closed}, "
                f"manual={stats.manual_positions_closed}, "
                f"errors={stats.errors}"
            )
        except Exception as e:
            logger.error(f"❌ Startup reconcile failed: {e}", exc_info=True)    
    
    def start(self):
        """Запустить планировщик."""
        logger.info("🚀 Starting Trading Scheduler...")
        
        # Настраиваем задачи
        self.setup_jobs()
        
        # Выводим расписание
        self.print_schedule()
        
        # Обработка Ctrl+C
        def signal_handler(signum, frame):
            logger.info("\n⚠️  Shutdown signal received")
            self.print_stats()
            logger.info("👋 Shutting down gracefully...")
            self.scheduler.shutdown(wait=False)
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info("✅ Scheduler started successfully")
        logger.info("Press Ctrl+C to stop\n")

        


        
        # Запускаем планировщик (блокирующий режим)
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("\n👋 Scheduler stopped")
            self.print_stats()


# ═══════════════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Главная функция."""
    try:
        # Создаём и запускаем планировщик
        scheduler = TradingScheduler()
        scheduler.start()
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
