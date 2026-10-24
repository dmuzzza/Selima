"""
SQL-запросы и расчёт метрик для web_dashboard.

Источник данных: PostgreSQL-таблицы strategy_backtest_summary и
strategy_backtest_trades (наполняются scripts/export_dual_ma_to_postgres.py).

Чистые функции без FastAPI — можно тестировать отдельно.
"""

import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_BALANCE: float = 5000.0
DEFAULT_PORT: int = 8050

# ═══════════════════════════════════════════════════════════════
# ENGINE
# ═══════════════════════════════════════════════════════════════

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """Engine к PostgreSQL из .env (создаётся один раз)."""
    global _engine
    if _engine is None:
        load_dotenv()
        db_url = (
            f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
            f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}"
            f"?sslmode=require"
        )
        _engine = create_engine(db_url, pool_pre_ping=True)
    return _engine


# ═══════════════════════════════════════════════════════════════
# ФИЛЬТРЫ
# ═══════════════════════════════════════════════════════════════

def _parse_date(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """Строка YYYY-MM-DD → datetime (для end_of_day — 23:59:59)."""
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d
    except ValueError:
        return None


def _build_where(
    tickers: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    directions: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    table_alias: str = "t",
) -> tuple[str, Dict[str, Any]]:
    """
    Собрать WHERE-условия для обеих таблиц.

    Returns:
        (where_sql, params) — where_sql без ключевого слова WHERE (может быть пустым).
    """
    clauses: List[str] = []
    params: Dict[str, Any] = {}

    if tickers:
        keys = []
        for i, t in enumerate(tickers):
            key = f"ticker_{i}"
            params[key] = t
            keys.append(f":{key}")
        clauses.append(f"{table_alias}.ticker IN ({', '.join(keys)})")

    if timeframes:
        keys = []
        for i, tf in enumerate(timeframes):
            key = f"tf_{i}"
            params[key] = tf
            keys.append(f":{key}")
        clauses.append(f"{table_alias}.timeframe IN ({', '.join(keys)})")

    if directions:
        keys = []
        for i, d in enumerate(directions):
            key = f"dir_{i}"
            params[key] = d
            keys.append(f":{key}")
        clauses.append(f"{table_alias}.direction IN ({', '.join(keys)})")

    dt_from = _parse_date(date_from)
    if dt_from:
        params["date_from"] = dt_from
        clauses.append(f"{table_alias}.signal_date >= :date_from")

    dt_to = _parse_date(date_to, end_of_day=True)
    if dt_to:
        params["date_to"] = dt_to
        clauses.append(f"{table_alias}.signal_date <= :date_to")

    return (" AND ".join(clauses), params) if clauses else ("", params)


# ═══════════════════════════════════════════════════════════════
# ВЫБОРКА ДАННЫХ
# ═══════════════════════════════════════════════════════════════

def fetch_trades(
    tickers: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    directions: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Все отфильтрованные сделки из strategy_backtest_trades."""
    where, params = _build_where(tickers, timeframes, directions, date_from, date_to)
    sql = f"""
        SELECT ticker, timeframe, signal_date, direction, pattern_name,
               entry_price, exit_price, days_held, pnl_net, pnl_percent
        FROM strategy_backtest_trades t
        {f'WHERE {where}' if where else ''}
        ORDER BY signal_date ASC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    trades = []
    for r in rows:
        signal_date = r["signal_date"]
        days_held = r["days_held"] or 0
        exit_price = r["exit_price"]
        is_open = exit_price is None or days_held == 0
        trades.append({
            "ticker": r["ticker"],
            "timeframe": r["timeframe"],
            "signal_date": signal_date,
            "direction": r["direction"],
            "pattern_name": r["pattern_name"],
            "entry_price": float(r["entry_price"] or 0),
            "exit_price": float(exit_price) if exit_price is not None else None,
            "days_held": days_held,
            "pnl_net": float(r["pnl_net"] or 0),
            "pnl_percent": float(r["pnl_percent"] or 0),
            # Приблизительная дата выхода (в таблице нет exit_time)
            "exit_date": signal_date + timedelta(days=days_held) if not is_open else None,
            "is_open": is_open,
        })
    return trades


def fetch_summary(
    tickers: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    directions: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Строки strategy_backtest_summary по фильтру.

    Направление/даты в summary не хранятся — эти фильтры применяются
    к тикерам, у которых есть подходящие сделки в strategy_backtest_trades.
    """
    where, params = _build_where(tickers, timeframes, directions, date_from, date_to, table_alias="s")

    # Подзапрос: тикеры, подходящие под фильтры по сделкам
    t_where, t_params = _build_where(tickers, timeframes, directions, date_from, date_to, table_alias="t")
    ticker_subquery = ""
    if t_where:
        ticker_subquery = (
            f" AND s.ticker IN (SELECT DISTINCT t.ticker FROM strategy_backtest_trades t WHERE {t_where})"
        )
        params.update(t_params)

    sql = f"""
        SELECT s.strategy_name, s.timeframe, s.ticker, s.figi,
               s.total_trades, s.closed_trades, s.winning, s.losing, s.win_rate,
               s.avg_win, s.avg_loss, s.avg_mfe, s.avg_mae, s.avg_hold_days,
               s.median_long_up_move, s.median_short_down_move,
               s.total_pnl, s.total_pnl_percent, s.final_balance
        FROM strategy_backtest_summary s
        {f'WHERE {where}' if where else ''}
        {ticker_subquery}
        ORDER BY s.total_pnl DESC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    result = []
    for r in rows:
        result.append({
            "strategy_name": r["strategy_name"],
            "timeframe": r["timeframe"],
            "ticker": r["ticker"],
            "figi": r["figi"],
            "total_trades": int(r["total_trades"] or 0),
            "closed_trades": int(r["closed_trades"] or 0),
            "winning": int(r["winning"] or 0),
            "losing": int(r["losing"] or 0),
            "win_rate": float(r["win_rate"] or 0),
            "avg_win": float(r["avg_win"] or 0),
            "avg_loss": float(r["avg_loss"] or 0),
            "avg_mfe": float(r["avg_mfe"] or 0),
            "avg_mae": float(r["avg_mae"] or 0),
            "avg_hold_days": float(r["avg_hold_days"] or 0),
            "median_long_up_move": float(r["median_long_up_move"] or 0),
            "median_short_down_move": float(r["median_short_down_move"] or 0),
            "total_pnl": float(r["total_pnl"] or 0),
            "total_pnl_percent": float(r["total_pnl_percent"] or 0),
            "final_balance": float(r["final_balance"] or 0),
        })
    return result


def fetch_filter_options() -> Dict[str, Any]:
    """Опции для фильтров: тикеры, таймфреймы, направления, диапазон дат."""
    with get_engine().connect() as conn:
        tickers = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT ticker FROM strategy_backtest_trades ORDER BY ticker"
        )).fetchall()]
        timeframes = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT timeframe FROM strategy_backtest_trades ORDER BY timeframe"
        )).fetchall()]
        directions = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT direction FROM strategy_backtest_trades ORDER BY direction"
        )).fetchall()]
        dates = conn.execute(text(
            "SELECT MIN(signal_date), MAX(signal_date) FROM strategy_backtest_trades"
        )).fetchone()

    return {
        "tickers": tickers,
        "timeframes": timeframes,
        "directions": directions,
        "date_min": dates[0].strftime("%Y-%m-%d") if dates and dates[0] else None,
        "date_max": dates[1].strftime("%Y-%m-%d") if dates and dates[1] else None,
    }


