"""
Grid Search TP/SL для стратегии dual_ma_crossover_after_testing.

Перебирает TP от 2.0 до 5.0% и SL от 1.0 до 4.0% с шагом 0.1.
Выводит топ-10 лучших комбинаций по Sharpe Ratio.

Использование:
    python optimization/grid_tp_sl_dual_ma.py

Можно указать таймфрейм и отфильтровать по тикеру:
    python optimization/grid_tp_sl_dual_ma.py --timeframe 4h --ticker SBER
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.market_data.history_manager import HistoryManager
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.strategies.dual_ma_crossover.strategy import DualMACrossoverStrategy
from src.brokers.sdk_broker import TinkoffSDKBroker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("grid_tp_sl_dual_ma")

# Приглушаем логи стратегии, индикаторов и SQLAlchemy — чтобы прогресс-бар был чистым
for noisy_logger in [
    "DualMACrossoverStrategy",
    "src.strategies.indicators_builder",
    "sqlalchemy.engine",
    "src.market_data",
]:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ ПЕРЕБОРА
# ═══════════════════════════════════════════════════════════════

TP_RANGE = (2.0, 5.0)      # min, max
SL_RANGE = (1.0, 4.0)      # min, max
STEP = 0.1                 # шаг

TOP_N = 10                 # сколько лучших комбинаций вывести

MIN_TRADES = 10            # минимальное число закрытых сделок


@dataclass
class GridResult:
    """Результат одного запуска на конкретном инструменте."""
    ticker: str
    figi: str
    timeframe: str
    tp: float
    sl: float
    total_trades: int
    closed_trades: int
    winning: int
    losing: int
    win_rate: float
    total_pnl: float
    total_pnl_percent: float
    profit_factor: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float
    avg_hold_days: float


def load_candles_from_db(figi: str, timeframe: str) -> List[Candle]:
    """Загрузить свечи из PostgreSQL через HistoryManager."""
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN", "")
    broker = TinkoffSDKBroker(token)
    history = HistoryManager(broker)
    tf_enum = Timeframe(timeframe)
    candles = history.get_candles(figi, tf_enum)
    candles = [c for c in candles if c.volume and c.volume > 0]
    return candles


def generate_tp_sl_grid() -> List[tuple]:
    """Сгенерировать сетку TP x SL."""
    pairs = []
    tp = TP_RANGE[0]
    while tp <= TP_RANGE[1] + 0.1:
        sl = SL_RANGE[0]
        while sl <= SL_RANGE[1] + 0.1:
            if tp > sl:  # TP должен быть больше SL
                pairs.append((round(tp, 1), round(sl, 1)))
            sl += STEP
        tp += STEP
    return pairs


def run_single(
    candles: List[Candle],
    ticker: str,
    figi: str,
    timeframe: str,
    tp: float,
    sl: float,
) -> Optional[GridResult]:
    """Запустить стратегию с конкретными TP/SL."""
    config = {
        "initial_balance": 50_000.0,
        "position_size_percent": 100.0,
        "allow_short": True,
        "sma_period": 50,
        "adaptive_period": 14,
        "use_ema_filter": True,
        "ema_period": 200,
        "tp_percent": tp,
        "sl_percent": sl,
    }
    try:
        strategy = DualMACrossoverStrategy(timeframe, config=config, debug=False)
        result = strategy.run(candles)
        stats = result["stats"]

        if stats.closed_trades < MIN_TRADES:
            return None

        sr = stats.sharpe_ratio
        if sr is None:
            sr = 0.0

        return GridResult(
            ticker=ticker,
            figi=figi,
            timeframe=timeframe,
            tp=tp,
            sl=sl,
            total_trades=stats.total_trades,
            closed_trades=stats.closed_trades,
            winning=stats.winning_trades,
            losing=stats.losing_trades,
            win_rate=stats.win_rate,
            total_pnl=stats.total_pnl,
            total_pnl_percent=stats.total_pnl_percent,
            profit_factor=stats.profit_factor,
            expectancy=stats.expectancy,
            sharpe_ratio=sr,
            max_drawdown=stats.max_drawdown,
            avg_hold_days=stats.avg_hold_days,
        )
    except Exception as e:
        logger.error(f"Error running {ticker} TP={tp} SL={sl}: {e}")
        return None


def _format_result_table(results: List[GridResult], title: str) -> None:
    """Вывести таблицу результатов."""
    print("\n" + "=" * 130)
    print(f"  {title}")
    print("=" * 130)
    header = (
        f"{'#':>3} | {'Ticker':<8} {'TF':<5} {'TP%':>6} {'SL%':>6} "
        f"{'Trades':>7} {'Closed':>7} {'W':>5} {'L':>5} {'WR%':>7} "
        f"{'PnL':>10} {'PnL%':>8} {'PF':>6} {'Expect%':>8} "
        f"{'Sharpe':>7} {'DD%':>7} {'HoldD':>6}"
    )
    print(header)
    print("-" * 130)

    for idx, r in enumerate(results, 1):
        line = (
            f"{idx:>3} | {r.ticker:<8} {r.timeframe:<5} {r.tp:>6.1f} {r.sl:>6.1f} "
            f"{r.total_trades:>7} {r.closed_trades:>7} {r.winning:>5} {r.losing:>5} {r.win_rate:>7.1f} "
            f"{r.total_pnl:>10,.0f} {r.total_pnl_percent:>+8.2f} {r.profit_factor:>6.2f} {r.expectancy:>+8.2f} "
            f"{r.sharpe_ratio:>7.2f} {r.max_drawdown:>7.1f} {r.avg_hold_days:>6.1f}"
        )
        print(line)

    print("=" * 130)


def _format_overall_table(results: List[GridResult], title: str) -> None:
    """Вывести общую таблицу без тикеров (только TP/SL и метрики)."""
    print("\n" + "=" * 120)
    print(f"  {title}")
    print("=" * 120)
    header = (
        f"{'#':>3} | {'TP%':>6} {'SL%':>6} "
        f"{'Trades':>7} {'Closed':>7} {'W':>5} {'L':>5} {'WR%':>7} "
        f"{'PnL':>10} {'PnL%':>8} {'PF':>6} {'Expect%':>8} "
        f"{'Sharpe':>7} {'DD%':>7} {'HoldD':>6}"
    )
    print(header)
    print("-" * 120)

    for idx, r in enumerate(results, 1):
        line = (
            f"{idx:>3} | {r.tp:>6.1f} {r.sl:>6.1f} "
            f"{r.total_trades:>7} {r.closed_trades:>7} {r.winning:>5} {r.losing:>5} {r.win_rate:>7.1f} "
            f"{r.total_pnl:>10,.0f} {r.total_pnl_percent:>+8.2f} {r.profit_factor:>6.2f} {r.expectancy:>+8.2f} "
            f"{r.sharpe_ratio:>7.2f} {r.max_drawdown:>7.1f} {r.avg_hold_days:>6.1f}"
        )
        print(line)

    print("=" * 120)


def print_all_results(results: List[GridResult]) -> None:
    """Вывести топ-3 по каждому инструменту и общий топ-3."""
    # --- Топ-3 по каждому инструменту ---
    tickers = sorted(set(r.ticker for r in results))
    for ticker in tickers:
        ticker_results = [r for r in results if r.ticker == ticker]
        ticker_top = sorted(ticker_results, key=lambda r: r.sharpe_ratio, reverse=True)[:3]
        _format_result_table(ticker_top, f"TOP 3 — {ticker} ({len(ticker_results)} valid combos)")

    # --- Общий топ-3 (без разделения по тикерам) ---
    overall_top = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)[:3]
    _format_overall_table(overall_top, f"TOP 3 — OVERALL ({len(results)} total)")


def main():
    parser = argparse.ArgumentParser(description="Grid search TP/SL for dual_ma_crossover_after_testing")
    parser.add_argument("--timeframe", type=str, default="4h", help="Таймфрейм (4h, 1d, 1h)")
    parser.add_argument("--ticker", type=str, default=None, help="Фильтр по тикеру (SBER, GAZP, ...)")
    args = parser.parse_args()

    timeframe = args.timeframe

    # Загружаем инструменты
    with open("config/instruments.json", "r", encoding="utf-8") as f:
        inst_config = json.load(f)
    instruments = [i for i in inst_config["instruments"] if i.get("enabled", True)]

    if args.ticker:
        instruments = [i for i in instruments if i["ticker"] == args.ticker.upper()]
        if not instruments:
            logger.error(f"Ticker {args.ticker} not found in instruments.json")
            return

    # Генерируем сетку
    grid = generate_tp_sl_grid()
    logger.info(f"Grid: {len(grid)} combinations - TP {TP_RANGE}, SL {SL_RANGE}, step {STEP}")
    logger.info(f"Instruments: {len(instruments)}, timeframe: {timeframe}")

    all_results: List[GridResult] = []

    for inst in tqdm(instruments, desc="Instruments", unit="inst"):
        ticker = inst["ticker"]
        figi = inst["figi"]

        try:
            candles = load_candles_from_db(figi, timeframe)
        except Exception as e:
            logger.error(f"Failed to load candles for {ticker}: {e}")
            continue

        if len(candles) < 300:
            logger.warning(f"{ticker}: insufficient candles ({len(candles)}), skipping")
            continue

        instrument_before = len(all_results)
        for tp, sl in tqdm(grid, desc=f"  {ticker} TP×SL", unit="comb", leave=False):
            result = run_single(candles, ticker, figi, timeframe, tp, sl)
            if result is not None:
                all_results.append(result)

        instrument_results = len(all_results) - instrument_before
        logger.info(f"  {ticker}: {instrument_results} valid / {len(grid)} combos")

    if not all_results:
        logger.error("No valid results. Try lowering MIN_TRADES or checking data.")
        return

    print_all_results(all_results)


if __name__ == "__main__":
    main()
