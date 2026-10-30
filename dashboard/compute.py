"""
Бизнес-логика для Dashboard.

Чистые функции: запуск стратегий, агрегация статистики.
Не зависят от Dash — можно тестировать отдельно.

Вынесено из app_dashboard.py для устранения дублирования
(один и тот же цикл обхода figi повторялся в трёх функциях).
"""

import logging
from typing import List, Dict, Optional, Any

from src.strategies.base_strategy import Trade
from src.strategies.strategy_factory import StrategyFactory
from dashboard.data import load_candles, get_figi_for_timeframe, get_ticker, has_enough_candles
from dashboard.config import DEFAULT_INITIAL_BALANCE

logger = logging.getLogger(__name__)

TEST_TICKER_FILTER: list[str] = []#['T', 'POSI', 'AFKS', 'LKOH', 'CHMF', 'EUTR', 'VTBR']  # ← впиши нужные тикеры, например: ["SBER", "GAZP", "LKOH"] 'PIKK', 'NVTK', 'MGNT'

# ═══════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════

def _calculate_avg_mfe(trades: List[Trade]) -> float:
    """Средний MFE (потенциальный TP) по всем сделкам."""
    valid_mfe = [t.mfe_percent for t in trades if t.mfe_percent is not None]
    return sum(valid_mfe) / len(valid_mfe) if valid_mfe else 0.0


def _calculate_avg_mae(trades: List[Trade]) -> float:
    """Средний MAE (потенциальный SL) по всем сделкам."""
    valid_mae = [t.mae_percent for t in trades if t.mae_percent is not None]
    return sum(valid_mae) / len(valid_mae) if valid_mae else 0.0


def _get_initial_balance(strategy_code: str, timeframe: str, ticker: Optional[str] = None) -> float:
    """Получить initial_balance из конфига стратегии.

    Ticker нужен, потому что TP/SL резолвятся из config/tp_sl.json при создании
    стратегии и отсутствие ключа — ошибка (T3-4). Баланс от TP/SL не зависит,
    поэтому при неудаче используется DEFAULT_INITIAL_BALANCE.
    """
    try:
        temp = StrategyFactory.create(
            strategy_code, timeframe, config={"ticker": ticker} if ticker else None, debug=False
        )
        return temp.config.get("initial_balance", DEFAULT_INITIAL_BALANCE)
    except Exception as e:
        logger.warning(f"Failed to get initial_balance from strategy, using default: {e}")
        return DEFAULT_INITIAL_BALANCE


# ═══════════════════════════════════════════════════════════════
# ОСНОВНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════

def filter_unprofitable(
    results: List[Dict[str, Any]],
    hide_unprofitable: bool = False,
) -> List[Dict[str, Any]]:
    """
    Отфильтровать инструменты с отрицательным Total PnL.

    Args:
        results: Список результатов (из run_strategy_for_all_tickers)
        hide_unprofitable: Если True — оставить только с total_pnl >= 0

    Returns:
        Отфильтрованный список.
    """
    if not hide_unprofitable:
        return results

    before = len(results)
    filtered = [r for r in results if r["summary"]["total_pnl"] >= 0]
    after = len(filtered)

    if before != after:
        removed = before - after
        logger.info(f"  Скрыто убыточных инструментов: {removed} (осталось {after}/{before})")

    return filtered