# ═══════════════════════════════════════════════════════════════
# МЕТРИКИ ИЗ СДЕЛОК
# ═══════════════════════════════════════════════════════════════

def _z_score(pnl_values: List[float]) -> float:
    """
    Z-score по сериям (streaks) сделок.
    Положительный z → серии короче случайных (возврат к среднему),
    отрицательный → серии длиннее случайных.
    """
    outcomes = [1 if p > 0 else 0 for p in pnl_values]
    n = len(outcomes)
    wins = sum(outcomes)
    losses = n - wins
    if n < 2 or wins == 0 or losses == 0:
        return 0.0

    # Число серий (runs)
    runs = 1
    for i in range(1, n):
        if outcomes[i] != outcomes[i - 1]:
            runs += 1

    mean_runs = 2.0 * wins * losses / n
    var = (2.0 * wins * losses) * (2.0 * wins * losses - n) / (n * n * (n - 1))
    if var <= 0:
        return 0.0
    return (runs - mean_runs) / math.sqrt(var)


def compute_metrics(
    trades: List[Dict[str, Any]],
    summary_rows: List[Dict[str, Any]],
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
) -> Dict[str, Any]:
    """
    Все KPI-метрики, рассчитанные из сделок (как в dashboard/compute.py).

    Метрики, отсутствующие в PG (expectancy, profit_factor, payoff_ratio,
    max_drawdown, sharpe, z_score), считаются здесь из strategy_backtest_trades.
    """
    closed = [t for t in trades if not t["is_open"]]
    wins = [t for t in closed if t["pnl_net"] > 0]
    losses = [t for t in closed if t["pnl_net"] <= 0]

    total_pnl = sum(t["pnl_net"] for t in closed)
    avg_win = sum(t["pnl_net"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["pnl_net"] for t in losses) / len(losses) if losses else 0.0

    gross_profit = sum(t["pnl_net"] for t in wins)
    gross_loss = abs(sum(t["pnl_net"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
    expectancy = total_pnl / len(closed) if closed else 0.0

    # Max drawdown по кривой equity (закрытые сделки, порядок по exit_date)
    sorted_closed = sorted(closed, key=lambda t: t["exit_date"] or t["signal_date"])
    equity = initial_balance
    peak = initial_balance
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for t in sorted_closed:
        equity += t["pnl_net"]
        if equity > peak:
            peak = equity
        dd_abs = peak - equity
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs
            max_dd_pct = (dd_abs / peak * 100) if peak > 0 else 0.0

    # Sharpe по помесячной доходности (аннуализированный)
    monthly_pnl = _group_by_month(sorted_closed, lambda t: t["pnl_net"])
    monthly_returns = [pnl / initial_balance for pnl in monthly_pnl.values()] if monthly_pnl else []
    if len(monthly_returns) > 1:
        mean_r = sum(monthly_returns) / len(monthly_returns)
        var_r = sum((r - mean_r) ** 2 for r in monthly_returns) / (len(monthly_returns) - 1)
        std_r = math.sqrt(var_r)
        sharpe = (mean_r / std_r) * math.sqrt(12) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Усреднённые по тикерам метрики из summary
    n = len(summary_rows)
    avg_mfe = sum(r["avg_mfe"] for r in summary_rows) / n if n else 0.0
    avg_mae = sum(r["avg_mae"] for r in summary_rows) / n if n else 0.0
    median_long = sum(r["median_long_up_move"] for r in summary_rows) / n if n else 0.0
    median_short = sum(r["median_short_down_move"] for r in summary_rows) / n if n else 0.0

    return {
        "initial_balance": initial_balance,
        "final_balance": initial_balance + total_pnl,
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "winning": len(wins),
        "losing": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round(total_pnl / initial_balance * 100, 2) if initial_balance > 0 else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 3),
        "payoff_ratio": round(payoff_ratio, 3),
        "max_drawdown": round(max_dd_abs, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "z_score": round(_z_score([t["pnl_net"] for t in closed]), 3),
        "avg_mfe": round(avg_mfe, 2),
        "avg_mae": round(avg_mae, 2),
        "median_long_up_move": round(median_long, 2),
        "median_short_down_move": round(median_short, 2),
    }


# ═══════════════════════════════════════════════════════════════
# ПОМЕСЯЧНАЯ РАЗБИВКА
# ═══════════════════════════════════════════════════════════════

def _month_key(d: datetime) -> str:
    return d.strftime("%Y-%m")


def _group_by_month(closed_trades: List[Dict[str, Any]], value_fn) -> Dict[str, float]:
    grouped: Dict[str, float] = defaultdict(float)
    for t in closed_trades:
        grouped[_month_key(t["exit_date"] or t["signal_date"])] += value_fn(t)
    return dict(grouped)


def compute_monthly(
    trades: List[Dict[str, Any]],
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
) -> Dict[str, Any]:
    """
    Помесячная разбивка по закрытым сделкам:
    баланс (нарастающий итог), PnL, win/loss, средний hold.
    """
    closed = [t for t in trades if not t["is_open"]]
    if not closed:
        return {"months": [], "balance": [], "pnl": [], "win_count": [], "loss_count": [], "avg_hold": []}

    monthly: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"pnl": 0.0, "win": 0, "loss": 0, "hold_days": []}
    )
    for t in closed:
        m = monthly[_month_key(t["exit_date"] or t["signal_date"])]
        m["pnl"] += t["pnl_net"]
        if t["pnl_percent"] > 0:
            m["win"] += 1
        else:
            m["loss"] += 1
        m["hold_days"].append(max(1, t["days_held"]))

    months = sorted(monthly.keys())
    balance = []
    running = initial_balance
    for m in months:
        running += monthly[m]["pnl"]
        balance.append(round(running, 2))

    return {
        "months": months,
        "balance": balance,
        "pnl": [round(monthly[m]["pnl"], 2) for m in months],
        "win_count": [monthly[m]["win"] for m in months],
        "loss_count": [monthly[m]["loss"] for m in months],
        "avg_hold": [
            round(sum(monthly[m]["hold_days"]) / len(monthly[m]["hold_days"]), 1)
            for m in months
        ],
    }


def compute_concurrent(
    trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Одновременные открытые позиции по месяцам и направлениям (наслоение интервалов).

    Каждая сделка — интервал [signal_date, signal_date + days_held].
    Незакрытые сделки (exit_price IS NULL) — интервал до конца периода данных.

    Returns:
        {
            "months": ["2024-01", ...],
            "long_avg":  [...], "long_max": [...],
            "short_avg": [...], "short_max": [...],
        }
    """
    if not trades:
        return {"months": [], "long_avg": [], "long_max": [], "short_avg": [], "short_max": []}

    # Границы периода
    period_start = min(t["signal_date"] for t in trades).date()
    period_end = max(
        [(t["exit_date"] or t["signal_date"]).date() for t in trades]
        + [t["signal_date"].date() for t in trades]
    )

    # Интервалы по направлениям
    intervals: Dict[str, List[tuple]] = {"Long": [], "Short": []}
    for t in trades:
        direction = t["direction"] if t["direction"] in intervals else "Long"
        start = t["signal_date"].date()
        # Незакрытые сделки тянем до конца периода (иначе выпадут из наслоений)
        end = t["exit_date"].date() if t["exit_date"] else period_end
        if end < start:
            end = start
        intervals[direction].append((start, end))

    # Проход по дням месяцев
    monthly: Dict[str, Dict[str, List[int]]] = defaultdict(
        lambda: {"Long": [], "Short": []}
    )
    day = date(period_start.year, period_start.month, 1)
    while day <= period_end:
        key = day.strftime("%Y-%m")
        for direction, ivs in intervals.items():
            count = sum(1 for s, e in ivs if s <= day <= e)
            monthly[key][direction].append(count)
        day += timedelta(days=1)

    months = sorted(monthly.keys())
    return {
        "months": months,
        "long_avg": [
            round(sum(monthly[m]["Long"]) / len(monthly[m]["Long"]), 2) if monthly[m]["Long"] else 0.0
            for m in months
        ],
        "long_max": [max(monthly[m]["Long"]) if monthly[m]["Long"] else 0 for m in months],
        "short_avg": [
            round(sum(monthly[m]["Short"]) / len(monthly[m]["Short"]), 2) if monthly[m]["Short"] else 0.0
            for m in months
        ],
        "short_max": [max(monthly[m]["Short"]) if monthly[m]["Short"] else 0 for m in months],
    }


def compute_pnl_histogram(
    trades: List[Dict[str, Any]],
    bins: int = 30,
) -> Dict[str, Any]:
    """
    Гистограмма распределения PnL (%) по закрытым сделкам: общая + Long/Short.
    """
    closed = [t for t in trades if not t["is_open"]]
    if not closed:
        return {"edges": [], "all": [], "long": [], "short": []}

    values = [t["pnl_percent"] for t in closed]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    width = (hi - lo) / bins
    edges = [round(lo + i * width, 2) for i in range(bins + 1)]

    def hist(subset: List[float]) -> List[int]:
        counts = [0] * bins
        for v in subset:
            idx = min(int((v - lo) / width), bins - 1)
            counts[idx] += 1
        return counts

    return {
        "edges": edges,
        "all": hist(values),
        "long": hist([t["pnl_percent"] for t in closed if t["direction"] == "Long"]),
        "short": hist([t["pnl_percent"] for t in closed if t["direction"] == "Short"]),
    }


# ═══════════════════════════════════════════════════════════════
# ТАБЛИЦА СДЕЛОК (пагинация + сортировка)
# ═══════════════════════════════════════════════════════════════

SORTABLE_FIELDS = {
    "signal_date", "ticker", "timeframe", "direction", "pattern_name",
    "entry_price", "exit_price", "days_held", "pnl_net", "pnl_percent",
}


def paginate_trades(
    trades: List[Dict[str, Any]],
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "signal_date",
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """Отсортировать и разбить сделки по страницам (для таблицы на фронте)."""
    if sort_by not in SORTABLE_FIELDS:
        sort_by = "signal_date"
    reverse = sort_dir.lower() != "asc"

    def sort_key(t):
        v = t.get(sort_by)
        if v is None:
            return (1, 0)  # None в конец
        if isinstance(v, datetime):
            return (0, v.timestamp())
        return (0, v)

    sorted_trades = sorted(trades, key=sort_key, reverse=reverse)

    total = len(sorted_trades)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    page_items = sorted_trades[start:start + page_size]

    items = [{
        "signal_date": t["signal_date"].strftime("%Y-%m-%d %H:%M") if t["signal_date"] else "-",
        "ticker": t["ticker"],
        "timeframe": t["timeframe"],
        "direction": t["direction"],
        "pattern_name": t["pattern_name"] or "-",
        "entry_price": t["entry_price"],
        "exit_price": t["exit_price"],
        "days_held": t["days_held"],
        "pnl_net": round(t["pnl_net"], 2),
        "pnl_percent": round(t["pnl_percent"], 2),
        "is_open": t["is_open"],
    } for t in page_items]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


# ═══════════════════════════════════════════════════════════════
# TOTAL-СТРОКА ПО ТИКЕРАМ (как compute_aggregate_stats в dashboard)
# ═══════════════════════════════════════════════════════════════

def compute_summary_total(
    summary_rows: List[Dict[str, Any]],
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
) -> Optional[Dict[str, Any]]:
    """Агрегированная TOTAL-строка по всем отфильтрованным тикерам."""
    if not summary_rows:
        return None

    total_pnl = sum(r["total_pnl"] for r in summary_rows)
    closed = sum(r["closed_trades"] for r in summary_rows)
    winning = sum(r["winning"] for r in summary_rows)
    losing = sum(r["losing"] for r in summary_rows)
    n = len(summary_rows)

    return {
        "strategy_name": "TOTAL",
        "timeframe": "-",
        "ticker": f"{n} tickers",
        "figi": "-",
        "total_trades": sum(r["total_trades"] for r in summary_rows),
        "closed_trades": closed,
        "winning": winning,
        "losing": losing,
        "win_rate": (winning / closed * 100) if closed else 0.0,
        "avg_win": (sum(r["avg_win"] * r["winning"] for r in summary_rows) / winning) if winning else 0.0,
        "avg_loss": (sum(r["avg_loss"] * r["losing"] for r in summary_rows) / losing) if losing else 0.0,
        "avg_mfe": sum(r["avg_mfe"] for r in summary_rows) / n,
        "avg_mae": sum(r["avg_mae"] for r in summary_rows) / n,
        "avg_hold_days": sum(r["avg_hold_days"] for r in summary_rows) / n,
        "median_long_up_move": sum(r["median_long_up_move"] for r in summary_rows) / n,
        "median_short_down_move": sum(r["median_short_down_move"] for r in summary_rows) / n,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round(total_pnl / initial_balance * 100, 2) if initial_balance > 0 else 0.0,
        "final_balance": round(initial_balance + total_pnl, 2),
    }
