# migrations/migrate_add_lot_size.py
import sqlite3
conn = sqlite3.connect("data/live_trades.db")
try:
    conn.execute("ALTER TABLE live_trades ADD COLUMN lot_size INTEGER DEFAULT 1")
    conn.commit()
    print("✓ lot_size column added")
except Exception as e:
    print(f"Already exists or error: {e}")
finally:
    conn.close()
