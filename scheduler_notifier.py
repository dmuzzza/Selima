"""
Trading Notifier - SIGNAL-ONLY (no trades)

Дубликат scheduler.py, который:
- 🔴 P0: Обновление рыночных данных (каждую минуту) — БЕЗ ИЗМЕНЕНИЙ
- 🔴 P0: Сканирование сигналов (каждые 30 мин) → уведомление в VK
- 🟡 P1: Сбор стоп-ордеров (каждые 2 мин) — БЕЗ ИЗМЕНЕНИЙ
- 🟢 P2: Мониторинг позиций (каждые 3 мин) — БЕЗ ИЗМЕНЕНИЙ

КЛЮЧЕВОЕ ОТЛИЧИЕ: сделки НЕ открываются — вместо этого шлётся уведомление
в VK-чат с помощью VK_TOKEN.

Работает: 05:00 - 00:30 MSK
"""

import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['SSL_TBANK_VERIFY'] = 'True'
import sys
import json
import logging
import threading
import signal
import time as _time
import pytz
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from types import SimpleNamespace
from pathlib import Path
from enum import IntEnum
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# Импорты из проекта
from src.market_data.data_updater import DataUpdater
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe
from src.brokers.broker_factory import BrokerFactory
from src.trading.account_selector import AccountSelector
from src.trading.orchestrator import TradingOrchestrator
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy
from config.trading_mode import resolve_trading_mode

# ═══════════════════════════════════════════════════════════════════════════
# РЕЖИМ ТОРГОВЛИ (только через окружение, см. config/trading_mode.py)
# ═══════════════════════════════════════════════════════════════════════════
# dry_run — только сигналы и уведомления; live — реальные сделки (нужен LIVE_CONFIRM=yes).
# Сделки пишутся в PostgreSQL (live_trades) через PgLiveTradeRepository.
TRADING_MODE = resolve_trading_mode()

# ═══════════════════════════════════════════════════════════════════════════
# 🧪 ТЕСТОВЫЙ РЕЖИМ (генерация фиктивных сигналов)
# ═══════════════════════════════════════════════════════════════════════════
TEST_MODE = (os.getenv("TEST_MODE") or "").strip().lower() in {"1", "true", "yes"}
TEST_SIGNAL_TYPE = "BULLISH"
TEST_INSTRUMENT = "T"
TEST_TIMEFRAME = "4h"
TEST_SIGNALS_BY_TIMEFRAME = {
    "1h": [],
    "4h": [
        {"ticker": "PIKK", "signal_type": "BULLISH"},
        {"ticker": "GAZP", "signal_type": "BEARISH"},
        {"ticker": "T", "signal_type": "BEARISH"},
    ],
    "1d": [],
}
USE_MULTI_TEST_SIGNALS = True

# ═══════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/notifier.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("TradingNotifier")
Path("logs").mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════════════════

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

SCHEDULER_START_HOUR = 5
SCHEDULER_END_HOUR = 0

TRADING_TIMEFRAMES = ["4h"] #"1h", "4h", "1d"

CONFIG_PATH = "config/instruments.json"
DB_PATH = "marketdata.db"


# VK API
VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"

# ═══════════════════════════════════════════════════════════════════════════
# СИСТЕМА ПРИОРИТЕТОВ ЗАДАЧ
# ═══════════════════════════════════════════════════════════════════════════

class TaskPriority(IntEnum):
    CRITICAL = 0
    IMPORTANT = 1
    BACKGROUND = 2

critical_lock = threading.Lock()
important_lock = threading.Lock()


def can_run_task(priority: TaskPriority) -> bool:
    if priority == TaskPriority.CRITICAL:
        return True
    elif priority == TaskPriority.IMPORTANT:
        if critical_lock.locked():
            logger.info("⏸️  Critical task running, postponing important task")
            return False
        return True
    elif priority == TaskPriority.BACKGROUND:
        if critical_lock.locked():
            logger.info("⏸️ Critical task running, skipping background task")
            return False
        return True
    return False

# ═══════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════

def get_reload_params() -> Dict[str, any]:
    now = datetime.now(MOSCOW_TZ)
    if now.weekday() == 0:
        return {"hours_back": 72, "reason": "Monday - loading weekend data (3 days)"}
    else:
        return {"hours_back": 2, "reason": "Regular update (2 hours)"}


