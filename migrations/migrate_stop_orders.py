import sqlite3
from pathlib import Path

db_path = Path("data/live_trades.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("🔧 Добавление колонки trade_id в stop_orders...")
print("="*80)

try:
    cursor.execute("ALTER TABLE stop_orders ADD COLUMN trade_id TEXT")
    print("✅ Колонка trade_id добавлена")
except sqlite3.OperationalError as e:
    print(f"⚠️ {e}")

conn.commit()

# Заполняем trade_id для существующих записей (если есть)
print("\n🔄 Заполнение trade_id для существующих ордеров...")
cursor.execute("""
    UPDATE stop_orders 
    SET trade_id = (
        SELECT trade_id 
        FROM live_trades 
        WHERE live_trades.position_id = stop_orders.position_id
        LIMIT 1
    )
    WHERE trade_id IS NULL
""")
updated = cursor.rowcount
print(f"✅ Обновлено записей: {updated}")

conn.commit()

# Проверка
print("\n📊 Новая структура:")
cursor.execute("PRAGMA table_info(stop_orders)")
columns = [col[1] for col in cursor.fetchall()]
for col in columns:
    print(f"  - {col}")

if 'trade_id' in columns:
    print("\n🎉 Миграция успешна!")
else:
    print("\n❌ Что-то пошло не так...")

conn.close()
