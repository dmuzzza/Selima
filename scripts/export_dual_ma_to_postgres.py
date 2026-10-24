"""
Export Dual MA Crossover backtest results to PostgreSQL.

Скрипт прогоняет стратегию dual_ma_crossover на ВСЕЙ истории (по умолчанию 4h)
по всем инструментам из таблицы market_data и записывает результаты в PostgreSQL.

При каждом запуске обе таблицы ПОЛНОСТЬЮ ПЕРЕЗАПИСЫВАЮТСЯ (TRUNCATE):

    1. strategy_backtest_summary — сводка по тикерам (аналог сводной таблицы дашборда)
    2. strategy_backtest_trades  — все сделки (аналог таблицы "📋 Trades" дашборда)

ВАЖНО: в таблицы попадают ТОЛЬКО тикеры с ПОЛОЖИТЕЛЬНЫМ суммарным PnL
(Total PnL > 0) за период.

ПО УМОЛЧАНИЮ применяется фильтр дат как в дашборде (start = 2022-01-01),
иначе цифры будут отличаться от сводной таблицы дашборда.

Использование:
    python -m scripts.export_dual_ma_to_postgres
    python -m scripts.export_dual_ma_to_postgres --timeframe 4h
    python -m scripts.export_dual_ma_to_postgres --start-date 2023-01-01
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.market_data.history_manager import MarketData
from src.strategies.dual_ma_crossover.strategy import DualMACrossoverStrategy

logger = logging.getLogger(__name__)

STRATEGY_NAME = "dual_ma_crossover"

# Только 4h — по требованию (в конфиге стратегии TP/SL заданы только для 4h)
DEFAULT_TIMEFRAME = "4h"
TF_MAP = {
    "30m": Timeframe.M30,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
}

# Фильтр дат как в дашборде (сводная таблица → DatePickerRange со стартом 2022-01-01).
# Без этого на "всей истории" (LKOH с 2021-02) цифры отличались бы от дашборда.
DEFAULT_START_DATE = "2022-01-01"


# ═══════════════════════════════════════════════════════════════════════════
# 1. DDL — СОЗДАНИЕ ТАБЛИЦ
# ═══════════════════════════════════════════════════════════════════════════

# Сводная таблица по тикерам (как в дашборде)
DDL_SUMMARY = """
CREATE TABLE IF NOT EXISTS strategy_backtest_summary (
    id                     BIGSERIAL PRIMARY KEY,
    strategy_name          VARCHAR(50)   NOT NULL,
    timeframe              VARCHAR(10)   NOT NULL,
    ticker                 VARCHAR(50)   NOT NULL,
    figi                   VARCHAR(50)   NOT NULL,

    total_trades           INTEGER       NOT NULL DEFAULT 0,
    closed_trades          INTEGER       NOT NULL DEFAULT 0,
    winning                INTEGER       NOT NULL DEFAULT 0,
    losing                 INTEGER       NOT NULL DEFAULT 0,
    win_rate               NUMERIC(10,4) NOT NULL DEFAULT 0,

    avg_win                NUMERIC(16,2) NOT NULL DEFAULT 0,   -- Avg Win (₽)
    avg_loss               NUMERIC(16,2) NOT NULL DEFAULT 0,   -- Avg Loss (₽)
    avg_mfe                NUMERIC(12,4) NOT NULL DEFAULT 0,   -- Avg TP (%)
    avg_mae                NUMERIC(12,4) NOT NULL DEFAULT 0,   -- Avg SL (%)
    avg_hold_days          NUMERIC(10,4) NOT NULL DEFAULT 0,

    median_long_up_move    NUMERIC(12,4) NOT NULL DEFAULT 0,
    median_short_down_move NUMERIC(12,4) NOT NULL DEFAULT 0,

    total_pnl              NUMERIC(16,2) NOT NULL DEFAULT 0,   -- Total PnL (₽)
    total_pnl_percent      NUMERIC(12,4) NOT NULL DEFAULT 0,   -- Total PnL (%)
    final_balance          NUMERIC(16,2) NOT NULL DEFAULT 0,   -- Final Balance (₽)

    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),

    UNIQUE (strategy_name, timeframe, ticker)
);
CREATE INDEX IF NOT EXISTS idx_bt_summary_pnl
    ON strategy_backtest_summary (total_pnl DESC);
