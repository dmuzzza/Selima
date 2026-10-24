"""
Export Dual MA Crossover trades to CSV.

Скрипт запускает стратегию dual_ma_crossover на всех доступных инструментах
и сохраняет все закрытые сделки в CSV-файл с полной информацией об индикаторах.

Использование:
    # Один таймфрейм
    python -m scripts.export_dual_ma_trades
    python -m scripts.export_dual_ma_trades --timeframe 1h

    # Все таймфреймы (30m, 1h, 4h, 1d) в один CSV
    python -m scripts.export_dual_ma_trades --all

    # Все таймфреймы + фильтр по датам
    python -m scripts.export_dual_ma_trades --all --start-date 2023-01-01
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz
from dotenv import load_dotenv

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.models import Candle
from src.strategies.dual_ma_crossover.strategy import DualMACrossoverStrategy

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════

def load_instruments(history: HistoryManager, timeframe: str) -> List[Dict[str, str]]:
    """
    Загрузить доступные FIGI для таймфрейма с маппингом тикеров.

    Returns:
        List[dict]: [{"figi": ..., "ticker": ...}, ...]
    """
    from src.market_data.history_manager import MarketData

    combinations = history.get_available_combinations()
    figis = [figi for figi, tfs in combinations.items() if timeframe in tfs]

    session = history.Session()
    try:
        rows = session.query(MarketData.figi, MarketData.ticker).distinct().all()
        figi_to_ticker = {figi: ticker for figi, ticker in rows}
    finally:
        session.close()

    instruments = []
    for figi in figis:
        ticker = figi_to_ticker.get(figi, figi)
        instruments.append({"figi": figi, "ticker": ticker})

    return instruments


# ═══════════════════════════════════════════════════════════════════════════
# ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════════════

def _collect_for_timeframe(
    history: HistoryManager,
    timeframe: str,
    start_date: str | None,
    end_date: str | None,
) -> List[Dict[str, Any]]:
    """
    Собрать сделки для одного таймфрейма.

    Returns:
        Список строк для CSV.
    """
    from src.market_data.timeframe import Timeframe

    tf_map = {"30m": Timeframe.M30, "1h": Timeframe.H1, "4h": Timeframe.H4, "1d": Timeframe.D1}

    strategy = DualMACrossoverStrategy(timeframe=timeframe, debug=False)

    instruments = load_instruments(history, timeframe)
    if not instruments:
        logger.warning(f"  Нет инструментов для {timeframe}")
        return []

    logger.info(f"  Загружено {len(instruments)} инструментов для {timeframe}")
    rows: List[Dict[str, Any]] = []
    tf_enum = tf_map[timeframe]

    for instrument in instruments:
        figi = instrument["figi"]
        ticker = instrument["ticker"]

        try:
            candles = history.get_candles(figi, tf_enum)
            candles = [c for c in candles if c.volume and c.volume > 0]

            if not candles:
                continue

            # Фильтрация по датам
            if start_date:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                msk_tz = pytz.timezone("Europe/Moscow")
                sd = msk_tz.localize(sd) if sd.tzinfo is None else sd
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd.replace(tzinfo=None)]
            if end_date:
                ed = datetime.strptime(end_date, "%Y-%m-%d")
                msk_tz = pytz.timezone("Europe/Moscow")
                ed = msk_tz.localize(ed) if ed.tzinfo is None else ed
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed.replace(tzinfo=None)]

            min_required = max(strategy.sma_period, strategy.adaptive_period, strategy.ema_period) + 1
            if len(candles) < min_required:
                continue

            result = strategy.run(candles)
            trades = result["trades"]
            closed_trades = [t for t in trades if not t.is_open()]

            if not closed_trades:
                continue

            # Индикаторы
            sma_series = strategy.calculate_sma_series(candles)
            ma_adaptive_series = strategy.calculate_ma_adaptive_series(candles)
            ema_series = strategy.calculate_ema_series(candles)

            for trade in closed_trades:
                entry_idx = trade.entry_index
                if entry_idx is None or entry_idx >= len(candles):
                    continue

                entry_candle = candles[entry_idx]

                direction = "Long" if trade.is_long() else "Short" if trade.is_short() else "-"
                exit_type = trade.exit_type or "-"

                row = {
                    "Тикер": ticker,
                    "Дата сделки": trade.entry_time.strftime("%Y-%m-%d %H:%M") if trade.entry_time else "-",
                    "ТФ": timeframe,
                    "Направление": direction,
                    "SMA50": round(sma_series[entry_idx], 4) if sma_series[entry_idx] is not None else None,
                    "Adaptive MA14": round(ma_adaptive_series[entry_idx], 4) if ma_adaptive_series[entry_idx] is not None else None,
                    "EMA200": round(ema_series[entry_idx], 4) if ema_series[entry_idx] is not None else None,
                    "Цена входа": trade.entry_price,
                    "Цена выхода": trade.exit_price,
                    "Тип выхода": exit_type,
                    "Объём в сигнале": entry_candle.volume,
                    "Комиссия (₽)": round(trade.commission_total, 4) if trade.commission_total else 0.0,
                    "PnL (₽)": round(trade.pnl_net, 2) if trade.pnl_net else 0.0,
                    "PnL (%)": round(trade.pnl_percent, 4) if trade.pnl_percent else 0.0,
                }
                rows.append(row)

            logger.debug(f"  {ticker}: {len(closed_trades)} сделок")

        except Exception as e:
            logger.error(f"  Ошибка {ticker}: {e}")
            continue

    logger.info(f"  Собрано {len(rows)} сделок для {timeframe}")
    return rows


def run_export(
    timeframe: str,
    output_path: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """
    Запустить экспорт сделок dual_ma_crossover в CSV.

    Args:
        timeframe: Таймфрейм ("1h", "4h", "1d", "30m")
        output_path: Путь к выходному CSV-файлу
        start_date: Начальная дата YYYY-MM-DD (опционально)
        end_date: Конечная дата YYYY-MM-DD (опционально)
    """
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise RuntimeError("TINKOFF_API_TOKEN not found in environment")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("Инициализация broker и history manager...")
    broker = BrokerFactory.create("sdk", token)
    history = HistoryManager(broker)

    print(f"\n  ========================================")
    print(f"  Экспорт сделок dual_ma_crossover")
    print(f"  Таймфреймы: {timeframe}")
    print(f"  ========================================\n")

    all_rows: List[Dict[str, Any]] = []

    if timeframe == "__ALL__":
        # Собираем все ТФ, которые есть в БД
        all_timeframes = ['4h']#["30m", "1h", "4h", "1d"]
        for tf in all_timeframes:
            rows = _collect_for_timeframe(history, tf, start_date, end_date)
            all_rows.extend(rows)
    else:
        all_rows = _collect_for_timeframe(history, timeframe, start_date, end_date)

    if not all_rows:
        logger.warning("Сделок не найдено ни по одному таймфрейму")
        return

    # Сортируем по дате входа
    all_rows.sort(key=lambda r: r["Дата сделки"])

    # Пишем CSV
    fieldnames = [
        "Тикер",
        "Дата сделки",
        "ТФ",
        "Направление",
        "SMA50",
        "Adaptive MA14",
        "EMA200",
        "Цена входа",
        "Цена выхода",
        "Тип выхода",
        "Объём в сигнале",
        "Комиссия (₽)",
        "PnL (₽)",
        "PnL (%)",
    ]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    # Итоговая статистика
    total_pnl = sum(r["PnL (₽)"] for r in all_rows)
    total_commission = sum(r["Комиссия (₽)"] for r in all_rows)
    total_trades = len(all_rows)
    winning = sum(1 for r in all_rows if r["PnL (₽)"] > 0)
    losing = sum(1 for r in all_rows if r["PnL (₽)"] <= 0)

    print("\n" + "=" * 70)
    print(f"  ЭКСПОРТ ЗАВЕРШЁН")
    print("=" * 70)
    print(f"  Файл:             {output_path}")
    print(f"  Всего сделок:     {total_trades}")
    print(f"  Прибыльных:       {winning}")
    print(f"  Убыточных:        {losing}")
    print(f"  Win Rate:         {winning / total_trades * 100:.1f}%" if total_trades > 0 else "  Win Rate:         N/A")
    print(f"  Суммарный PnL:    {total_pnl:+,.2f} ₽")
    print(f"  Сумм. комиссия:   {total_commission:,.2f} ₽")

    # Статистика по таймфреймам
    if timeframe == "4h":
        print("-" * 70)
        print(f"  ПО ТАЙМФРЕЙМАМ:")
        tfs_in_data = sorted(set(r["ТФ"] for r in all_rows))
        for tf in tfs_in_data:
            tf_rows = [r for r in all_rows if r["ТФ"] == tf]
            tf_pnl = sum(r["PnL (₽)"] for r in tf_rows)
            tf_w = sum(1 for r in tf_rows if r["PnL (₽)"] > 0)
            print(f"    {tf}: {len(tf_rows)} сделок | Win rate {tf_w / len(tf_rows) * 100:.1f}% | PnL {tf_pnl:+,.2f} ₽")

    print("=" * 70)

    logger.info(f"Экспортировано {total_trades} сделок в {output_path}")


# ═══════════════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Export Dual MA Crossover trades to CSV")
    parser.add_argument("--timeframe", type=str, default="4h",
                        help="Таймфрейм: 30m, 1h, 4h, 1d")
    parser.add_argument("--all", action="store_true",
                        help="Экспортировать ВСЕ таймфреймы (30m, 1h, 4h, 1d) в один файл")
    parser.add_argument("--output", type=str, default="dual_ma_trades.csv",
                        help="Путь к выходному CSV файлу")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Начальная дата YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Конечная дата YYYY-MM-DD")

    args = parser.parse_args()

    if args.all:
        timeframe = "__ALL__"
        if args.timeframe != "1d":
            print(f"  ПРЕДУПРЕЖДЕНИЕ: флаг --all игнорирует --timeframe {args.timeframe}")
    else:
        timeframe = args.timeframe

    run_export(timeframe, args.output, args.start_date, args.end_date)


if __name__ == "__main__":
    main()