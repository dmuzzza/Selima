"""
Проверка сигналов стратегии Dual MA Crossover — ВЫРАВНЕНО С DASHBOARD.

Отличия от исходной версии dual_ma_signals.py:
1. Убран LIMIT в SQL — теперь загружается ВСЯ история из БД для figi+timeframe,
   как это делает HistoryManager.get_candles() (используется и dashboard, и
   продакшн-сканером).
2. Фильтр свечей теперь ТОЧНО как в dashboard/data.py::load_candles():
   candles = [c for c in candles if c.volume and c.volume > 0]
   (НЕ is_complete, как в scheduler_notifier.py — это осознанный выбор,
   раз эталоном выбран dashboard).
3. Добавлена опциональная фильтрация по датам (--start-date/--end-date),
   применяемая ПОСЛЕ загрузки полной истории — как в dashboard. Если даты
   не заданы, индикаторы считаются на всей истории (это важно для
   консистентности EMA200/SMA50/adaptive MA — см. предыдущий разбор).

Запуск:
    python dual_ma_signals_dashboard_aligned.py --ticker MGNT --timeframe 4h
    python dual_ma_signals_dashboard_aligned.py --ticker MGNT --timeframe 4h --start-date 2025-01-01
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
from dotenv import load_dotenv

from src.market_data.history_manager import HistoryManager, MarketData
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.brokers.broker_factory import BrokerFactory
from src.strategies.dual_ma_crossover import DualMACrossoverStrategy

TIMEFRAME_MAP = {
    "30m": Timeframe.M30,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
}


def load_candles_like_dashboard(
    history: HistoryManager,
    figi: str,
    timeframe_str: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Candle]:
    """
    Точная копия dashboard/data.py::load_candles().

    Загружает ВСЮ историю из БД (без LIMIT), фильтрует по volume > 0,
    затем опционально обрезает по датам — ПОСЛЕ загрузки полного ряда,
    чтобы индикаторы (EMA200/SMA50/adaptive) считались корректно.
    """
    timeframe = TIMEFRAME_MAP.get(timeframe_str, Timeframe.H1)

    # Вся история — как в HistoryManager.get_candles(), без LIMIT
    candles = history.get_candles(figi, timeframe)

    # Фильтр: только непустые свечи (ТОЧНО как в dashboard, не is_complete!)
    candles = [c for c in candles if c.volume and c.volume > 0]

    if start_date:
        sd = datetime.strptime(start_date.split("T")[0], "%Y-%m-%d")
        candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]

    if end_date:
        ed = datetime.strptime(end_date.split("T")[0], "%Y-%m-%d")
        candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed + timedelta(days=1)]

    return candles


def resolve_figi_by_ticker(history: HistoryManager, ticker: str) -> Optional[str]:
    """Найти FIGI по тикеру через таблицу market_data (как маппинг в dashboard.data.init)."""
    session = history.Session()
    try:
        row = session.query(MarketData.figi).filter(MarketData.ticker == ticker).first()
        return row[0] if row else None
    finally:
        session.close()


def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
    """Преобразовать список Candle в DataFrame для вывода."""
    return pd.DataFrame(
        [
            {
                "open_time": c.open_time,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "is_complete": c.is_complete,
            }
            for c in candles
        ]
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Сигналы Dual MA Crossover на ВСЕЙ истории — выровнено с dashboard"
    )
    parser.add_argument("--ticker", default="POSI", help="Тикер инструмента")
    parser.add_argument("--timeframe", default="4h", help="Таймфрейм (30m/1h/4h/1d)")
    parser.add_argument("--start-date", default=None, help="Начальная дата YYYY-MM-DD (опционально)")
    parser.add_argument("--end-date", default=None, help="Конечная дата YYYY-MM-DD (опционально)")
    args = parser.parse_args()

    load_dotenv()

    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise ValueError("❌ TINKOFF_API_TOKEN not found in .env")

    broker = BrokerFactory.create("sdk", token)
    history = HistoryManager(broker)

    ticker = args.ticker.upper()
    figi = resolve_figi_by_ticker(history, ticker)
    if not figi:
        print(f"Нет данных для ticker={ticker} в таблице market_data.")
        return

    candles = load_candles_like_dashboard(
        history, figi, args.timeframe, args.start_date, args.end_date
    )

    if not candles:
        print(f"Нет свечей для {ticker} / {args.timeframe} после фильтрации.")
        return

    strategy = DualMACrossoverStrategy(timeframe=args.timeframe, config=None, debug=False)
    signals = strategy.detect_signals(candles)

    signal_flags = [0] * len(candles)
    for s in signals:
        if 0 <= s.candle_index < len(signal_flags):
            signal_flags[s.candle_index] = 1

    df = candles_to_dataframe(candles)
    df["сигнал"] = signal_flags

    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)

    print(f"\nСтратегия: {strategy.name} | {strategy.description}")
    print(f"Инструмент: {ticker} | Таймфрейм: {args.timeframe}")
    print(f"Свечей (после фильтра volume>0): {len(df)} | Сигналов: {df['сигнал'].sum()}\n")
    print(df.tail(50))  # последние 50 строк для читаемости, при необходимости убери .tail()


if __name__ == "__main__":
    main()
