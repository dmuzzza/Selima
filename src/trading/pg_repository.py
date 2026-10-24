"""
PostgreSQL Repositories for Live Trading.

Полные репозитории сделок и стоп-ордеров, работающие с PostgreSQL вместо SQLite.

Таблицы `live_trades` и `stop_orders` уже существуют в PostgreSQL с той же схемой,
что и в SQLite. Классы реализуют интерфейс, совместимый с:
  - LiveTradeRepository  (src/trading/repository.py)
  - StopOrderRepository  (src/trading/stop_order_repository.py)

Используются в scheduler_notifier (и везде, где нужен PostgreSQL вместо SQLite).
"""

import json
import logging
from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy import text

from src.trading.models import LiveTrade, TradeStatus
from src.trading.stop_order_models import StopOrderRecord, StopOrderStatus

logger = logging.getLogger(__name__)


def _get_engine():
    """Создать engine из .env (как в HistoryManager)."""
    import os
    from sqlalchemy import create_engine

    db_url = (
        f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
        f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
        f"?sslmode=require"
    )
    return create_engine(db_url, pool_pre_ping=True)


# Общий список колонок для INSERT: используется и в upsert (save), и в
# идемпотентной вставке (create_pending_if_absent).
_INSERT_COLUMNS = """
    trade_id, position_id, strategy_name, timeframe, timestamp,
    ticker, figi, direction, signal_key,
    entry_time, entry_price, lots, lot_size, position_size,
    exit_time, exit_price, exit_type,
    pnl_abs, pnl_percent,
    tp_price, sl_price, tp_percent, sl_percent,
    status, account_id, account_id_hash, account_id_short, orders_collected,
    error_message, broker, metadata
"""

_INSERT_VALUES = """
    :trade_id, :position_id, :strategy_name, :timeframe, :timestamp,
    :ticker, :figi, :direction, :signal_key,
    :entry_time, :entry_price, :lots, :lot_size, :position_size,
    :exit_time, :exit_price, :exit_type,
    :pnl_abs, :pnl_percent,
    :tp_price, :sl_price, :tp_percent, :sl_percent,
    :status, :account_id, :account_id_hash, :account_id_short, :orders_collected,
    :error_message, :broker, CAST(:metadata AS jsonb)
"""


