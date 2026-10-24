"""
Stop Order Repository

Работа с БД стоп-ордеров.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from .stop_order_models import StopOrderRecord, StopOrderStatus

logger = logging.getLogger(__name__)


class StopOrderRepository:
    """Repository для стоп-ордеров."""
    
    def __init__(self, db_path: str = "data/live_trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"✓ StopOrderRepository initialized: {self.db_path}")
    
    def _init_db(self):
        """Создать таблицу если не существует."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stop_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stop_order_id TEXT UNIQUE NOT NULL,
                    position_id TEXT NOT NULL,
                    trade_id TEXT,  
                    order_type TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    figi TEXT NOT NULL,
                    stop_price REAL NOT NULL,
                    lots_requested INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    last_checked_at TIMESTAMP,
                    executed_at TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    account_id TEXT NOT NULL,
                    account_id_hash TEXT,
                    account_id_short TEXT,
                    metadata TEXT,
                    FOREIGN KEY (position_id) REFERENCES live_trades(position_id)
                )
            """)
            
            # Индексы
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stop_orders_status 
                ON stop_orders(status)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stop_orders_position_id 
                ON stop_orders(position_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stop_orders_trade_id 
                ON stop_orders(trade_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stop_orders_hash
                ON stop_orders(account_id_hash, status)
            """)
            
            conn.commit()
            logger.info("✓ Stop orders table initialized")
    
    def save(self, order: StopOrderRecord) -> bool:
        """Сохранить или обновить стоп-ордер."""
        try:

            from src.utils.crypto import get_crypto
            crypto = get_crypto()
            
            # Шифруем account_id
            encrypted_account_id = crypto.encrypt(order.account_id)
            account_id_hash = crypto.hash_account_id(order.account_id)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO stop_orders (
                        stop_order_id, position_id, trade_id, order_type, ticker, figi,
                        stop_price, lots_requested,
                        created_at, last_checked_at, executed_at,
                        status, account_id, account_id_hash, account_id_short, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order.stop_order_id,
                    order.position_id,
                    order.trade_id,
                    order.order_type,
                    order.ticker,
                    order.figi,
                    order.stop_price,
                    order.lots_requested,
                    order.created_at.isoformat(),
                    order.last_checked_at.isoformat() if order.last_checked_at else None,
                    order.executed_at.isoformat() if order.executed_at else None,
                    order.status.value,
                    encrypted_account_id,
                    account_id_hash,
                    order.account_id_short,
                    json.dumps(order.metadata) if order.metadata else None
                ))
                
                conn.commit()
                logger.debug(f"✓ Stop order saved: {order.stop_order_id}")
                return True
                
        except Exception as e:
            logger.error(f"✗ Failed to save stop order {order.stop_order_id}: {e}")
            return False
    
    def get_active_orders(self, account_id: Optional[str] = None) -> List[StopOrderRecord]:
        """Получить все активные ордера."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if account_id:

                from src.utils.crypto import get_crypto
                crypto = get_crypto()
                
                # ✅ ШИФРУЕМ перед поиском
                encrypted_account_id = crypto.encrypt(account_id)

                account_id_hash = crypto.hash_account_id(account_id)
                cursor = conn.execute("""
                    SELECT * FROM stop_orders 
                    WHERE status = 'ACTIVE' AND account_id_hash = ?
                    ORDER BY created_at DESC
                """, (account_id_hash,))
            else:
                cursor = conn.execute("""
                    SELECT * FROM stop_orders 
                    WHERE status = 'ACTIVE'
                    ORDER BY created_at DESC
                """)
            
            return [self._row_to_model(row) for row in cursor.fetchall()]
    
    def get_orders_by_position_id(self, position_id: str) -> List[StopOrderRecord]:
        """Получить ордера по position_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM stop_orders 
                WHERE position_id = ?
                ORDER BY created_at
            """, (position_id,))
            
            return [self._row_to_model(row) for row in cursor.fetchall()]
    
    def get_pending_positions(self) -> List[str]:
        """
        Получить position_id сделок, для которых НЕ собраны ордера.
        
        Returns:
            List[str]: Список position_id
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT lt.position_id
                FROM live_trades lt
                WHERE lt.status = 'OPEN'
                  AND lt.position_id IS NOT NULL
                  AND lt.orders_collected = 0
                ORDER BY lt.entry_time DESC
            """)
            
            return [row[0] for row in cursor.fetchall()]
    
    def _row_to_model(self, row: sqlite3.Row) -> StopOrderRecord:
        """Преобразовать строку БД в модель."""

        from src.utils.crypto import get_crypto
        crypto = get_crypto()
        
        # Расшифровываем account_id
        encrypted_account_id = row['account_id']
        account_id = crypto.decrypt(encrypted_account_id)
        raw_status = row["status"]
        try:
            status = StopOrderStatus(raw_status)
        except Exception:
            logger.warning(f"⚠️ Unknown stop order status in DB: {raw_status} (stop_order_id={row['stop_order_id']})")
            status = StopOrderStatus.UNKNOWN
        return StopOrderRecord(
            id=row['id'],
            stop_order_id=row['stop_order_id'],
            position_id=row['position_id'],
            trade_id=row['trade_id'] if 'trade_id' in row.keys() else None,
            order_type=row['order_type'],
            ticker=row['ticker'],
            figi=row['figi'],
            stop_price=row['stop_price'],
            lots_requested=row['lots_requested'],
            created_at=datetime.fromisoformat(row['created_at']),
            last_checked_at=datetime.fromisoformat(row['last_checked_at']) if row['last_checked_at'] else None,
            executed_at=datetime.fromisoformat(row['executed_at']) if row['executed_at'] else None,
            status=status,
            account_id=account_id,
            account_id_short=row['account_id_short'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {}
        )
