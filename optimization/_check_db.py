"""Вспомогательный скрипт для проверки доступных данных в БД."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = (
    f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
    f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
    f"?sslmode=require"
)

engine = create_engine(db_url, pool_pre_ping=True)

with engine.connect() as conn:
    rows = conn.execute(
        text("SELECT DISTINCT figi, ticker, timeframe FROM market_data ORDER BY figi, timeframe")
    ).fetchall()
    print("Total combinations:", len(rows))
    for r in rows:
        print(r)