def run_strategy_for_ticker(
    figi: str,
    timeframe: str,
    strategy_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Запустить стратегию на одном тикере.

    Returns:
        Словарь с ключами: ticker, figi, stats, trades, summary — или None при ошибке/нехватке данных.
    """
    try:
        ticker = get_ticker(figi)
        candles = load_candles(figi, timeframe, start_date, end_date)

        if not has_enough_candles(candles):
            logger.warning(f"Skipping {ticker}: insufficient data")
            return None

        strategy = StrategyFactory.create(
            strategy_code, timeframe, config={"ticker": ticker}, debug=False
        )
        result = strategy.run(candles)
        stats = result["stats"]

        return {
            "ticker": ticker,
            "figi": figi,
            "candles": candles,
            "stats": stats,
            "trades": result["trades"],
            "strategy": strategy,
            "summary": {
                "ticker": ticker,
                "figi": figi,
                "total_trades": stats.total_trades,
                "closed_trades": stats.closed_trades,
                "winning": stats.winning_trades,
                "losing": stats.losing_trades,
                "win_rate": stats.win_rate,
                "avg_win": stats.avg_win_abs,
                "avg_loss": stats.avg_loss_abs,
                "total_pnl": stats.total_pnl,
                "total_pnl_percent": stats.total_pnl_percent,
                "final_balance": stats.final_balance,
                "avg_mfe": _calculate_avg_mfe(result["trades"]),
                "avg_mae": _calculate_avg_mae(result["trades"]),
                "avg_hold_days": stats.avg_hold_days,
                "expectancy": stats.expectancy,
                "profit_factor": stats.profit_factor,
                "payoff_ratio": stats.payoff_ratio,
                "max_drawdown": stats.max_drawdown,
                "sharpe_ratio": stats.sharpe_ratio,
                "z_score": stats.z_score,
                "median_long_up_move": stats.median_long_up_move,
                "median_short_down_move": stats.median_short_down_move,

            },
        }

    except Exception as e:
        logger.error(f"Error processing {figi}: {e}")
        return None


def run_strategy_for_all_tickers(
    timeframe: str,
    strategy_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Запустить стратегию на ВСЕХ тикерах с указанным таймфреймом.

    Returns:
        Список результатов (только успешные).
    """
    results: List[Dict[str, Any]] = []
    available_figis = get_figi_for_timeframe(timeframe)
    # Временный фильтр по тикерам
    if TEST_TICKER_FILTER:
        available_figis = [
            f for f in available_figis
            if get_ticker(f) in TEST_TICKER_FILTER
        ]
        logger.info(f"⚠️  Filtered to test tickers: {TEST_TICKER_FILTER} → {len(available_figis)} figi(s)")

    logger.info(f"Running {strategy_code} on {timeframe} across {len(available_figis)} instruments...")

    for figi in available_figis:
        result = run_strategy_for_ticker(figi, timeframe, strategy_code, start_date, end_date)
        if result is not None:
            results.append(result)
            ticker = result["ticker"]
            s = result["stats"]
            logger.info(f"✓ {ticker}: {s.closed_trades} trades, WR={s.win_rate:.1f}%, PnL={s.total_pnl:+.2f}₽")

    # Сортируем по PnL
    results.sort(key=lambda r: r["summary"]["total_pnl"], reverse=True)
    logger.info(f"✓ Completed {len(results)}/{len(available_figis)} instruments successfully")
    return results


def compute_aggregate_stats(
    results: List[Dict[str, Any]],
    strategy_code: str,
    timeframe: str,
) -> Dict[str, Any]:
    """
    Вычислить агрегированную статистику (TOTAL строка) по всем результатам.

    Использует портфельную логику: один стартовый баланс + суммарный PnL.
    """
    if not results:
        return {}

    summary_list = [r["summary"] for r in results]
    initial_balance = _get_initial_balance(strategy_code, timeframe, results[0].get("ticker"))

    total_trades = sum(r["total_trades"] for r in summary_list)
    closed_trades = sum(r["closed_trades"] for r in summary_list)
    winning = sum(r["winning"] for r in summary_list)
    losing = sum(r["losing"] for r in summary_list)
    total_pnl = sum(r["total_pnl"] for r in summary_list)

    total = {
        "total_trades": total_trades,
        "closed_trades": closed_trades,
        "winning": winning,
        "losing": losing,
        "total_pnl": total_pnl,
        "initial_balance": initial_balance,
        "final_balance": initial_balance + total_pnl,
        "win_rate": (winning / closed_trades * 100) if closed_trades > 0 else 0.0,
        "total_pnl_percent": (total_pnl / initial_balance * 100) if initial_balance > 0 else 0.0,
    }

    # Средний avg_win (взвешенный)
    total_avg_win = sum(r["avg_win"] * r["winning"] for r in summary_list)
    total["avg_win"] = total_avg_win / winning if winning > 0 else 0.0

    # Средний avg_loss (взвешенный)
    total_avg_loss = sum(r["avg_loss"] * r["losing"] for r in summary_list)
    total["avg_loss"] = total_avg_loss / losing if losing > 0 else 0.0

    # Средние MFE/MAE/hold
    n = len(summary_list)
    total["avg_mfe"] = sum(r["avg_mfe"] for r in summary_list) / n if n else 0.0
    total["avg_mae"] = sum(r["avg_mae"] for r in summary_list) / n if n else 0.0
    total["avg_hold_days"] = sum(r["avg_hold_days"] for r in summary_list) / n if n else 0.0
    total["median_long_up_move"] = sum(r["median_long_up_move"] for r in summary_list) / n if n else 0.0
    total["median_short_down_move"] = sum(r["median_short_down_move"] for r in summary_list) / n if n else 0.0

    return total


def compute_monthly_breakdown(
    results: List[Dict[str, Any]],
    strategy_code: str,
    timeframe: str,
) -> Dict[str, list]:
    """
    Помесячная разбивка по всем закрытым сделкам (все тикеры вместе).

    Returns:
        {
            "months":       ["2024-01", "2024-02", ...],
            "balance":      [105000, 103000, ...],   # нарастающий баланс
            "pnl":          [+5000, -2000, ...],     # PnL за месяц
            "win_count":    [5, 3, ...],
            "loss_count":   [2, 4, ...],
            "avg_hold":     [3.2, 5.1, ...],         # средний hold days
        }
    """
    from collections import defaultdict

    # Собираем ВСЕ закрытые сделки из всех тикеров
    all_closed: list = []
    for r in results:
        for trade in r.get("trades", []):
            if not trade.is_open() and trade.exit_time is not None:
                all_closed.append(trade)

    if not all_closed:
        return {"months": [], "balance": [], "pnl": [], "win_count": [], "loss_count": [], "avg_hold": []}

    # Группируем по месяцу
    monthly: dict = defaultdict(lambda: {"pnl": 0.0, "win": 0, "loss": 0, "hold_days": []})

    for t in all_closed:
        month_key = t.exit_time.strftime("%Y-%m")
        monthly[month_key]["pnl"] += t.pnl_abs or 0.0
        if t.pnl_percent and t.pnl_percent > 0:
            monthly[month_key]["win"] += 1
        else:
            monthly[month_key]["loss"] += 1
        if t.exit_time and t.entry_time:
            days = max(1, (t.exit_time - t.entry_time).days)
            monthly[month_key]["hold_days"].append(days)

    # Сортируем месяцы
    sorted_months = sorted(monthly.keys())

    months = []
    balance_list = []
    pnl_list = []
    win_list = []
    loss_list = []
    hold_list = []

    initial_balance = _get_initial_balance(strategy_code, timeframe, results[0].get("ticker"))
    running_balance = initial_balance

    for m in sorted_months:
        data = monthly[m]
        months.append(m)
        pnl_list.append(round(data["pnl"], 2))
        running_balance += data["pnl"]
        balance_list.append(round(running_balance, 2))
        win_list.append(data["win"])
        loss_list.append(data["loss"])
        avg_h = sum(data["hold_days"]) / len(data["hold_days"]) if data["hold_days"] else 0.0
        hold_list.append(round(avg_h, 1))

    return {
        "months": months,
        "balance": balance_list,
        "pnl": pnl_list,
        "win_count": win_list,
        "loss_count": loss_list,
        "avg_hold": hold_list,
    }


def compute_trades_list(
    results: List[Dict[str, Any]],
    timeframe: str,
) -> List[Dict[str, Any]]:
    """
    Собрать плоский список всех сделок из результатов для таблицы.

    Returns:
        List[dict]: Сделки с полями:
            signal_date, ticker, timeframe, entry_price, exit_price,
            days_held, pnl_net, pnl_percent
    """
    trades = []
    for r in results:
        ticker = r["ticker"]
        for t in r.get("trades", []):
            days_held = 0
            if t.exit_time and t.entry_time:
                days_held = max(1, (t.exit_time - t.entry_time).days)

            direction = "Long" if t.is_long() else "Short" if t.is_short() else "-"
            trades.append({
                "signal_date": t.entry_time.strftime("%Y-%m-%d %H:%M") if t.entry_time else "-",
                "ticker": ticker,
                "timeframe": timeframe,
                "direction": direction,
                "pattern_name": t.pattern_name or "-",
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "days_held": days_held,
                "pnl_net": t.pnl_net or 0.0,
                "pnl_percent": t.pnl_percent or 0.0,
            })

    # Сортируем по дате входа (сигнала) — от новых к старым
    trades.sort(key=lambda x: x["signal_date"], reverse=True)
    return trades


def compute_averaged_metrics(
    results: List[Dict[str, Any]],
) -> Dict[str, float]:
    """
    Вычислить усреднённые метрики (expectancy, profit_factor, payoff_ratio, max_drawdown)
    по всем результатам.
    """
    if not results:
        return {
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "payoff_ratio": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0
        }

    n = len(results)
    summary_list = [r["summary"] for r in results]
    sharpe_values = [r["sharpe_ratio"] for r in summary_list if r.get("sharpe_ratio") is not None]
    z_values = [r["z_score"] for r in summary_list if r.get("z_score") is not None]

    return {
        "expectancy": sum(r["expectancy"] for r in summary_list) / n,
        "profit_factor": sum(r["profit_factor"] for r in summary_list) / n,
        "payoff_ratio": sum(r["payoff_ratio"] for r in summary_list) / n,
        "max_drawdown": sum(r["max_drawdown"] for r in summary_list) / n,
        "sharpe_ratio": sum(sharpe_values) / len(sharpe_values) if sharpe_values else 0.0,
        "z_score": sum(z_values) / len(z_values) if z_values else 0.0,
    }