def should_run_timeframe(timeframe: str, now: datetime) -> bool:
    hour = now.hour
    minute = now.minute
    if timeframe == "1h":
        return minute == 0
    elif timeframe == "4h":
        return hour % 4 == 3 and minute == 0
    elif timeframe == "1d":
        return hour == 0 and minute == 0
    return False


def is_weekend() -> bool:
    now = datetime.now(MOSCOW_TZ)
    return now.weekday() >= 5

# ═══════════════════════════════════════════════════════════════════════════
# 🐘 POSTGRES-РЕПОЗИТОРИИ (сделки → PostgreSQL, не SQLite)
# ═══════════════════════════════════════════════════════════════════════════
# Единственное хранилище сделок и стоп-ордеров — PostgreSQL
# (таблицы live_trades и stop_orders).

from src.trading.pg_repository import PgLiveTradeRepository, PgStopOrderRepository



# ═══════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ КЛАСС НОТИФИКАТОРА
# ═══════════════════════════════════════════════════════════════════════════

class TradingNotifier:
    """
    Сканер сигналов с VK-уведомлениями.

    Делает всё то же, что и TradingScheduler:
    - Обновление рыночных данных
    - Запуск торговых стратегий (поиск сигналов)
    - Сбор стоп-ордеров
    - Мониторинг открытых позиций

    НО ВМЕСТО ОТКРЫТИЯ СДЕЛОК → отправляет уведомление в VK.
    """

    def __init__(self):
        logger.info("=" * 80)
        logger.info("🔔 Initializing Trading Notifier — SIGNAL ONLY, NO TRADES")
        logger.info("=" * 80)

        load_dotenv()

        # Tinkoff token
        self.token = os.getenv("TINKOFF_API_TOKEN")
        if not self.token:
            raise ValueError("❌ TINKOFF_API_TOKEN not found in .env")