"""

# Таблица сделок (как таблица "📋 Trades" в дашборде)
DDL_TRADES = """
CREATE TABLE IF NOT EXISTS strategy_backtest_trades (
    id            BIGSERIAL PRIMARY KEY,
    strategy_name VARCHAR(50)  NOT NULL,
    timeframe     VARCHAR(10)  NOT NULL,
    ticker        VARCHAR(50)  NOT NULL,
    figi          VARCHAR(50)  NOT NULL,

    signal_date   TIMESTAMPTZ  NOT NULL,        -- Signal Date
    direction     VARCHAR(6)   NOT NULL,        -- Long / Short
    pattern_name  VARCHAR(50),                  -- Pattern
    entry_price   NUMERIC(14,4) NOT NULL,       -- Entry Price
    exit_price    NUMERIC(14,4),                -- Exit Price (NULL = OPEN)
    days_held     INTEGER      NOT NULL DEFAULT 0,   -- Days Held

    pnl_net       NUMERIC(16,2) NOT NULL DEFAULT 0,  -- Result (₽)
    pnl_percent   NUMERIC(12,4) NOT NULL DEFAULT 0,  -- Result (%)

    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bt_trades_ticker_dt
    ON strategy_backtest_trades (ticker, signal_date);
"""

INSERT_SUMMARY = text("""
    INSERT INTO strategy_backtest_summary
    (strategy_name, timeframe, ticker, figi,
     total_trades, closed_trades, winning, losing, win_rate,
     avg_win, avg_loss, avg_mfe, avg_mae, avg_hold_days,
     median_long_up_move, median_short_down_move,
     total_pnl, total_pnl_percent, final_balance)
    VALUES
    (:strategy_name, :timeframe, :ticker, :figi,
     :total_trades, :closed_trades, :winning, :losing, :win_rate,
     :avg_win, :avg_loss, :avg_mfe, :avg_mae, :avg_hold_days,
     :median_long_up_move, :median_short_down_move,
     :total_pnl, :total_pnl_percent, :final_balance)
""")

INSERT_TRADE = text("""
    INSERT INTO strategy_backtest_trades
    (strategy_name, timeframe, ticker, figi, signal_date, direction, pattern_name,
     entry_price, exit_price, days_held, pnl_net, pnl_percent)
    VALUES
    (:strategy_name, :timeframe, :ticker, :figi, :signal_date, :direction, :pattern_name,
     :entry_price, :exit_price, :days_held, :pnl_net, :pnl_percent)
""")


# ═══════════════════════════════════════════════════════════════════════════
# 2. ПОДКЛЮЧЕНИЕ К БАЗЕ
# ═══════════════════════════════════════════════════════════════════════════

def get_engine():
    """Создать engine на основе .env (как в HistoryManager)."""
    db_url = (
        f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
        f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
        f"?sslmode=require"
    )
    return create_engine(db_url, pool_pre_ping=True)


def create_and_truncate(engine) -> None:
    """
    Создать таблицы (если не существуют) и ПОЛНОСТЬЮ перезаписать их
    (TRUNCATE) — при каждом запуске данные обнуляются.
    """
    with engine.begin() as conn:
        conn.execute(text(DDL_SUMMARY))
        conn.execute(text(DDL_TRADES))
        conn.execute(text("TRUNCATE TABLE strategy_backtest_summary"))
        conn.execute(text("TRUNCATE TABLE strategy_backtest_trades"))
    logger.info("✓ Таблицы созданы/пересозданы (TRUNCATE выполнен)")


# ═══════════════════════════════════════════════════════════════════════════
# 3. ЧТЕНИЕ СВЕЧЕЙ ИЗ POSTGRES
# ═══════════════════════════════════════════════════════════════════════════

def get_candles(session, figi: str, timeframe: Timeframe) -> List[Candle]:
    """Загрузить свечи из market_data для инструмента и таймфрейма."""
    rows = (
        session.query(MarketData)
        .filter(
            MarketData.figi == figi,
            MarketData.timeframe == timeframe.value,
        )
        .order_by(MarketData.open_time)
        .all()
    )
    return [
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


def get_instruments(session, timeframe: Timeframe) -> List[Dict[str, str]]:
    """Уникальные (figi, ticker), у которых есть данные по таймфрейму."""
    rows = (
        session.query(MarketData.ticker, MarketData.figi)
        .filter(MarketData.timeframe == timeframe.value)
        .distinct()
        .all()
    )
    # Сортируем по тикеру для стабильного порядка
    rows = sorted(rows, key=lambda r: r[0])
    return [{"figi": figi, "ticker": ticker} for ticker, figi in rows]


# ═══════════════════════════════════════════════════════════════════════════
# 4. СБОР РЕЗУЛЬТАТОВ (ЛОГИКА КАК В ДАШБОРДЕ)
# ═══════════════════════════════════════════════════════════════════════════

def _calculate_avg_mfe(trades: List[Any]) -> float:
    """Средний MFE (потенциальный TP) по всем сделкам."""
    valid = [t.mfe_percent for t in trades if t.mfe_percent is not None]
    return sum(valid) / len(valid) if valid else 0.0


def _calculate_avg_mae(trades: List[Any]) -> float:
    """Средний MAE (потенциальный SL) по всем сделкам."""
    valid = [t.mae_percent for t in trades if t.mae_percent is not None]
    return sum(valid) / len(valid) if valid else 0.0


def build_trade_rows(
    strategy_name: str,
    timeframe: str,
    ticker: str,
    figi: str,
    trades: List[Any],
) -> List[Dict[str, Any]]:
    """Плоский список сделок — как compute_trades_list в дашборде."""
    rows = []
    for t in trades:
        days_held = 0
        if t.exit_time and t.entry_time:
            days_held = max(1, (t.exit_time - t.entry_time).days)

        direction = "Long" if t.is_long() else "Short" if t.is_short() else "-"

        rows.append({
            "strategy_name": strategy_name,
            "timeframe": timeframe,
            "ticker": ticker,
            "figi": figi,
            "signal_date": t.entry_time,
            "direction": direction,
            "pattern_name": t.pattern_name or "-",
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "days_held": days_held,
            "pnl_net": t.pnl_net or 0.0,
            "pnl_percent": t.pnl_percent or 0.0,
        })
    return rows


def collect_results(session, timeframe: str, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    """
    Прогнать стратегию по всем инструментам с фильтром по датам.

    Фильтрация свечей повторяет dashboard/data.py → load_candles:
        start_date: open_time >= start_date (YYYY-MM-DD)
        end_date:   open_time <  end_date + 1 день (YYYY-MM-DD)

    Returns:
        Список словарей:
            {ticker, figi, total_pnl, summary: {...}, trades: [...]}
    """
    tf_enum = TF_MAP[timeframe]
    instruments = get_instruments(session, tf_enum)
    logger.info(f"Инструментов с таймфреймом {timeframe}: {len(instruments)}")

    min_required = max(50, 14, 200) + 1  # sma/adaptive/ema периоды + 1

    # Парсим даты один раз (как в dashboard/data.py)
    sd = None
    ed = None
    if start_date:
        sd = datetime.strptime(start_date.split("T")[0], "%Y-%m-%d")
    if end_date:
        ed = datetime.strptime(end_date.split("T")[0], "%Y-%m-%d")

    results: List[Dict[str, Any]] = []

    for instr in instruments:
        figi = instr["figi"]
        ticker = instr["ticker"]

        try:
            candles = get_candles(session, figi, tf_enum)
            candles = [c for c in candles if c.volume and c.volume > 0]

            # Фильтр по датам (как в дашборде)
            if sd is not None:
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]
            if ed is not None:
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed + timedelta(days=1)]

            if len(candles) < min_required:
                logger.debug(f"  Пропуск {ticker}: мало свечей ({len(candles)})")
                continue

            # Свежий экземпляр стратегии для каждого тикера
            strategy = DualMACrossoverStrategy(timeframe=timeframe, debug=False)
            result = strategy.run(candles)
            stats = result["stats"]

            if stats.closed_trades == 0:
                logger.debug(f"  Пропуск {ticker}: нет закрытых сделок")
                continue

            trades = result["trades"]
            summary = {
                "strategy_name": STRATEGY_NAME,
                "timeframe": timeframe,
                "ticker": ticker,
                "figi": figi,
                "total_trades": stats.total_trades,
                "closed_trades": stats.closed_trades,
                "winning": stats.winning_trades,
                "losing": stats.losing_trades,
                "win_rate": stats.win_rate,
                "avg_win": stats.avg_win_abs,
                "avg_loss": stats.avg_loss_abs,
                "avg_mfe": _calculate_avg_mfe(trades),
                "avg_mae": _calculate_avg_mae(trades),
                "avg_hold_days": stats.avg_hold_days,
                "median_long_up_move": stats.median_long_up_move,
                "median_short_down_move": stats.median_short_down_move,
                "total_pnl": stats.total_pnl,
                "total_pnl_percent": stats.total_pnl_percent,
                "final_balance": stats.final_balance,
            }

            results.append({
                "ticker": ticker,
                "figi": figi,
                "total_pnl": stats.total_pnl,
                "summary": summary,
                "trades": build_trade_rows(STRATEGY_NAME, timeframe, ticker, figi, trades),
            })

            logger.info(f"  ✓ {ticker}: {stats.closed_trades} сделок | PnL {stats.total_pnl:+,.2f}₽")

        except Exception as e:
            logger.error(f"  Ошибка {ticker}: {e}")
            continue

    return results


# ═══════════════════════════════════════════════════════════════════════════
# 5. ЗАПИСЬ В POSTGRES
# ═══════════════════════════════════════════════════════════════════════════

def save_positive_tickers(engine, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Отфильтровать тикеры с положительным Total PnL (> 0) и записать их
    в обе таблицы.

    Returns:
        Список записанных результатов (только положительные).
    """
    positives = [r for r in results if r["total_pnl"] > 0]
    positives.sort(key=lambda r: r["total_pnl"], reverse=True)

    logger.info(f"Тикеров с ПОЛОЖИТЕЛЬНЫМ PnL: {len(positives)} из {len(results)}")

    if not positives:
        return []

    summary_rows = [r["summary"] for r in positives]
    trade_rows: List[Dict[str, Any]] = []
    for r in positives:
        trade_rows.extend(r["trades"])

    with engine.begin() as conn:
        conn.execute(INSERT_SUMMARY, summary_rows)
        if trade_rows:
            conn.execute(INSERT_TRADE, trade_rows)

    logger.info(
        f"✓ Записано: {len(summary_rows)} строк в strategy_backtest_summary, "
        f"{len(trade_rows)} сделок в strategy_backtest_trades"
    )
    return positives


# ═══════════════════════════════════════════════════════════════════════════
# 6. ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backtest dual_ma_crossover (4h) и запись результатов в PostgreSQL"
    )
    parser.add_argument(
        "--timeframe", default=DEFAULT_TIMEFRAME,
        help=f"Таймфрейм (по умолчанию {DEFAULT_TIMEFRAME})",
    )
    parser.add_argument(
        "--start-date", default=DEFAULT_START_DATE,
        help=f"Начальная дата YYYY-MM-DD (по умолчанию {DEFAULT_START_DATE}, как в дашборде)",
    )
    parser.add_argument(
        "--end-date", default=None,
        help="Конечная дата YYYY-MM-DD (по умолчанию — до текущего момента)",
    )
    args = parser.parse_args()

    if args.timeframe not in TF_MAP:
        raise SystemExit(f"Неподдерживаемый таймфрейм: {args.timeframe}")

    load_dotenv()
    # Принудительно используем UTF-8 для вывода в консоль (Windows cp1251)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    engine = get_engine()
    Session = sessionmaker(bind=engine)

    print(f"\n  {'=' * 60}")
    print(f"  Бектест dual_ma_crossover [{args.timeframe}] → PostgreSQL")
    print(f"  Период: {args.start_date or 'начало'} → {args.end_date or 'сейчас'}")
    print(f"  {'=' * 60}\n")

    # 1. Создаём таблицы и полностью перезаписываем
    create_and_truncate(engine)

    # 2. Прогон стратегии по всем инструментам
    session = Session()
    try:
        results = collect_results(session, args.timeframe, args.start_date, args.end_date)
    finally:
        session.close()

    print(f"\n  Проанализировано тикеров: {len(results)}")

    if not results:
        print("  Сделок не найдено ни по одному тикеру. Таблицы перезаписаны (пустые).")
        print(f"  {'=' * 60}\n")
        return

    # 3. Фильтр положительных + запись
    positives = save_positive_tickers(engine, results)

    print("\n" + "=" * 60)
    print(f"  ВСЕГО тикеров:      {len(results)}")
    print(f"  Положительных:      {len(positives)}")
    print("-" * 60)

    total_pnl = 0.0
    total_trades = 0
    for r in positives:
        n = r["summary"]["closed_trades"]
        total_pnl += r["total_pnl"]
        total_trades += n
        print(f"    {r['ticker']:>6}: PnL {r['total_pnl']:+,.2f} ₽ | {n} сделок")

    if positives:
        print("-" * 60)
        print(f"  Суммарный PnL положительных: {total_pnl:+,.2f} ₽ | сделок: {total_trades}")

    print("  Данные записаны в PostgreSQL (таблицы перезаписаны).")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
