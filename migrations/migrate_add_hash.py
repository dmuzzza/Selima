"""
Migration: Add account_id_hash field

Добавляет поле account_id_hash и заполняет его для существующих записей.
"""

import sqlite3
from src.utils.crypto import get_crypto

DB_PATH = "data/live_trades.db"

def migrate():
    crypto = get_crypto()
    
    with sqlite3.connect(DB_PATH) as conn:
        # 1. Добавить поле в live_trades
        try:
            conn.execute("ALTER TABLE live_trades ADD COLUMN account_id_hash TEXT")
            print("✓ Added account_id_hash to live_trades")
        except sqlite3.OperationalError:
            print("✓ account_id_hash already exists in live_trades")
        
        # 2. Заполнить хеши для существующих записей
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, account_id FROM live_trades WHERE account_id_hash IS NULL").fetchall()
        
        for row in rows:
            # Расшифровываем account_id
            decrypted = crypto.decrypt(row['account_id'])
            # Хешируем
            hashed = crypto.hash_account_id(decrypted)
            # Обновляем
            conn.execute("UPDATE live_trades SET account_id_hash = ? WHERE id = ?", (hashed, row['id']))
        
        print(f"✓ Updated {len(rows)} records in live_trades")
        
        # 3. Создать индекс
        try:
            conn.execute("""
                CREATE INDEX idx_live_trades_hash_ticker 
                ON live_trades(account_id_hash, ticker, status)
            """)
            print("✓ Created index on live_trades")
        except sqlite3.OperationalError:
            print("✓ Index already exists")
        
        # 4. То же для stop_orders
        try:
            conn.execute("ALTER TABLE stop_orders ADD COLUMN account_id_hash TEXT")
            print("✓ Added account_id_hash to stop_orders")
        except sqlite3.OperationalError:
            print("✓ account_id_hash already exists in stop_orders")
        
        rows = conn.execute("SELECT id, account_id FROM stop_orders WHERE account_id_hash IS NULL").fetchall()
        
        for row in rows:
            decrypted = crypto.decrypt(row['account_id'])
            hashed = crypto.hash_account_id(decrypted)
            conn.execute("UPDATE stop_orders SET account_id_hash = ? WHERE id = ?", (hashed, row['id']))
        
        print(f"✓ Updated {len(rows)} records in stop_orders")
        
        try:
            conn.execute("""
                CREATE INDEX idx_stop_orders_hash 
                ON stop_orders(account_id_hash, status)
            """)
            print("✓ Created index on stop_orders")
        except sqlite3.OperationalError:
            print("✓ Index already exists")
        
        conn.commit()
        print("\n✅ Migration completed!")

if __name__ == "__main__":
    migrate()