# VK
        self.vk_token = os.getenv("VK_TOKEN")
        self.vk_peer_id = os.getenv("VK_CHAT_ID")

        if not self.vk_token:
            raise ValueError("❌ VK_TOKEN not found in .env")
        if not self.vk_peer_id:
            raise ValueError("❌ VK_CHAT_ID (peer_id for VK) not found in .env")

        logger.info(f"📱 VK bot token: {self.vk_token[:8]}...")
        logger.info(f"📱 VK peer ID: {self.vk_peer_id}")

        # Компоненты (те же, что в scheduler.py)
        self.broker = BrokerFactory.create("sdk", self.token)
        self.history_manager = HistoryManager(self.broker)  # единственный источник правды
        self.data_updater = DataUpdater(
            token=self.token,
            config_path=CONFIG_PATH,
            history_manager=self.history_manager,  # 🔑 передаём тот же инстанс
            broker=self.broker,                    # 🔑 и того же брокера
        )
        self.account_selector = AccountSelector()

        self.scheduler = BlockingScheduler(timezone=MOSCOW_TZ)

        self.stats = {
            "updates_completed": 0,
            "strategies_run": 0,
            "signals_found": 0,
            "notifications_sent": 0,
            "trades_executed": 0,
            "orders_collected": 0,
            "positions_closed": 0,
            "errors": 0,
            "last_update": None,
            "last_strategy_run": None
        }

        # Дедупликация сигналов: ключ "ticker:timeframe" → candle_index последнего отправленного сигнала
        self._last_signal_index: Dict[str, int] = {}

        # Кэш тикеров из strategy_backtest_summary (PostgreSQL)
        self._positive_tickers_cache: Optional[set] = None
        self._positive_tickers_ts: float = 0.0
        self._positive_tickers_ttl: float = 1800.0  # 30 минут

        logger.info("✓ Trading Notifier initialized")
        logger.info(f"  Token: {self.token[:10]}...")
        logger.info(f"  Config: {CONFIG_PATH}")
        logger.info(f"  Timezone: {MOSCOW_TZ}")
        logger.info("=" * 80)

    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 P0: ОБНОВЛЕНИЕ ДАННЫХ + СКАНИРОВАНИЕ СИГНАЛОВ (АТОМАРНО)
    # ═══════════════════════════════════════════════════════════════════════

    def update_and_scan(self):
        """
        🔴 P0-CRITICAL: обновление рыночных данных → немедленное сканирование сигналов.
        Гарантирует, что сканирование всегда происходит на свежих данных.
        """
        if not can_run_task(TaskPriority.CRITICAL):
            return

        with critical_lock:
            self.update_market_data()
            self.run_trading_strategies()

    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 P0: ОБНОВЛЕНИЕ РЫНОЧНЫХ ДАННЫХ (КРИТИЧНО) — БЕЗ ИЗМЕНЕНИЙ
    # ═══════════════════════════════════════════════════════════════════════

    def update_market_data(self):
        # Блокировка и проверка приоритета теперь в update_and_scan
        try:
            now = datetime.now(MOSCOW_TZ)
            if is_weekend():
                return

            params = get_reload_params()
            hours_back = params["hours_back"]

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
                        _time.sleep(10)
                    else:
                        self.stats["errors"] += 1
                        raise

        except Exception as e:
            logger.error(f"❌ DB update failed: {e}", exc_info=True)
            self.stats["errors"] += 1

    # ═══════════════════════════════════════════════════════════════════════
    # 🔑 ФИЛЬТР: ТИКЕРЫ ИЗ STRATEGY_BACKTEST_SUMMARY (POSTGRES)
    # ═══════════════════════════════════════════════════════════════════════

    def _get_positive_backtest_tickers(self) -> set:
        """
        Получить множество тикеров из strategy_backtest_summary (PostgreSQL).

        Таблица наполняется ночным экспортом (scripts/export_dual_ma_to_postgres.py)
        и содержит ТОЛЬКО тикеры с положительным Total PnL.

        Кэшируется на _positive_tickers_ttl секунд (30 мин), чтобы не дёргать
        БД на каждый инструмент при каждом сканировании.

        Returns:
            set: Множество тикеров. При ошибке БД — пустое множество
                 (консервативно: сигналы пропускаются).
        """
        now = _time.monotonic()
        if (
            self._positive_tickers_cache is not None
            and (now - self._positive_tickers_ts) < self._positive_tickers_ttl
        ):
            return self._positive_tickers_cache

        try:
            from sqlalchemy import text

            with self.history_manager.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT DISTINCT ticker FROM strategy_backtest_summary"
                )).fetchall()

            tickers = {r[0] for r in rows}
            self._positive_tickers_cache = tickers
            self._positive_tickers_ts = now
            logger.info(f"📋 Backtest winners loaded from PG: {len(tickers)} tickers")
            return tickers

        except Exception as e:
            logger.error(f"❌ Failed to load backtest tickers from PG: {e}")
            # Консервативное поведение: пустой список → все сигналы пропускаются
            return set()

    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 P0: СКАНИРОВАНИЕ СИГНАЛОВ (ВМЕСТО ОТКРЫТИЯ СДЕЛОК)
    # ═══════════════════════════════════════════════════════════════════════

    def run_trading_strategies(self):
        """
        🔴 P0: Сканирование сигналов для всех инструментов и таймфреймов.
        ВМЕСТО открытия сделок → отправляет уведомление в VK.
        """
        try:
            now = datetime.now(MOSCOW_TZ)
            logger.info("\n" + "─" * 60)
            logger.info(f"🔔 SIGNAL SCANNER EXECUTION")
            logger.info(f"   Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            logger.info("─" * 60)

            total_signals = 0
            total_notifications = 0
            total_trades = 0
            instruments_processed = 0

            instruments = self.data_updater.get_enabled_instruments()
            logger.info(f"📋 Instruments to scan: {len(instruments)}")

            for timeframe in TRADING_TIMEFRAMES:
                if TEST_MODE:
                    logger.warning(f"🧪 TEST MODE: Processing {timeframe}")

                logger.info(f"\n{'·'*40}")
                logger.info(f"📈 Timeframe: {timeframe}")
                logger.info(f"{'·'*40}")

                for instrument in instruments:
                    if timeframe not in instrument.timeframes:
                        continue

                    try:
                        instruments_processed += 1
                        logger.info(f"\n🔍 {instrument.ticker} ({instrument.figi})")

                        result = self._process_instrument(
                            instrument=instrument,
                            timeframe=timeframe,
                        )

                        total_signals += result["signals_count"]
                        total_notifications += result["notifications_sent"]
                        total_trades += result.get("trades_count", 0)

                    except Exception as e:
                        logger.error(f"❌ Error processing {instrument.ticker}: {e}")
                        continue

            logger.info("\n" + "─" * 60)
            logger.info(f"📊 SCAN COMPLETED")
            logger.info(f"   Instruments: {instruments_processed}")
            logger.info(f"   Signals: {total_signals}")
            logger.info(f"   Notifications sent: {total_notifications}")
            logger.info(f"   Trades executed: {total_trades}")
            logger.info("─" * 60)

            self.stats["strategies_run"] += 1
            self.stats["signals_found"] += total_signals
            self.stats["notifications_sent"] += total_notifications
            self.stats["trades_executed"] += total_trades
            self.stats["last_strategy_run"] = now

        except Exception as e:
            logger.error(f"❌ Strategy scan failed: {e}")
            import traceback
            traceback.print_exc()
            self.stats["errors"] += 1

    # ═══════════════════════════════════════════════════════════════════════
    # 🔑 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: _process_instrument
    # ═══════════════════════════════════════════════════════════════════════

    def _process_instrument(
        self,
        instrument,
        timeframe: str,
    ) -> Dict[str, int]:
        """
        Обработка одного инструмента: поиск сигнала + отправка в VK.

        Args:
            instrument: InstrumentConfig
            timeframe: Таймфрейм ("1h", "4h", "1d")

        Returns:
            dict: {"signals_count": int, "notifications_sent": int}
        """
        timeframe_map = {
            # "30m": Timeframe.M30,
            # "1h": Timeframe.H1,
            "4h": Timeframe.H4,
            # "1d": Timeframe.D1,
        }

        tf_enum = timeframe_map.get(timeframe)
        if not tf_enum:
            logger.warning(f"⚠️  Unknown timeframe: {timeframe}")
            return {"signals_count": 0, "notifications_sent": 0}

        # Загружаем свечи из БД
        candles = self.history_manager.get_candles(instrument.figi, tf_enum)
        candles = [c for c in candles if c.is_complete] #candles = [c for c in candles if c.volume and c.volume > 0]

        if not candles or len(candles) < 201:
            logger.warning(f"⚠️  Insufficient data: {len(candles)} candles")
            return {"signals_count": 0, "notifications_sent": 0}

        logger.info(f"   📊 Candles: {len(candles)}")

        # Создаём стратегию
        strategy = DualMACrossoverStrategy(
            timeframe=timeframe,
            config=None,
            debug=False
        )

        # ═══════════════════════════════════════════════════════════════
        # 🧪 ТЕСТОВЫЙ РЕЖИМ
        # ═══════════════════════════════════════════════════════════════
        if TEST_MODE:
            from src.strategies.base_strategy import Signal

            if USE_MULTI_TEST_SIGNALS:
                tf_list = TEST_SIGNALS_BY_TIMEFRAME.get(timeframe, [])
                wanted = next((x for x in tf_list if x.get("ticker") == instrument.ticker), None)
                if not wanted:
                    logger.info(f" ⏭️ Skipped (no test signal configured for {instrument.ticker} on {timeframe})")
                    return {"signals_count": 0, "notifications_sent": 0}

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
                if TEST_INSTRUMENT and instrument.ticker != TEST_INSTRUMENT:
                    logger.info(f" ⏭️ Skipped (TEST_MODE filter: {TEST_INSTRUMENT})")
                    return {"signals_count": 0, "notifications_sent": 0}

                if TEST_TIMEFRAME and timeframe != TEST_TIMEFRAME:
                    logger.info(f" ⏭️ Skipped (TEST_MODE filter: {TEST_TIMEFRAME})")
                    return {"signals_count": 0, "notifications_sent": 0}

                signal = Signal(
                    timestamp=datetime.now(),
                    candle_index=len(candles) - 1,
                    signal_type=TEST_SIGNAL_TYPE,
                    price=candles[-1].close,
                    metadata={"test_mode": True},
                )
                logger.warning(f" 🧪 TEST MODE: Generated {TEST_SIGNAL_TYPE} signal")

        # ═══════════════════════════════════════════════════════════════
        # 🎯 ПРОДАКШН: Реальный поиск сигнала
        # ═══════════════════════════════════════════════════════════════
        else:
            signals = strategy.detect_signals(candles)
            if not signals:
                logger.info(f"   ✓ No signal")
                return {"signals_count": 0, "notifications_sent": 0}

            last_idx = len(candles) - 1
            signal = next((s for s in signals if s.candle_index == last_idx), None)

            if not signal:
                logger.info(f"   ✓ No new signal on latest candle")
                return {"signals_count": 0, "notifications_sent": 0}

            logger.info(f"   🔔 Signal: {signal.signal_type}")

        # ═══════════════════════════════════════════════════════════════
        # 🔑 ФИЛЬТР: тикер должен быть в strategy_backtest_summary (PG)
        # ═══════════════════════════════════════════════════════════════
        positive_tickers = self._get_positive_backtest_tickers()
        if instrument.ticker not in positive_tickers:
            logger.info(
                f"   ⏭️ Skipped ({instrument.ticker} not in backtest winners list)"
            )
            return {"signals_count": 0, "notifications_sent": 0}

        # Дедупликация: не отправляем повторно сигнал для той же свечи
        sig_key = f"{instrument.ticker}:{timeframe}"
        if sig_key in self._last_signal_index and self._last_signal_index[sig_key] == signal.candle_index:
            logger.info(f"  ⏭️ Skipped (duplicate signal at candle_index={signal.candle_index})")
            return {"signals_count": 0, "notifications_sent": 0}
        self._last_signal_index[sig_key] = signal.candle_index

        # ═══════════════════════════════════════════════════════════════
        # 🔑 ВЫБОР СЧЁТА ПО НАПРАВЛЕНИЮ СИГНАЛА
        # ═══════════════════════════════════════════════════════════════
        direction = "LONG" if signal.signal_type == "BULLISH" else "SHORT"
        account_id = self.account_selector.get_account_id(direction)
        logger.info(f"   💳 Account for {direction}: {account_id}")

        # ═══════════════════════════════════════════════════════════════
        # 🔑 ПРОВЕРКА ДУБЛЕЙ ПО ВСЕМ СЧЕТАМ (PostgreSQL, не SQLite)
        # ═══════════════════════════════════════════════════════════════
        pg_repo = PgLiveTradeRepository(self.history_manager.engine)
        all_accounts = self.account_selector.get_all_accounts()

        if pg_repo.has_open_trade_on_timeframe_accounts(
            ticker=instrument.ticker,
            timeframe=timeframe,
            direction=direction,
            account_ids=all_accounts,
        ):
            logger.warning(
                f"🚫 DUPLICATE PREVENTED: {instrument.ticker} {timeframe} {direction} "
                f"already OPEN on one of accounts"
            )
            return {"signals_count": 1, "notifications_sent": 0, "trades_count": 0}

        # ═══════════════════════════════════════════════════════════════
        # 🔑 ОТКРЫТИЕ СДЕЛКИ (только в режиме live)
        # ═══════════════════════════════════════════════════════════════
        trades_count = 0
        if TRADING_MODE.is_live:
            try:
                orchestrator = TradingOrchestrator(
                    token=self.token,
                    account_id=account_id,
                    strategy_name=strategy.name,
                    timeframe=timeframe,
                    repository=pg_repo,
                )

                live_trades = orchestrator.process_signals(
                    signals=[signal],
                    ticker=instrument.ticker,
                    figi=instrument.figi,
                    tp_percent=strategy.tp_percent,
                    sl_percent=strategy.sl_percent,
                )

                trades_count = len(live_trades)
                logger.info(f"   📈 Trades executed: {trades_count}")

            except Exception as e:
                logger.error(f"   ❌ Trade execution failed: {e}", exc_info=True)

        # ═══════════════════════════════════════════════════════════════
        # 📱 VK-УВЕДОМЛЕНИЕ
        # ═══════════════════════════════════════════════════════════════
        success = self._send_vk_notification(
            signal=signal,
            instrument=instrument,
            timeframe=timeframe,
            candles=candles,
            strategy_name=strategy.name,
            tp_percent=strategy.tp_percent,
            sl_percent=strategy.sl_percent,
        )
        if success:
            self._last_signal_index[sig_key] = signal.candle_index
        return {
            "signals_count": 1,
            "notifications_sent": 1 if success else 0,
            "trades_count": trades_count,
        }

# ═══════════════════════════════════════════════════════════════════════
    # 📱 VK-УВЕДОМЛЕНИЕ (сообщения от сообщества себе)
    # ═══════════════════════════════════════════════════════════════════════

    def _send_vk_notification(
        self,
        signal,
        instrument,
        timeframe: str,
        candles: list,
        strategy_name: str,
        tp_percent: float,
        sl_percent: float,
    ) -> bool:
        """
        Отправляет уведомление о сигнале в VK через messages.send от сообщества.

        Returns:
            bool: True если сообщение отправлено успешно.
        """
        import urllib.request
        import urllib.error
        import random

        now = datetime.now(MOSCOW_TZ)

        # Определяем эмодзи и направление
        if signal.signal_type == "BULLISH":
            emoji = "🟢📈"
            direction_text = "LONG"
        else:
            emoji = "🔴📉"
            direction_text = "SHORT"

        # Расчёт TP/SL цен
        entry_price = signal.price
        if signal.signal_type == "BULLISH":
            tp_price = entry_price * (1 + tp_percent / 100.0)
            sl_price = entry_price * (1 - sl_percent / 100.0)
        else:
            tp_price = entry_price * (1 - tp_percent / 100.0)
            sl_price = entry_price * (1 + sl_percent / 100.0)

        # Формируем текст сообщения
        message = (
            f"{emoji} {instrument.ticker} — {direction_text}\n"
            f"-------------------------------------------------\n"
            f"Дата сигнала: {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S MSK')}\n"
            f"ТФ: {timeframe}\n"
            f"Цена входа: {entry_price:.2f} ₽\n"
            f"TP ({tp_percent:.1f}%): {tp_price:.2f} ₽\n"
            f"SL ({sl_percent:.1f}%): {sl_price:.2f} ₽\n"
        )

        # Добавляем пометку тестового режима
        if signal.metadata and signal.metadata.get("test_mode"):
            message += f"\n⚠️ Тестовый сигнал"

        # Формируем параметры VK API запроса (GET-запрос, как требует messages.send)
        params = {
            "access_token": self.vk_token,
            "v": VK_API_VERSION,
            "peer_id": int(self.vk_peer_id),
            "message": message,
            "random_id": random.randint(0, 2**31 - 1),
        }
        

        query_string = urllib.parse.urlencode(params)
        url = f"{VK_API_URL}?{query_string}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "selima-notifier/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                result = json.loads(body)

                if result.get("response") is not None:
                    msg_id = result["response"]
                    logger.info(f"  📨 VK notification SENT (msg_id={msg_id})")
                    return True
                else:
                    error = result.get("error", {})
                    logger.error(f"  ❌ VK API error: code={error.get('error_code')}, msg={error.get('error_msg')}")
                    return False

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"  ❌ VK HTTP {e.code}: {body}")
            return False
        except Exception as e:
            logger.error(f"  ❌ VK send failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════════════
    # 🟡 P1: СБОР СТОП-ОРДЕРОВ (ВАЖНО) — БЕЗ ИЗМЕНЕНИЙ
    # ═══════════════════════════════════════════════════════════════════════

    def collect_stop_orders(self):
        if not can_run_task(TaskPriority.IMPORTANT):
            return

        with important_lock:
            try:
                logger.info("\n" + "─" * 60)
                logger.info("🟡 P1-IMPORTANT: COLLECTING STOP ORDERS")
                logger.info("─" * 60)

                from src.trading.order_collector import OrderCollector

                # Сделки теперь живут в PostgreSQL → репозитории тоже PG
                pg_trade_repo = PgLiveTradeRepository(self.history_manager.engine)
                pg_order_repo = PgStopOrderRepository(self.history_manager.engine)

                collector = OrderCollector(
                    token=self.token,
                    trade_repo=pg_trade_repo,
                    order_repo=pg_order_repo,
                )
                stats = collector.collect_pending_orders(wait_seconds=0)

                if stats["collected"] > 0:
                    self.stats["orders_collected"] += stats["collected"]

                logger.info(f"✓ Collected: {stats['collected']} orders")
                logger.info("─" * 60 + "\n")

            except Exception as e:
                logger.error(f"❌ Order collection failed: {e}")
                import traceback
                traceback.print_exc()
                self.stats["errors"] += 1

    # ═══════════════════════════════════════════════════════════════════════
    # 🟢 P2: МОНИТОРИНГ ПОЗИЦИЙ (ФОНОВОЕ) — БЕЗ ИЗМЕНЕНИЙ
    # ═══════════════════════════════════════════════════════════════════════

    def monitor_open_positions(self):
        if not can_run_task(TaskPriority.BACKGROUND):
            return

        try:
            from src.trading.reconciler import TradingReconciler

            # Сделки и стоп-ордера теперь в PostgreSQL → репозитории тоже PG
            pg_trade_repo = PgLiveTradeRepository(self.history_manager.engine)
            pg_order_repo = PgStopOrderRepository(self.history_manager.engine)

            reconciler = TradingReconciler(
                token=self.token,
                timezone=MOSCOW_TZ,
                log=logger,
                trade_repo=pg_trade_repo,
                order_repo=pg_order_repo,
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
    # 🌙 NIGHTLY: EXPORT BACKTEST RESULTS TO POSTGRES (01:00 MSK)
    # ═══════════════════════════════════════════════════════════════════════

    def run_nightly_backtest_export(self):
        """
        🌙 Ночной экспорт бектеста dual_ma_crossover в PostgreSQL.

        Запускает scripts/export_dual_ma_to_postgres.py как отдельный процесс
        (subprocess), чтобы сбой не повлиял на основной планировщик.
        Скрипт полностью перезаписывает таблицы strategy_backtest_summary
        и strategy_backtest_trades, оставляя только тикеры с PnL > 0.
        """
        import subprocess

        logger.info("\n" + "─" * 60)
        logger.info("🌙 NIGHTLY: Backtest export to PostgreSQL")
        logger.info(f"   Time: {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info("─" * 60)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.export_dual_ma_to_postgres"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,  # 10 минут максимум
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )

            if result.returncode == 0:
                logger.info("✅ Nightly backtest export completed successfully")
                # Логируем последние строки вывода (краткая сводка)
                tail = result.stdout.strip().split("\n")[-10:]
                for line in tail:
                    # Безопасное логирование: заменяем символы, непечатаемые в cp1251
                    safe_line = line.encode("cp1251", errors="replace").decode("cp1251")
                    logger.info(f"   {safe_line}")
            else:
                logger.error(f"❌ Backtest export failed (exit code {result.returncode})")
                if result.stderr:
                    logger.error(f"   stderr: {result.stderr[-500:]}")

        except subprocess.TimeoutExpired:
            logger.error("❌ Backtest export timed out (600s limit)")
        except Exception as e:
            logger.error(f"❌ Nightly backtest export error: {e}", exc_info=True)

        logger.info("─" * 60 + "\n")

    # ═══════════════════════════════════════════════════════════════════════
    # УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ — БЕЗ ИЗМЕНЕНИЙ
    # ═══════════════════════════════════════════════════════════════════════

    def setup_jobs(self):
        logger.info("⚙️  Setting up scheduler jobs...")

        # 🔴 P0: Обновление данных + сканирование сигналов — каждую минуту
        self.scheduler.add_job(
            func=self.update_and_scan,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/5",
                second="5",
                timezone=MOSCOW_TZ
            ),
            id="update_and_scan",
            name="[P0-CRITICAL] DB Update + Signal Scanner (every 5 min)",
            max_instances=1,
            coalesce=True
        )
        logger.info("   ✓ [P0-CRITICAL] DB Update + Signal Scanner (every 5 min, sec 5)")

        # 🟡 P1: Сбор стоп-ордеров каждые 2 минуты
        self.scheduler.add_job(
            func=self.collect_stop_orders,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/3",
                timezone=MOSCOW_TZ
            ),
            id="collect_stop_orders",
            name="[P1-IMPORTANT] Collect Stop Orders",
            max_instances=1,
            coalesce=True
        )
        logger.info("   ✓ [P1-IMPORTANT] Collect Stop Orders (every 2 min)")

        # 🟢 P2: Мониторинг позиций каждые 3 минуты
        self.scheduler.add_job(
            func=self.monitor_open_positions,
            trigger=CronTrigger(
                hour=f"{SCHEDULER_START_HOUR}-23,0",
                minute="*/3",
                timezone=MOSCOW_TZ
            ),
            id="monitor_positions",
            name="[P2-BACKGROUND] Monitor Positions",
            max_instances=1,
            coalesce=True
        )
        logger.info("   ✓ [P2-BACKGROUND] Monitor Positions (every 3 min)")

        # 🌙 Ночной экспорт бектеста в PostgreSQL — каждый день в 01:00 MSK
        self.scheduler.add_job(
            func=self.run_nightly_backtest_export,
            trigger=CronTrigger(
                hour=1,
                minute=0,
                timezone=MOSCOW_TZ
            ),
            id="nightly_backtest_export",
            name="[NIGHTLY] Backtest Export to PostgreSQL (01:00 MSK)",
            max_instances=1,
            coalesce=True
        )
        logger.info("   ✓ [NIGHTLY] Backtest Export to PostgreSQL (01:00 MSK)")

        logger.info("✅ All jobs configured with priorities")

    def print_schedule(self):
        logger.info("\n" + "=" * 80)
        logger.info("📅 NOTIFIER CONFIGURATION")
        logger.info("=" * 80)
        logger.info(f"Timezone: {MOSCOW_TZ}")
        logger.info(f"Active hours: {SCHEDULER_START_HOUR:02d}:00 - 00:30 (next day)")
        logger.info(f"Timeframes: {', '.join(TRADING_TIMEFRAMES)}")
        logger.info(f"Live trading: {'ON' if TRADING_MODE.is_live else 'OFF (signal-only)'}")
        # logger.info(f"📱 VK peer ID: {self.vk_peer_id}")

        logger.info("\n📋 Scheduled Jobs:")
        for job in self.scheduler.get_jobs():
            logger.info(f"   • {job.name}")
            logger.info(f"     ID: {job.id}")
            logger.info(f"     Trigger: {job.trigger}")

        logger.info("=" * 80 + "\n")

    def print_stats(self):
        logger.info("\n" + "=" * 80)
        logger.info("📊 NOTIFIER STATISTICS")
        logger.info("=" * 80)
        logger.info(f"Updates completed: {self.stats['updates_completed']}")
        logger.info(f"Strategies run: {self.stats['strategies_run']}")
        logger.info(f"Signals found: {self.stats['signals_found']}")
        logger.info(f"Notifications sent: {self.stats['notifications_sent']}")
        logger.info(f"Trades executed: {self.stats['trades_executed']}")
        logger.info(f"Orders collected: {self.stats['orders_collected']}")
        logger.info(f"Positions closed: {self.stats['positions_closed']}")
        logger.info(f"Errors: {self.stats['errors']}")

        if self.stats['last_update']:
            logger.info(f"Last update: {self.stats['last_update'].strftime('%Y-%m-%d %H:%M:%S')}")
        if self.stats['last_strategy_run']:
            logger.info(f"Last strategy run: {self.stats['last_strategy_run'].strftime('%Y-%m-%d %H:%M:%S')}")

        logger.info("=" * 80 + "\n")

    def _startup_reconcile(self):
        logger.info("🔄 STARTUP: Running initial reconciliation...")
        try:
            from src.trading.reconciler import TradingReconciler

            pg_trade_repo = PgLiveTradeRepository(self.history_manager.engine)
            pg_order_repo = PgStopOrderRepository(self.history_manager.engine)

            reconciler = TradingReconciler(
                token=self.token,
                timezone=MOSCOW_TZ,
                log=logger,
                trade_repo=pg_trade_repo,
                order_repo=pg_order_repo,
            )
            stats = reconciler.reconcile(skip_weekends=False)
            logger.info(
                f"✅ Startup reconcile done: "
                f"executed={stats.executed_positions_closed}, "
                f"manual={stats.manual_positions_closed}, "
                f"errors={stats.errors}"
            )
        except Exception as e:
            logger.error(f"❌ Startup reconcile failed: {e}", exc_info=True)

    def start(self):
        logger.info("🔔 Starting Trading Notifier...")
        self.setup_jobs()
        self.print_schedule()

        def signal_handler(signum, frame):
            logger.info("\n⚠️  Shutdown signal received")
            self.print_stats()
            logger.info("👋 Shutting down gracefully...")
            self.scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("✅ Notifier started successfully")
        logger.info("Press Ctrl+C to stop\n")

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("\n👋 Notifier stopped")
            self.print_stats()


# ═══════════════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

def main():
    try:
        notifier = TradingNotifier()
        notifier.start()
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()