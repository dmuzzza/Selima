"""
Миграция: Добавление position_id и других полей
"""

import sqlite3
import os

DB_PATH = "data/live_trades.db"

def migrate():
    """Выполнить миграцию БД."""
    
    if not os.path.exists(DB_PATH):
        print(f"✓ Database not found, will be created automatically")
        return
    
    print("="*60)
    print("🔄 MIGRATION: Adding position_id and account_id_short")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Проверяем, нужна ли миграция
        cursor.execute("PRAGMA table_info(live_trades)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'position_id' in columns:
            print("✓ Migration already applied")
            return
        
        # Добавляем новые колонки
        print("\n1. Adding new columns to live_trades...")
        
        try:
            conn.execute("ALTER TABLE live_trades ADD COLUMN position_id TEXT")
            print("   ✓ position_id added")
        except sqlite3.OperationalError:
            print("   ⚠️  position_id already exists")
        
        try:
            conn.execute("ALTER TABLE live_trades ADD COLUMN account_id_short TEXT")
            print("   ✓ account_id_short added")
        except sqlite3.OperationalError:
            print("   ⚠️  account_id_short already exists")
        
        try:
            conn.execute("ALTER TABLE live_trades ADD COLUMN orders_collected INTEGER DEFAULT 0")
            print("   ✓ orders_collected added")
        except sqlite3.OperationalError:
            print("   ⚠️  orders_collected already exists")
        
        # Обновляем существующие записи
        print("\n2. Updating existing records...")
        
        conn.execute("""
            UPDATE live_trades 
            SET account_id_short = substr(account_id, -4)
            WHERE account_id_short IS NULL
        """)
        
        print("   ✓ account_id_short populated")
        
        # Создаём индексы
        print("\n3. Creating indexes...")
        
        try:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_live_trades_position_id 
                ON live_trades(position_id)
            """)
            print("   ✓ Index on position_id created")
        except sqlite3.OperationalError:
            print("   ⚠️  Index already exists")
        
        conn.commit()
    
    print("\n" + "="*60)
    print("✅ MIGRATION COMPLETED")
    print("="*60)

if __name__ == "__main__":
    migrate()
