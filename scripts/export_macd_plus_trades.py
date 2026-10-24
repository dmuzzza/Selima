"""
Export MACD Plus trades to Excel.

Скрипт запускает стратегию macd_plus на всех доступных инструментах
для указанного таймфрейма и сохраняет сделки в Excel-файл.

Помимо базовых полей (дата, тикер, таймфрейм, направление, цены, дни, PnL),
добавляются значения индикаторов на момент входа:
- MA200
- MACD line
- MACD signal
- MACD histogram
- RSI14

Использование:
    python -m scripts.export_macd_plus_trades
    python -m scripts.export_macd_plus_trades --timeframe 4h --output macd_plus_trades.xlsx
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.models import Candle
from src.strategies.macd_plus.strategy import MACDPlusStrategy

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# РАСЧЁТ RSI
# ═══════════════════════════════════════════════════════════════════════════

def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Рассчитать RSI для ряда цен закрытия.

    Args:
        closes: Список цен закрытия
        period: Период RSI

    Returns:
        List[float | None]: Массив RSI (None если недостаточно данных)
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    rsi_series: List[Optional[float]] = [None] * len(closes)
    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_series[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_series[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period

        if avg_loss == 0:
            rsi_series[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_series[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi_series


# ═══════════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════

def load_data(history: HistoryManager, timeframe: str) -> List[Dict[str, Any]]:
    """
    Загрузить доступные FIGI для таймфрейма и построить маппинг тикеров.

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

def run_export(timeframe: str, output_path: str, start_date: Optional[str], end_date: Optional[str]) -> None:
    """
    Запустить экспорт сделок macd_plus в Excel.
    """
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise RuntimeError("TINKOFF_API_TOKEN not found in environment")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    logger.info("Initializing broker and history manager...")
    broker = BrokerFactory.create("sdk", token)
    history = HistoryManager(broker)

    strategy = MACDPlusStrategy(timeframe=timeframe, debug=False)

    logger.info(f"Loading instruments for timeframe {timeframe}...")
    instruments = load_data(history, timeframe)
    logger.info(f"Found {len(instruments)} instruments")

    all_rows: List[Dict[str, Any]] = []

    for instrument in instruments:
        figi = instrument["figi"]
        ticker = instrument["ticker"]

        try:
            candles = history.get_candles(figi, history._resolve_timeframe(timeframe))
            candles = [c for c in candles if c.volume and c.volume > 0]

            if not candles:
                logger.warning(f"Skipping {ticker}: no candles")
                continue

            # Фильтрация по датам
            if start_date:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]
            if end_date:
                ed = datetime.strptime(end_date, "%Y-%m-%d")
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed]

            if len(candles) < 200:
                logger.warning(f"Skipping {ticker}: only {len(candles)} candles")
                continue

            result = strategy.run(candles)
            trades = result["trades"]

            if not trades:
                logger.info(f"{ticker}: no trades")
                continue

            # Рассчитываем индикаторы для всех свечей
            ma200_series = strategy._calculate_sma_series(candles, strategy.trend_ma_period)
            macd_line, signal_line, histogram = strategy._calculate_macd_series(candles)
            closes = [c.close for c in candles]
            rsi_series = calculate_rsi(closes, period=14)

            for trade in trades:
                entry_idx = trade.entry_index
                if entry_idx is None or entry_idx >= len(candles):
                    continue

                days_held = 0
                if trade.exit_time and trade.entry_time:
                    days_held = max(1, (trade.exit_time - trade.entry_time).days)

                row = {
                    "Дата": trade.entry_time.strftime("%Y-%m-%d %H:%M") if trade.entry_time else "-",
                    "Тикер": ticker,
                    "ТФ": timeframe,
                    "Направление": "Long" if trade.is_long() else "Short" if trade.is_short() else "-",
                    "Цена входа": trade.entry_price,
                    "Цена выхода": trade.exit_price,
                    "Кол-во дней в сделке": days_held,
                    "Результат сделки": trade.pnl_net,
                    "Результат сделки %": trade.pnl_percent,
                    "MA200": ma200_series[entry_idx],
                    "MACD": macd_line[entry_idx],
                    "MACD Signal": signal_line[entry_idx],
                    "MACD Histogram": histogram[entry_idx],
                    "RSI14": rsi_series[entry_idx],
                }
                all_rows.append(row)

            logger.info(f"{ticker}: {len(trades)} trades processed")

        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
            continue

    if not all_rows:
        logger.warning("No trades found")
        return

    df = pd.DataFrame(all_rows)

    # Сортируем по дате входа
    df["_sort_date"] = pd.to_datetime(df["Дата"], errors="coerce")
    df = df.sort_values("_sort_date").drop(columns=["_sort_date"])

    # Форматируем числовые колонки
    numeric_cols = [
        "Цена входа", "Цена выхода", "Результат сделки",
        "Результат сделки %", "MA200", "MACD", "MACD Signal",
        "MACD Histogram", "RSI14",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: round(x, 4) if pd.notna(x) else x)

    df.to_excel(output_path, index=False, engine="openpyxl")
    logger.info(f"✓ Exported {len(df)} trades to {output_path}")


# ═══════════════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Export MACD Plus trades to Excel")
    parser.add_argument("--timeframe", type=str, default="4h", help="Timeframe: 30m, 1h, 4h, 1d")
    parser.add_argument("--output", type=str, default="macd_plus_trades.xlsx", help="Output Excel file path")
    parser.add_argument("--start-date", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None, help="End date YYYY-MM-DD")

    args = parser.parse_args()
    run_export(args.timeframe, args.output, args.start_date, args.end_date)


if __name__ == "__main__":
    main()
