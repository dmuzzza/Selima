"""
Проверить наличие UNIQUE constraint в БД.
"""

import sqlite3
import sys
from pathlib import Path

db_path = Path("marketdata.db")

print("=" * 80)
print("UNIQUE CONSTRAINT CHECKER")
print("=" * 80)

if not db_path.exists():
    print("\n✓ Database doesn't exist yet")
    print("  It will be created with proper constraints on first run")
    sys.exit(0)

print(f"\n📊 Checking {db_path}...")

with sqlite3.connect(db_path) as conn:
    # Получаем информацию о таблице
    cursor = conn.execute("PRAGMA table_info(market_data)")
    columns = cursor.fetchall()
    
    print(f"\n📋 Table 'market_data' columns: {len(columns)}")
    for col in columns:
        print(f"   - {col[1]} ({col[2]})")
    
    # Получаем список индексов
    cursor = conn.execute("""
        SELECT name, sql 
        FROM sqlite_master 
        WHERE type='index' AND tbl_name='market_data'
    """)
    
    indexes = cursor.fetchall()
    
    print(f"\n📋 Indexes: {len(indexes)}")
    
    constraint_found = False
    for name, sql in indexes:
        print(f"   - {name}")
        if sql:
            print(f"     {sql}")
        if name == 'uq_market_data':
            constraint_found = True
    
    # Проверяем дубликаты
    cursor = conn.execute("""
        SELECT figi, timeframe, open_time, COUNT(*) as cnt
        FROM market_data
        GROUP BY figi, timeframe, open_time
        HAVING cnt > 1
        LIMIT 5
    """)
    
    duplicates = cursor.fetchall()
    
    print("\n" + "=" * 80)
    
    if constraint_found:
        print("✅ UNIQUE constraint EXISTS")
        print("   Duplicates are impossible!")
    else:
        print("⚠️ UNIQUE constraint NOT FOUND")
        print("   Duplicates are possible!")
        print("\n   Run migration:")
        print("   python scripts/migrate_add_constraint.py")
    
    if duplicates:
        print(f"\n⚠️ Found {len(duplicates)} duplicate entries (showing first 5):")
        for row in duplicates:
            print(f"   {row[0]} | {row[1]} | {row[2]} | Count: {row[3]}")
        print("\n   Clean them with:")
        print("   python -c 'from src.market_data.history_manager import *; ...")
    else:
        print("\n✓ No duplicates found in database")
    
    print("=" * 80)