class PgLiveTradeRepository:
    """
    Репозиторий сделок в PostgreSQL (таблица live_trades).

    Совместим с интерфейсом LiveTradeRepository (SQLite), но все операции
    выполняются в PostgreSQL.
    """

    def __init__(self, engine=None):
        self.engine = engine or _get_engine()

        # DDL выполняется один раз на engine: репозиторий создаётся в каждом
        # цикле планировщика, повторный ALTER/CREATE INDEX не нужен.
        if not getattr(self.engine, "_live_trades_schema_ready", False):
            with self.engine.begin() as conn:
                # Уникальный индекс по trade_id нужен для ON CONFLICT (upsert)
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_trades_trade_id "
                    "ON live_trades(trade_id)"
                ))
                # Идемпотентность сигналов: один signal_key -> максимум одна сделка.
                # Частичный индекс: старые записи без ключа (NULL) не конфликтуют.
                conn.execute(text(
                    "ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS signal_key TEXT"
                ))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_trades_signal_key "
                    "ON live_trades(signal_key) WHERE signal_key IS NOT NULL"
                ))
            setattr(self.engine, "_live_trades_schema_ready", True)

    # ─────────────────────────── Запись ───────────────────────────

    def _insert_params(self, trade: LiveTrade) -> dict:
        """Параметры bind для INSERT в live_trades (account_id шифруется)."""
        from src.utils.crypto import get_crypto

        crypto = get_crypto()

        return {
            "trade_id": trade.trade_id,
            "position_id": trade.position_id,
            "strategy_name": trade.strategy_name,
            "timeframe": trade.timeframe,
            "timestamp": trade.timestamp or datetime.now(),
            "ticker": trade.ticker,
            "figi": trade.figi,
            "direction": trade.direction,
            "signal_key": trade.signal_key,
            "entry_time": trade.entry_time,
            "entry_price": trade.entry_price,
            "lots": trade.lots,
            "lot_size": trade.lot_size or 1,
            "position_size": trade.position_size,
            "exit_time": trade.exit_time,
            "exit_price": trade.exit_price,
            "exit_type": trade.exit_type,
            "pnl_abs": trade.pnl_abs,
            "pnl_percent": trade.pnl_percent,
            "tp_price": trade.tp_price,
            "sl_price": trade.sl_price,
            "tp_percent": trade.tp_percent,
            "sl_percent": trade.sl_percent,
            "status": trade.status.value,
            "account_id": crypto.encrypt(trade.account_id),
            "account_id_hash": crypto.hash_account_id(trade.account_id),
            "account_id_short": trade.account_id_short,
            "orders_collected": 1 if trade.orders_collected else 0,
            "error_message": trade.error_message,
            "broker": trade.broker,
            "metadata": json.dumps(trade.metadata, ensure_ascii=False) if trade.metadata else "{}",
        }

    def save(self, trade: LiveTrade) -> bool:
        """Сохранить или обновить сделку (upsert по trade_id)."""
        try:
            params = self._insert_params(trade)

            with self.engine.begin() as conn:
                conn.execute(text(f"""
                    INSERT INTO live_trades ({_INSERT_COLUMNS})
                    VALUES ({_INSERT_VALUES})
                    ON CONFLICT (trade_id) DO UPDATE SET
                        position_id       = EXCLUDED.position_id,
                        strategy_name     = EXCLUDED.strategy_name,
                        timeframe         = EXCLUDED.timeframe,
                        timestamp         = EXCLUDED.timestamp,
                        ticker            = EXCLUDED.ticker,
                        figi              = EXCLUDED.figi,
                        direction         = EXCLUDED.direction,
                        signal_key        = EXCLUDED.signal_key,
                        entry_time        = EXCLUDED.entry_time,
                        entry_price       = EXCLUDED.entry_price,
                        lots              = EXCLUDED.lots,
                        lot_size          = EXCLUDED.lot_size,
                        position_size     = EXCLUDED.position_size,
                        exit_time         = EXCLUDED.exit_time,
                        exit_price        = EXCLUDED.exit_price,
                        exit_type         = EXCLUDED.exit_type,
                        pnl_abs           = EXCLUDED.pnl_abs,
                        pnl_percent       = EXCLUDED.pnl_percent,
                        tp_price          = EXCLUDED.tp_price,
                        sl_price          = EXCLUDED.sl_price,
                        tp_percent        = EXCLUDED.tp_percent,
                        sl_percent        = EXCLUDED.sl_percent,
                        status            = EXCLUDED.status,
                        account_id        = EXCLUDED.account_id,
                        account_id_hash   = EXCLUDED.account_id_hash,
                        account_id_short  = EXCLUDED.account_id_short,
                        orders_collected  = EXCLUDED.orders_collected,
                        error_message     = EXCLUDED.error_message,
                        broker            = EXCLUDED.broker,
                        metadata          = EXCLUDED.metadata
                """), params)
            logger.debug(f"✓ Trade saved to PostgreSQL: {trade.trade_id}")
            return True

        except Exception as e:
            logger.error(f"✗ Failed to save trade {trade.trade_id} to PostgreSQL: {e}")
            return False

    def create_pending_if_absent(self, trade: LiveTrade) -> bool:
        """
        Атомарно вставить PENDING-сделку, если её signal_key ещё не занят.

        Возвращает True, если запись создана, и False, если сигнал уже обработан
        (в том числе параллельным процессом или до рестарта).
        """
        if not trade.signal_key:
            raise ValueError("create_pending_if_absent requires trade.signal_key")

        try:
            params = self._insert_params(trade)

            with self.engine.begin() as conn:
                result = conn.execute(text(f"""
                    INSERT INTO live_trades ({_INSERT_COLUMNS})
                    VALUES ({_INSERT_VALUES})
                    ON CONFLICT (signal_key) WHERE signal_key IS NOT NULL DO NOTHING
                """), params)
                inserted = result.rowcount == 1

            if inserted:
                logger.info(f"📝 Trade record created: {trade.trade_id} (PENDING)")
            else:
                logger.warning(f"🚫 DUPLICATE SIGNAL PREVENTED: {trade.signal_key}")
            return inserted

        except Exception as e:
            logger.error(f"✗ Failed to insert pending trade {trade.trade_id}: {e}")
            raise

    # ─────────────────────────── Чтение ───────────────────────────

    def get_by_id(self, trade_id: str) -> Optional[LiveTrade]:
        """Получить сделку по ID."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM live_trades WHERE trade_id = :id"),
                {"id": trade_id},
            ).mappings().first()
        return self._row_to_trade(row) if row else None

    def get_open_trades(self) -> List[LiveTrade]:
        """Все открытые сделки (status = OPEN)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM live_trades WHERE status = 'OPEN' ORDER BY timestamp DESC")
            ).mappings().all()
        return [self._row_to_trade(r) for r in rows]

    def get_open_trades_by_ticker(
        self, ticker: str, account_id: str, timeframe: Optional[str] = None
    ) -> List[LiveTrade]:
        """Открытые сделки по тикеру/аккаунту/таймфрейму."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()
        account_id_hash = crypto.hash_account_id(account_id)

        sql = """
            SELECT * FROM live_trades
            WHERE ticker = :ticker
              AND account_id_hash = :hash
              AND status = 'OPEN'
        """
        params: Dict[str, object] = {"ticker": ticker, "hash": account_id_hash}
        if timeframe:
            sql += " AND timeframe = :timeframe"
            params["timeframe"] = timeframe
        sql += " ORDER BY entry_time DESC NULLS LAST"

        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [self._row_to_trade(r) for r in rows]

    def has_open_trade_on_timeframe_accounts(
        self,
        *,
        ticker: str,
        timeframe: str,
        direction: str,
        account_ids: List[str],
    ) -> bool:
        """Есть ли открытая сделка (ticker,timeframe,direction) на любом из счетов."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()

        if not account_ids:
            return False

        hashes = [crypto.hash_account_id(a) for a in account_ids]
        placeholders = ",".join([":h%d" % i for i in range(len(hashes))])
        params: Dict[str, object] = {
            "ticker": ticker,
            "timeframe": timeframe,
            "direction": direction,
        }
        for i, h in enumerate(hashes):
            params["h%d" % i] = h

        sql = f"""
            SELECT 1
            FROM live_trades
            WHERE ticker = :ticker
              AND timeframe = :timeframe
              AND direction = :direction
              AND status = 'OPEN'
              AND account_id_hash IN ({placeholders})
            LIMIT 1
        """
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), params).fetchone()
        return row is not None

    def get_trades_without_orders(self) -> List[LiveTrade]:
        """
        Сделки, для которых ещё не собраны стоп-ордера
        (status OPEN/PENDING, position_id задан, orders_collected = 0).
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM live_trades
                WHERE status IN ('OPEN', 'PENDING')
                  AND position_id IS NOT NULL
                  AND orders_collected = 0
                  AND entry_time IS NOT NULL
                ORDER BY entry_time DESC
            """)).mappings().all()
        return [self._row_to_trade(r) for r in rows]

    # ─────────────────────────── Закрытие ───────────────────────────

    def close_trade_by_id(self, trade_id: str, exit_type: str, exit_price: float) -> bool:
        """Закрыть сделку по trade_id (аналог LiveTradeRepository.close_trade_by_id)."""
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM live_trades WHERE trade_id = :id AND status = 'OPEN'"),
                    {"id": trade_id},
                ).mappings().first()

            if not row:
                logger.warning(f"⚠️ Trade {trade_id} not found or already closed")
                return False

            trade = self._row_to_trade(row)

            entry_price = trade.entry_price or 0.0
            shares = (trade.lots or 1) * (trade.lot_size or 1)

            if trade.direction == "LONG":
                pnl_abs = (exit_price - entry_price) * shares
                pnl_percent = ((exit_price / entry_price) - 1) * 100 if entry_price else 0.0
            else:
                pnl_abs = (entry_price - exit_price) * shares
                pnl_percent = ((entry_price - exit_price) / entry_price) * 100 if entry_price else 0.0

            trade.exit_time = datetime.now()
            trade.exit_price = exit_price
            trade.exit_type = exit_type
            trade.pnl_abs = round(pnl_abs, 2)
            trade.pnl_percent = round(pnl_percent, 4)
            trade.status = TradeStatus.CLOSED

            if self.save(trade):
                logger.info(
                    f"✅ Trade closed: {trade.ticker} ({exit_type} @ {exit_price:.2f}, "
                    f"P&L: {trade.pnl_abs:.2f} RUB / {trade.pnl_percent:.2f}%)"
                )
                return True
            return False

        except Exception as e:
            logger.error(f"✗ Failed to close trade {trade_id}: {e}")
            return False

    # ─────────────────────────── Конвертация ───────────────────────────

    @staticmethod
    def _row_to_trade(row) -> LiveTrade:
        """Строка PostgreSQL → LiveTrade."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()

        encrypted_account_id = row["account_id"]
        account_id = crypto.decrypt(encrypted_account_id)

        return LiveTrade(
            trade_id=row["trade_id"],
            position_id=row["position_id"],
            strategy_name=row["strategy_name"],
            timeframe=row["timeframe"],
            timestamp=row["timestamp"],
            ticker=row["ticker"],
            figi=row["figi"],
            direction=row["direction"],
            signal_key=row.get("signal_key"),
            entry_time=row["entry_time"],
            entry_price=row["entry_price"],
            lots=row["lots"],
            lot_size=row["lot_size"] or 1,
            position_size=row["position_size"],
            exit_time=row["exit_time"],
            exit_price=row["exit_price"],
            exit_type=row["exit_type"],
            pnl_abs=row["pnl_abs"],
            pnl_percent=row["pnl_percent"],
            tp_price=row["tp_price"],
            sl_price=row["sl_price"],
            tp_percent=row["tp_percent"] or 0.0,
            sl_percent=row["sl_percent"] or 0.0,
            status=TradeStatus(row["status"]),
            account_id=account_id,
            account_id_short=row["account_id_short"] or account_id[-4:],
            orders_collected=bool(row["orders_collected"]),
            error_message=row["error_message"],
            broker=row["broker"] or "tinkoff",
            metadata=row["metadata"] or {},
        )


class PgStopOrderRepository:
    """
    Репозиторий стоп-ордеров в PostgreSQL (таблица stop_orders).

    Совместим с интерфейсом StopOrderRepository (SQLite).
    """

    def __init__(self, engine=None):
        self.engine = engine or _get_engine()

    # ─────────────────────────── Запись ───────────────────────────

    def save(self, order: StopOrderRecord) -> bool:
        """Сохранить или обновить стоп-ордер (upsert по stop_order_id)."""
        try:
            from src.utils.crypto import get_crypto
            crypto = get_crypto()

            encrypted_account_id = crypto.encrypt(order.account_id)
            account_id_hash = crypto.hash_account_id(order.account_id)

            with self.engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO stop_orders (
                        stop_order_id, position_id, trade_id, order_type, ticker, figi,
                        stop_price, lots_requested,
                        created_at, last_checked_at, executed_at,
                        status, account_id, account_id_hash, account_id_short, metadata
                    ) VALUES (
                        :stop_order_id, :position_id, :trade_id, :order_type, :ticker, :figi,
                        :stop_price, :lots_requested,
                        :created_at, :last_checked_at, :executed_at,
                        :status, :account_id, :account_id_hash, :account_id_short, CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (stop_order_id) DO UPDATE SET
                        position_id       = EXCLUDED.position_id,
                        trade_id          = EXCLUDED.trade_id,
                        order_type        = EXCLUDED.order_type,
                        ticker            = EXCLUDED.ticker,
                        figi              = EXCLUDED.figi,
                        stop_price        = EXCLUDED.stop_price,
                        lots_requested    = EXCLUDED.lots_requested,
                        created_at        = EXCLUDED.created_at,
                        last_checked_at   = EXCLUDED.last_checked_at,
                        executed_at       = EXCLUDED.executed_at,
                        status            = EXCLUDED.status,
                        account_id        = EXCLUDED.account_id,
                        account_id_hash   = EXCLUDED.account_id_hash,
                        account_id_short  = EXCLUDED.account_id_short,
                        metadata          = EXCLUDED.metadata
                """), {
                    "stop_order_id": order.stop_order_id,
                    "position_id": order.position_id,
                    "trade_id": order.trade_id,
                    "order_type": order.order_type,
                    "ticker": order.ticker,
                    "figi": order.figi,
                    "stop_price": order.stop_price,
                    "lots_requested": order.lots_requested,
                    "created_at": order.created_at,
                    "last_checked_at": order.last_checked_at,
                    "executed_at": order.executed_at,
                    "status": order.status.value,
                    "account_id": encrypted_account_id,
                    "account_id_hash": account_id_hash,
                    "account_id_short": order.account_id_short,
                    "metadata": json.dumps(order.metadata, ensure_ascii=False) if order.metadata else "{}",
                })
            logger.debug(f"✓ Stop order saved to PostgreSQL: {order.stop_order_id}")
            return True

        except Exception as e:
            logger.error(f"✗ Failed to save stop order {order.stop_order_id} to PostgreSQL: {e}")
            return False

    # ─────────────────────────── Чтение ───────────────────────────

    def get_active_orders(self, account_id: Optional[str] = None) -> List[StopOrderRecord]:
        """Все активные стоп-ордера (можно отфильтровать по аккаунту)."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()

        if account_id:
            account_id_hash = crypto.hash_account_id(account_id)
            sql = text("""
                SELECT * FROM stop_orders
                WHERE status = 'ACTIVE' AND account_id_hash = :hash
                ORDER BY created_at DESC
            """)
            params = {"hash": account_id_hash}
        else:
            sql = text("""
                SELECT * FROM stop_orders
                WHERE status = 'ACTIVE'
                ORDER BY created_at DESC
            """)
            params = {}

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [self._row_to_model(r) for r in rows]

    def get_orders_by_position_id(self, position_id: str) -> List[StopOrderRecord]:
        """Стоп-ордера по position_id."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM stop_orders WHERE position_id = :pid ORDER BY created_at"),
                {"pid": position_id},
            ).mappings().all()
        return [self._row_to_model(r) for r in rows]

    # ─────────────────────────── Конвертация ───────────────────────────

    @staticmethod
    def _row_to_model(row) -> StopOrderRecord:
        """Строка PostgreSQL → StopOrderRecord."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()

        encrypted_account_id = row["account_id"]
        account_id = crypto.decrypt(encrypted_account_id)

        raw_status = row["status"]
        try:
            status = StopOrderStatus(raw_status)
        except Exception:
            logger.warning(
                f"⚠️ Unknown stop order status in DB: {raw_status} (stop_order_id={row['stop_order_id']})"
            )
            status = StopOrderStatus.UNKNOWN

        return StopOrderRecord(
            id=row["id"],
            stop_order_id=row["stop_order_id"],
            position_id=row["position_id"],
            trade_id=row["trade_id"],
            order_type=row["order_type"],
            ticker=row["ticker"],
            figi=row["figi"],
            stop_price=row["stop_price"],
            lots_requested=row["lots_requested"],
            created_at=row["created_at"],
            last_checked_at=row["last_checked_at"],
            executed_at=row["executed_at"],
            status=status,
            account_id=account_id,
            account_id_short=row["account_id_short"] or account_id[-4:],
            metadata=row["metadata"] or {},
        )
