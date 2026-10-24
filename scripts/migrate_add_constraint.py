"""
Миграция: Добавить столбцы is_complete и candle_source (v2 - правильная версия)
"""

import sqlite3
import sys
from pathlib import Path

db_path = Path("marketdata.db")

print("=" * 80)
print("MIGRATION: Add Candle Metadata (v2)")
print("=" * 80)

if not db_path.exists():
    print("\n✓ No database to migrate (will be created with new schema)")
    sys.exit(0)

print(f"\n📊 Migrating {db_path}...")

with sqlite3.connect(db_path) as conn:
    # Проверяем текущие столбцы
    cursor = conn.execute("PRAGMA table_info(market_data)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}
    
    print(f"\n📋 Current columns: {len(columns)}")
    
    migrations_applied = []
    
    # ═════════════════════════════════════════════════════════════
    # ДОБАВЛЯЕМ is_complete
    # ═════════════════════════════════════════════════════════════
    
    if 'is_complete' not in columns:
        print("\n➕ Adding column 'is_complete' (INTEGER)...")
        try:
            conn.execute("""
                ALTER TABLE market_data 
                ADD COLUMN is_complete INTEGER NOT NULL DEFAULT 1
            """)
            conn.commit()
            print("✅ Column 'is_complete' added")
            migrations_applied.append("is_complete")
        except sqlite3.Error as e:
            print(f"❌ Failed: {e}")
            sys.exit(1)
    else:
        print(f"\n✓ Column 'is_complete' exists (type: {columns['is_complete']})")
    
    # ═════════════════════════════════════════════════════════════
    # ДОБАВЛЯЕМ candle_source
    # ═════════════════════════════════════════════════════════════
    
    if 'candle_source' not in columns:
        print("\n➕ Adding column 'candle_source' (INTEGER)...")
        try:
            conn.execute("""
                ALTER TABLE market_data 
                ADD COLUMN candle_source INTEGER
            """)
            conn.commit()
            print("✅ Column 'candle_source' added")
            migrations_applied.append("candle_source")
        except sqlite3.Error as e:
            print(f"❌ Failed: {e}")
            sys.exit(1)
    else:
        print(f"\n✓ Column 'candle_source' exists (type: {columns['candle_source']})")
    
    # ═════════════════════════════════════════════════════════════
    # УДАЛЯЕМ СТАРЫЙ candle_source_type (если есть VARCHAR версия)
    # ═════════════════════════════════════════════════════════════
    
    if 'candle_source_type' in columns:
        print("\n⚠️ Found old 'candle_source_type' column (VARCHAR)")
        print("   This will be ignored. Use 'candle_source' (INTEGER) instead.")
    
    # ═════════════════════════════════════════════════════════════
    # ИТОГИ
    # ═════════════════════════════════════════════════════════════
    
    cursor = conn.execute("SELECT COUNT(*) FROM market_data")
    total_rows = cursor.fetchone()[0]
    
    print(f"\n📊 Database stats:")
    print(f"   Total candles: {total_rows:,}")
    
    if migrations_applied:
        print(f"\n✨ Applied migrations: {', '.join(migrations_applied)}")

print("\n" + "=" * 80)
print("✅ Migration complete!")
print("=" * 80)
print("\nValue mappings:")
print("  is_complete: 1=True, 0=False")
print("  candle_source: 0=UNSPECIFIED, 1=EXCHANGE, 2=DEALER_WEEKEND, NULL=unknown")
print("=" * 80)
