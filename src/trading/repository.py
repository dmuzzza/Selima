"""
Live Trade Repository

Репозиторий для сохранения и загрузки реальных сделок из БД.
"""

import logging
import sqlite3
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from .models import LiveTrade, TradeStatus

logger = logging.getLogger(__name__)


class LiveTradeRepository:
    """
    Репозиторий для работы с реальными сделками в БД.
    """
    
    def __init__(self, db_path: str = "data/live_trades.db"):
        """
        Инициализация репозитория.
        
        Args:
            db_path: Путь к файлу БД
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Создаём таблицу если не существует
        self._create_table()
        
        logger.info(f"✓ LiveTradeRepository initialized: {self.db_path}")
    
    def _create_table(self):
        """Создать таблицу сделок если не существует."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS live_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE NOT NULL,
                    position_id TEXT,
                    strategy_name TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    ticker TEXT NOT NULL,
                    figi TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_time TIMESTAMP,
                    entry_price REAL,
                    lots INTEGER,
                    lot_size INTEGER DEFAULT 1,
                    position_size REAL,
                    exit_time TIMESTAMP,
                    exit_price REAL,
                    exit_type TEXT,
                    pnl_abs REAL,
                    pnl_percent REAL,
                    tp_price REAL,
                    sl_price REAL,
                    tp_percent REAL,
                    sl_percent REAL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    account_id TEXT NOT NULL,
                    account_id_hash TEXT,
                    account_id_short TEXT,
                    orders_collected INTEGER DEFAULT 0,
                    error_message TEXT,
                    broker TEXT DEFAULT 'tinkoff',
                    metadata TEXT
                )
            """)
            
            # Индексы
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_live_trades_status
                ON live_trades(status)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_live_trades_position_id
                ON live_trades(position_id)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_live_trades_ticker_account
                ON live_trades(ticker, account_id, status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_live_trades_hash_ticker
                ON live_trades(account_id_hash, ticker, status)
            """)
            
            conn.commit()
    
    def save(self, trade: LiveTrade) -> bool:
        """Сохранить или обновить сделку в БД."""
        try:

            from src.utils.crypto import get_crypto
            crypto = get_crypto()
            
            # Шифруем account_id перед сохранением
            encrypted_account_id = crypto.encrypt(trade.account_id)

            # ✅ Хешируем для поиска
            account_id_hash = crypto.hash_account_id(trade.account_id)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO live_trades (
                        trade_id, position_id, strategy_name, timeframe, timestamp,
                        ticker, figi, direction,
                        entry_time, entry_price, lots, lot_size, position_size,
                        exit_time, exit_price, exit_type,
                        pnl_abs, pnl_percent,
                        tp_price, sl_price, tp_percent, sl_percent,
                        status, account_id, account_id_hash, account_id_short, orders_collected,
                        error_message, broker, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade.trade_id,
                    trade.position_id,
                    trade.strategy_name,
                    trade.timeframe,
                    trade.timestamp.isoformat() if trade.timestamp else datetime.now().isoformat(),
                    trade.ticker,
                    trade.figi,
                    trade.direction,
                    trade.entry_time.isoformat() if trade.entry_time else None,
                    trade.entry_price,
                    trade.lots,
                    trade.lot_size or 1,
                    trade.position_size,
                    trade.exit_time.isoformat() if trade.exit_time else None,
                    trade.exit_price,
                    trade.exit_type,
                    trade.pnl_abs,
                    trade.pnl_percent,
                    trade.tp_price,
                    trade.sl_price,
                    trade.tp_percent,
                    trade.sl_percent,
                    trade.status.value,
                    encrypted_account_id,
                    account_id_hash,
                    trade.account_id_short,
                    1 if trade.orders_collected else 0,
                    trade.error_message,
                    trade.broker,
                    json.dumps(trade.metadata) if trade.metadata else None
                ))
                
                conn.commit()
                logger.debug(f"✓ Trade saved: {trade.trade_id}")
                return True
                
        except Exception as e:
            logger.error(f"✗ Failed to save trade {trade.trade_id}: {e}")
            return False
    
    def get_by_id(self, trade_id: str) -> Optional[LiveTrade]:
        """
        Получить сделку по ID.
        
        Args:
            trade_id: ID сделки
        
        Returns:
            LiveTrade или None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM live_trades WHERE trade_id = ?",
                    (trade_id,)
                ).fetchone()
                
                if row:
                    return self._row_to_trade(row)
                return None
                
        except Exception as e:
            logger.error(f"✗ Failed to get trade {trade_id}: {e}")
            return None
    
    def get_open_trades(self) -> List[LiveTrade]:
        """
        Получить все открытые сделки.
        
        Returns:
            List[LiveTrade]: Список открытых сделок
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM live_trades WHERE status = ? ORDER BY timestamp DESC",
                    (TradeStatus.OPEN.value,)
                ).fetchall()
                
                return [self._row_to_trade(row) for row in rows]
                
        except Exception as e:
            logger.error(f"✗ Failed to get open trades: {e}")
            return []

    def get_trades_without_orders(self) -> List[LiveTrade]:
        """
        Получить сделки, для которых ещё не собраны стоп-ордера
        (status OPEN/PENDING, position_id задан, orders_collected = 0).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM live_trades
                WHERE status IN ('OPEN', 'PENDING')
                AND position_id IS NOT NULL
                AND orders_collected = 0
                AND entry_time IS NOT NULL
                ORDER BY entry_time DESC
            """)
            return [self._row_to_trade(row) for row in cursor.fetchall()]
    
    from typing import List

    def has_open_trade_on_timeframe_accounts(
        self,
        *,
        ticker: str,
        timeframe: str,
        direction: str,          # "LONG"/"SHORT"
        account_ids: List[str],
    ) -> bool:
        """
        True если уже есть OPEN-сделка по (ticker,timeframe,direction) на любом из переданных счетов.
        """
        if not account_ids:
            return False

        try:
            from src.utils.crypto import get_crypto
            crypto = get_crypto()

            hashes = [crypto.hash_account_id(a) for a in account_ids]
            placeholders = ",".join(["?"] * len(hashes))

            sql = f"""
            SELECT 1
            FROM live_trades
            WHERE ticker = ?
            AND timeframe = ?
            AND direction = ?
            AND status = 'OPEN'
            AND account_id_hash IN ({placeholders})
            LIMIT 1
            """

            params = [ticker, timeframe, direction, *hashes]

            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(sql, params).fetchone()
                return row is not None

        except Exception as e:
            logger.error(f"✗ Failed duplicate check for {ticker} {timeframe} {direction}: {e}")
            return False



    def get_open_trades_by_ticker(
        self,
        ticker: str,
        account_id: str,
        timeframe: Optional[str] = None
    ) -> List[LiveTrade]:
        """
        Получить открытые сделки по конкретному тикеру.
        
        Args:
            ticker: Тикер инструмента (SBER, GAZP)
            account_id: ID счёта
            timeframe: Таймфрейм (опционально)
        
        Returns:
            List[LiveTrade]: Список открытых сделок
        """

        from src.utils.crypto import get_crypto
        crypto = get_crypto()
        
        # ✅ ШИФРУЕМ account_id перед поиском в БД
        encrypted_account_id = crypto.encrypt(account_id)
        account_id_hash = crypto.hash_account_id(account_id)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if timeframe:
                cursor = conn.execute("""
                    SELECT * FROM live_trades
                    WHERE ticker = ?
                      AND account_id_hash = ?
                      AND timeframe = ?
                      AND status = 'OPEN'
                    ORDER BY entry_time DESC
                """, (ticker, account_id_hash, timeframe))
            else:
                cursor = conn.execute("""
                    SELECT * FROM live_trades
                    WHERE ticker = ?
                      AND account_id_hash = ?
                      AND status = 'OPEN'
                    ORDER BY entry_time DESC
                """, (ticker, account_id_hash))
            
            rows = cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]  # ✅ Исправлено!
    
    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> LiveTrade:
        """Конвертировать строку БД в LiveTrade."""
        from src.utils.crypto import get_crypto
        crypto = get_crypto()
        
        # Расшифровываем account_id
        encrypted_account_id = row['account_id']
        account_id = crypto.decrypt(encrypted_account_id)
        return LiveTrade(
            trade_id=row['trade_id'],
            position_id=row['position_id'] if row['position_id'] else None,
            strategy_name=row['strategy_name'],
            timeframe=row['timeframe'],
            timestamp=datetime.fromisoformat(row['timestamp']),
            ticker=row['ticker'],
            figi=row['figi'],
            direction=row['direction'],
            entry_time=datetime.fromisoformat(row['entry_time']) if row['entry_time'] else None,
            entry_price=row['entry_price'],
            lots=row['lots'],
            position_size=row['position_size'],
            exit_time=datetime.fromisoformat(row['exit_time']) if row['exit_time'] else None,
            exit_price=row['exit_price'],
            exit_type=row['exit_type'],
            pnl_abs=row['pnl_abs'],
            pnl_percent=row['pnl_percent'],
            tp_price=row['tp_price'],
            sl_price=row['sl_price'],
            tp_percent=row['tp_percent'],
            sl_percent=row['sl_percent'],
            status=TradeStatus(row['status']),
            account_id=account_id,
            account_id_short=row['account_id_short'] if row['account_id_short'] else row['account_id'][-4:],
            orders_collected=bool(row['orders_collected']) if 'orders_collected' in row.keys() else False,
            error_message=row['error_message'],
            broker=row['broker'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {}
        )
    
    def close_trade_by_id(
        self,
        trade_id: str,
        exit_type: str,
        exit_price: float
    ) -> bool:
        """
        Закрыть сделку по trade_id (надежнее чем по position_id, потому что position_id может повторяться).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                row = conn.execute(
                    """
                    SELECT * FROM live_trades
                    WHERE trade_id = ?
                    AND status = 'OPEN'
                    """,
                    (trade_id,),
                ).fetchone()

                if not row:
                    logger.warning(f"⚠️ Trade {trade_id} not found or already closed")
                    return False

                trade = self._row_to_trade(row)

                # --- P&L логика: копия из close_position_by_order ---
                entry_price = trade.entry_price


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



    def close_position_by_order(
        self,
        position_id: str,
        exit_type: str,
        exit_price: float
    ) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                row = conn.execute("""
                    SELECT * FROM live_trades
                    WHERE position_id = ?
                    AND status = 'OPEN'
                """, (position_id,)).fetchone()
                
                if not row:
                    logger.warning(f"⚠️ Position {position_id} not found or already closed")
                    return False
                
                trade = self._row_to_trade(row)
                
                # ✅ ПРАВИЛЬНЫЙ РАСЧЁТ P&L
                # entry_price может быть суммой сделки или ценой за акцию
                # Используем tp_price/sl_price для проверки диапазона
                entry_price = trade.entry_price
                
                # Защита от неправильного entry_price
                
                
                # Рассчитываем P&L
                shares = (trade.lots or 1) * (trade.lot_size or 1)

                if trade.direction == "LONG":
                    pnl_abs = (exit_price - entry_price) * shares
                    pnl_percent = ((exit_price / entry_price) - 1) * 100 if entry_price else 0.0
                else:
                    pnl_abs = (entry_price - exit_price) * shares
                    pnl_percent = ((entry_price - exit_price) / entry_price) * 100 if entry_price else 0.0
                
                # Обновляем сделку
                trade.exit_time = datetime.now()
                trade.exit_price = exit_price
                trade.exit_type = exit_type
                trade.pnl_abs = round(pnl_abs, 2)
                trade.pnl_percent = round(pnl_percent, 4)
                trade.status = TradeStatus.CLOSED
                
                if self.save(trade):
                    logger.info(
                        f"✅ Position closed: {trade.ticker} "
                        f"({exit_type} @ {exit_price:.2f}, "
                        f"P&L: {pnl_abs:+.2f} RUB / {pnl_percent:+.2f}%)"
                    )
                    return True
                
                return False
        
        except Exception as e:
            logger.error(f"❌ Failed to close position {position_id}: {e}")
            return False

    
    def get_by_position_id(self, position_id: str) -> Optional[LiveTrade]:
        """
        Получить сделку по position_id.
        
        Args:
            position_id: ID позиции
        
        Returns:
            LiveTrade или None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM live_trades WHERE position_id = ?",
                    (position_id,)
                ).fetchone()
                
                if row:
                    return self._row_to_trade(row)
                return None
        
        except Exception as e:
            logger.error(f"✗ Failed to get trade by position_id {position_id}: {e}")
            return None


