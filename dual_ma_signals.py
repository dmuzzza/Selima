"""
Проверка сигналов стратегии Dual MA Crossover на последних N свечах.

Скрипт:
1. Подключается к PostgreSQL (таблица market_data) через те же параметры,
   что и HistoryManager.
2. Берёт последние `limit` свечей для заданного инструмента и таймфрейма.
3. Прогоняет их через DualMACrossoverStrategy.detect_signals() (применяются
   ВСЕ условия стратегии: SMA50 × MA Adaptive пересечение + EMA-фильтр).
4. Выводит датафрейм со всеми столбцами market_data + колонка "сигнал"
   (0 — сигнала нет, 1 — сигнал есть).

Запуск:
    python dual_ma_signals.py
    python dual_ma_signals.py --ticker SBER --timeframe 4h --limit 250
"""

import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.market_data.history_manager import MarketData
from src.market_data.models import Candle
from src.strategies.dual_ma_crossover import DualMACrossoverStrategy


def build_engine():
    """Создаёт SQLAlchemy engine по тем же параметрам, что и HistoryManager."""
    db_url = (
        f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
        f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
        f"?sslmode=require"
    )
    return create_engine(db_url, pool_pre_ping=True)


def load_last_candles(engine, ticker: str, timeframe: str, limit: int):
    """
    Возвращает последние `limit` строк market_data (хронологически) и список Candle.

    Returns:
        (rows, candles) — строки SQLAlchemy и список Candle в том же порядке.
    """
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        rows = (
            session.query(MarketData)
            .filter(
                MarketData.ticker == ticker,
                MarketData.timeframe == timeframe,
            )
            .order_by(MarketData.open_time.desc())
            .limit(limit)
            .all()
        )
        # Возвращаем в хронологическом порядке (от старых к новым)
        rows = list(reversed(rows))
    finally:
        session.close()

    candles = [
        Candle(
            open_time=row.open_time,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=int(row.volume),
            is_complete=bool(row.is_complete),
            candle_source=row.candle_source,
        )
        for row in rows
    ]

    return rows, candles


def rows_to_dataframe(rows) -> pd.DataFrame:
    """Преобразует строки market_data в DataFrame со всеми столбцами таблицы."""
    data = [
        {
            "id": row.id,
            "figi": row.figi,
            "ticker": row.ticker,
            "timeframe": row.timeframe,
            "open_time": row.open_time,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": int(row.volume),
            "is_complete": bool(row.is_complete),
            "candle_source": row.candle_source,
        }
        for row in rows
    ]
    return pd.DataFrame(data)


def main():
    # Windows-консоль (cp1251) не умеет печатать спецсимволы (×, ÷, ₽ и т.д.)
    # Принудительно переключаем stdout на UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Сигналы Dual MA Crossover на последних N свечах из PostgreSQL"
    )
    parser.add_argument(
        "--ticker", default="MGNT", help="Тикер инструмента (по умолчанию SBER)"
    )
    parser.add_argument(
        "--timeframe", default="4h", help="Таймфрейм (по умолчанию 4h)"
    )
    parser.add_argument(
        "--limit", type=int, default=3000, help="Количество последних свечей (по умолчанию 250)"
    )
    args = parser.parse_args()

    load_dotenv()

    engine = build_engine()
    ticker = args.ticker.upper()
    rows, candles = load_last_candles(engine, ticker, args.timeframe, args.limit)

    if not candles:
        print(
            f"Нет данных для ticker={ticker}, timeframe={args.timeframe} "
            f"в таблице market_data."
        )
        return

    # Применяем ВСЕ условия стратегии Dual MA Crossover
    strategy = DualMACrossoverStrategy(timeframe=args.timeframe, config=None, debug=False)
    signals = strategy.detect_signals(candles)

    # Маппим сигналы на индексы свечей (0 — нет, 1 — есть)
    signal_flags = [0] * len(candles)
    for s in signals:
        if 0 <= s.candle_index < len(signal_flags):
            signal_flags[s.candle_index] = 1

    # Датафрейм со всеми столбцами market_data + колонка "сигнал"
    df = rows_to_dataframe(rows)
    df["сигнал"] = signal_flags

    # Настройки отображения — печатаем весь датафрейм
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 30)

    print(f"\nСтратегия: {strategy.name} | {strategy.description}")
    print(f"Инструмент: {ticker} | Таймфрейм: {args.timeframe}")
    print(f"Свечей: {len(df)} | Сигналов: {df['сигнал'].sum()}\n")
    print(df)


if __name__ == "__main__":
    main()
