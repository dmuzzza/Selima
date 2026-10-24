"""
Optuna-оптимизация стратегии macd_plus.

Схема валидации:
  - Все свечи по инструменту загружаются из PostgreSQL.
  - IS (In-Sample): первые 70% свечей — используются в objective().
  - OOS (Out-of-Sample): последние 30% свечей — только финальная проверка.

Результат сохраняется в:
  - optimization/tz_ml_optimization.md  (markdown-отчёт)
  - optimization/optimization_results.xlsx  (полная таблица по всем инструментам и trial'ам)
"""

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import optuna
import pandas as pd
from dotenv import load_dotenv

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.market_data.history_manager import HistoryManager
from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.strategies.macd_plus.strategy import MACDPlusStrategy
from src.brokers.sdk_broker import TinkoffSDKBroker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("optimize_macd_plus")

# Отключаем излишне подробные логи Optuna / SQLAlchemy
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ ОПТИМИЗАЦИИ
# ═══════════════════════════════════════════════════════════════════════════

TIMEOUT_SECONDS = 120  # таймаут на одно исследование (инструмент + таймфрейм)
N_TRIALS = 120         # число trials; при желании можно переопределить через CLI
IS_FRACTION = 0.70     # доля In-Sample

# Диапазоны гиперпараметров
PARAM_SPACE = {
    "macd_fast_period": (3, 21),
    "macd_slow_period": (10, 55),
    "macd_signal_period": (3, 21),
    "trend_ma_period": (50, 300),
    "tp_percent": (0.5, 6.0),
    "sl_percent": (0.3, 4.0),
}

# Минимальное число сделок, чтобы trial считался валидным
MIN_TRADES = 15

# Целевая метрика для оптимизации.
OBJECTIVE_METRIC = "sharpe_ratio"  # ключ из extract_metrics


# ═══════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OptimizationResult:
    figi: str
    ticker: str
    timeframe: str
    best_params: Dict
    is_metrics: Dict
    oos_metrics: Dict
    all_trials: int
    completed_trials: int
    best_value: float
    study: optuna.Study  # сохраняем для экспорта всех trials


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


def split_is_oos(candles: List[Candle], fraction: float = IS_FRACTION) -> Tuple[List[Candle], List[Candle]]:
    """Разделить свечи на IS и OOS по времени."""
    split_idx = int(len(candles) * fraction)
    if split_idx < 200:
        raise ValueError(f"Слишком мало свечей для IS: {split_idx}")
    return candles[:split_idx], candles[split_idx:]


def run_strategy(candles: List[Candle], timeframe: str, params: Dict, debug: bool = False) -> Dict:
    """Запустить стратегию с заданными параметрами."""
    config = {
        "initial_balance": 1_000_000.0,
        "position_size_percent": 100.0,
        "macd_fast_period": params["macd_fast_period"],
        "macd_slow_period": params["macd_slow_period"],
        "macd_signal_period": params["macd_signal_period"],
        "trend_ma_period": params["trend_ma_period"],
        "tp_percent": params["tp_percent"],
        "sl_percent": params["sl_percent"],
        "breakeven_enabled": False,
        "adx_filter_enabled": False,
    }
    strategy = MACDPlusStrategy(timeframe, config=config, debug=debug)
    result = strategy.run(candles)
    return result


def extract_metrics(stats) -> Dict:
    """Преобразовать StrategyStats в плоский dict."""
    return {
        "total_trades": stats.total_trades,
        "closed_trades": stats.closed_trades,
        "winning_trades": stats.winning_trades,
        "losing_trades": stats.losing_trades,
        "win_rate": stats.win_rate,
        "avg_win": stats.avg_win,
        "avg_loss": stats.avg_loss,
        "avg_win_abs": stats.avg_win_abs,
        "avg_loss_abs": stats.avg_loss_abs,
        "avg_hold_days": stats.avg_hold_days,
        "total_pnl": stats.total_pnl,
        "total_pnl_percent": stats.total_pnl_percent,
        "final_balance": stats.final_balance,
        "expectancy": stats.expectancy,
        "profit_factor": stats.profit_factor,
        "payoff_ratio": stats.payoff_ratio,
        "max_drawdown": stats.max_drawdown,
        "sharpe_ratio": stats.sharpe_ratio,
        "z_score": stats.z_score,
    }


def metric_value(metrics: Dict, name: str) -> float:
    """Безопасно получить числовое значение метрики."""
    val = metrics.get(name)
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return 0.0
    return float(val)


def build_objective(is_candles: List[Candle], timeframe: str):
    """Фабрика objective-функции для Optuna."""
    def objective(trial: optuna.Trial) -> float:
        # --- выбор параметров ---
        fast = trial.suggest_int("macd_fast_period", *PARAM_SPACE["macd_fast_period"])
        slow = trial.suggest_int("macd_slow_period", *PARAM_SPACE["macd_slow_period"])
        signal = trial.suggest_int("macd_signal_period", *PARAM_SPACE["macd_signal_period"])
        ma = trial.suggest_int("trend_ma_period", *PARAM_SPACE["trend_ma_period"])
        tp = trial.suggest_float("tp_percent", *PARAM_SPACE["tp_percent"], step=0.1)
        sl = trial.suggest_float("sl_percent", *PARAM_SPACE["sl_percent"], step=0.1)

        # MACD логика: fast < slow
        if fast >= slow:
            raise optuna.TrialPruned()

        params = {
            "macd_fast_period": fast,
            "macd_slow_period": slow,
            "macd_signal_period": signal,
            "trend_ma_period": ma,
            "tp_percent": tp,
            "sl_percent": sl,
        }

        try:
            result = run_strategy(is_candles, timeframe, params, debug=False)
        except Exception as e:
            logger.debug(f"Trial failed: {e}")
            raise optuna.TrialPruned()

        stats = result.get("stats")
        if stats is None or stats.closed_trades < MIN_TRADES:
            raise optuna.TrialPruned()

        metrics = extract_metrics(stats)

        # Основная целевая метрика
        value = metric_value(metrics, OBJECTIVE_METRIC)

        # Сохраняем метрики в user_attrs для последующего анализа
        trial.set_user_attr("metrics", metrics)
        trial.set_user_attr("params", params)

        # Возвращаем метрику даже если она отрицательная.
        # Optuna всё равно выберет максимальное значение.
        return value

    return objective


def optimize_single(
    figi: str,
    ticker: str,
    timeframe: str,
    n_trials: int = N_TRIALS,
    timeout: int = TIMEOUT_SECONDS,
) -> Optional[OptimizationResult]:
    """Оптимизировать одну комбинацию (figi, timeframe)."""
    logger.info(f"\n{'='*80}")
    logger.info(f"Optimizing {ticker} ({figi}) | timeframe={timeframe}")
    logger.info(f"{'='*80}")

    candles = load_candles_from_db(figi, timeframe)
    if len(candles) < 500:
        logger.warning(f"Недостаточно свечей: {len(candles)}. Пропускаем.")
        return None

    logger.info(f"Loaded {len(candles)} candles: {candles[0].open_time.date()} → {candles[-1].open_time.date()}")

    is_candles, oos_candles = split_is_oos(candles)
    logger.info(f"IS: {len(is_candles)} candles, OOS: {len(oos_candles)} candles")

    study_name = f"macd_plus_{ticker}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    study = optuna.create_study(
        study_name=study_name,
        direction="maximize",
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=5),
    )

    objective = build_objective(is_candles, timeframe)
    study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=True, n_jobs=1)

    completed_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if not completed_trials:
        logger.warning("No completed trials. Skipping.")
        return None

    best_params = study.best_params
    logger.info(f"Best params: {best_params}")
    logger.info(f"Best IS {OBJECTIVE_METRIC}: {study.best_value:.4f}")

    # --- IS финальная оценка ---
    is_result = run_strategy(is_candles, timeframe, best_params)
    is_metrics = extract_metrics(is_result["stats"])

    # --- OOS финальная оценка ---
    oos_result = run_strategy(oos_candles, timeframe, best_params)
    oos_metrics = extract_metrics(oos_result["stats"])

    return OptimizationResult(
        figi=figi,
        ticker=ticker,
        timeframe=timeframe,
        best_params=best_params,
        is_metrics=is_metrics,
        oos_metrics=oos_metrics,
        all_trials=len(study.trials),
        completed_trials=len(completed_trials),
        best_value=study.best_value,
        study=study,
    )


def format_metrics(m: Dict) -> str:
    """Отформатировать блок метрик."""
    lines = [
        f"  Total trades    : {m['total_trades']}",
        f"  Closed trades   : {m['closed_trades']}",
        f"  Win Rate        : {m['win_rate']:.1f}%",
        f"  Profit Factor   : {m['profit_factor']:.2f}",
        f"  Sharpe Ratio    : {m['sharpe_ratio']:.2f}" if m.get('sharpe_ratio') is not None else "  Sharpe Ratio    : N/A",
        f"  Expectancy      : {m['expectancy']:+.2f}%",
        f"  Max Drawdown    : {m['max_drawdown']:.1f}%",
        f"  Payoff Ratio    : {m['payoff_ratio']:.2f}",
        f"  Total PnL       : {m['total_pnl']:+.0f} RUB ({m['total_pnl_percent']:+.2f}%)",
    ]
    return "\n".join(lines)


def build_markdown_report(results: List[OptimizationResult]) -> str:
    """Сформировать единый Markdown-отчёт по шаблону."""
    # Выбираем лучший результат по IS Sharpe
    best = max(results, key=lambda r: r.is_metrics.get("sharpe_ratio") or 0.0)

    lines = [
        "====================================================",
        " OPTUNA OPTIMIZATION RESULTS",
        "====================================================",
        f" Strategy          : macd_plus",
        f" Instruments       : {len(results)}",
        f" Total trials      : {sum(r.all_trials for r in results)}",
        f" Objective metric  : {OBJECTIVE_METRIC}",
        f" Best combination  : {best.ticker} ({best.figi}) | {best.timeframe}",
        f" Best Sharpe (IS)  : {best.is_metrics.get('sharpe_ratio', 0.0):.2f}",
        "",
        " BEST PARAMETERS:",
        f"  macd_fast_period   : {best.best_params['macd_fast_period']}",
        f"  macd_slow_period   : {best.best_params['macd_slow_period']}",
        f"  macd_signal_period : {best.best_params['macd_signal_period']}",
        f"  trend_ma_period    : {best.best_params['trend_ma_period']}",
        f"  tp_percent         : {best.best_params['tp_percent']:.1f}",
        f"  sl_percent         : {best.best_params['sl_percent']:.1f}",
        "",
        "====================================================",
        f" IN-SAMPLE METRICS (IS, {int(IS_FRACTION*100)}%)",
        "====================================================",
        format_metrics(best.is_metrics),
        "",
        "====================================================",
        f" OUT-OF-SAMPLE METRICS (OOS, {int((1-IS_FRACTION)*100)}%) ← главная проверка",
        "====================================================",
        format_metrics(best.oos_metrics),
        "",
    ]

    # Проверка жизнеспособности
    oos_sharpe = best.oos_metrics.get("sharpe_ratio") or 0.0
    oos_pf = best.oos_metrics.get("profit_factor") or 0.0
    viable = oos_sharpe > 0 and oos_pf > 1.0
    lines.append(
        f" {'✅' if viable else '⚠️'} OOS Sharpe {'>' if oos_sharpe > 0 else '<='} 0, PF {'>' if oos_pf > 1.0 else '<='} 1.0 — "
        f"{'стратегия жизнеспособна' if viable else 'требует дополнительной проверки'}"
    )
    lines.append("====================================================")

    # Детальный раздел по всем инструментам
    lines.extend([
        "",
        "====================================================",
        " DETAILED RESULTS BY INSTRUMENT",
        "====================================================",
    ])
    for r in sorted(results, key=lambda x: x.is_metrics.get("sharpe_ratio") or 0.0, reverse=True):
        lines.extend([
            "",
            f"{r.ticker} ({r.figi}) | {r.timeframe}",
            f"  IS Sharpe: {r.is_metrics.get('sharpe_ratio', 0.0):.2f} | OOS Sharpe: {r.oos_metrics.get('sharpe_ratio', 0.0):.2f}",
            f"  IS PF:     {r.is_metrics.get('profit_factor', 0.0):.2f} | OOS PF:     {r.oos_metrics.get('profit_factor', 0.0):.2f}",
            f"  Params: MACD({r.best_params['macd_fast_period']},{r.best_params['macd_slow_period']},{r.best_params['macd_signal_period']}), "
            f"MA{r.best_params['trend_ma_period']}, TP={r.best_params['tp_percent']:.1f}%, SL={r.best_params['sl_percent']:.1f}%",
        ])

    return "\n".join(lines) + "\n"


def save_report(report: str, path: str = "optimization/tz_ml_optimization.md") -> None:
    """Сохранить markdown-отчёт."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"Markdown report saved: {path}")


def build_summary_dataframe(results: List[OptimizationResult]) -> pd.DataFrame:
    """Собрать сводную таблицу по инструментам/таймфреймам."""
    rows = []
    metric_cols = [
        "total_trades",
        "closed_trades",
        "winning_trades",
        "losing_trades",
        "win_rate",
        "avg_win",
        "avg_loss",
        "avg_win_abs",
        "avg_loss_abs",
        "avg_hold_days",
        "total_pnl",
        "total_pnl_percent",
        "final_balance",
        "expectancy",
        "profit_factor",
        "payoff_ratio",
        "max_drawdown",
        "sharpe_ratio",
        "z_score",
    ]
    for r in results:
        row = {
            "ticker": r.ticker,
            "figi": r.figi,
            "timeframe": r.timeframe,
            "all_trials": r.all_trials,
            "completed_trials": r.completed_trials,
            "best_is_value": r.best_value,
            "macd_fast_period": r.best_params.get("macd_fast_period"),
            "macd_slow_period": r.best_params.get("macd_slow_period"),
            "macd_signal_period": r.best_params.get("macd_signal_period"),
            "trend_ma_period": r.best_params.get("trend_ma_period"),
            "tp_percent": r.best_params.get("tp_percent"),
            "sl_percent": r.best_params.get("sl_percent"),
        }
        for col in metric_cols:
            row[f"is_{col}"] = r.is_metrics.get(col)
            row[f"oos_{col}"] = r.oos_metrics.get(col)
        rows.append(row)
    return pd.DataFrame(rows)


def build_trials_dataframe(results: List[OptimizationResult]) -> pd.DataFrame:
    """Собрать детальную таблицу по каждому завершённому trial."""
    rows = []
    for r in results:
        for trial in r.study.trials:
            if trial.state != optuna.trial.TrialState.COMPLETE:
                continue
            params = trial.user_attrs.get("params", trial.params)
            metrics = trial.user_attrs.get("metrics", {})
            row = {
                "ticker": r.ticker,
                "figi": r.figi,
                "timeframe": r.timeframe,
                "trial_number": trial.number,
                "value": trial.value,
            }
            for p_name, p_val in params.items():
                row[p_name] = p_val
            for m_name, m_val in metrics.items():
                row[m_name] = m_val
            rows.append(row)
    return pd.DataFrame(rows)


def save_excel_report(results: List[OptimizationResult], path: str = "optimization/optimization_results.xlsx") -> None:
    """Сохранить Excel-отчёт с двумя листами: summary и all_trials."""
    summary_df = build_summary_dataframe(results)
    trials_df = build_trials_dataframe(results)

    # Приводим числовые колонки к float, где возможно
    for df in (summary_df, trials_df):
        for col in df.columns:
            try:
                df[col] = pd.to_numeric(df[col], errors="ignore")
            except Exception:
                pass

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        trials_df.to_excel(writer, sheet_name="all_trials", index=False)

        # Автоширина колонок
        for sheet_name in ("summary", "all_trials"):
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except Exception:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

    logger.info(f"Excel report saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="Optimize macd_plus strategy with Optuna")
    parser.add_argument("--figi", type=str, default=None, help="Один FIGI для оптимизации")
    parser.add_argument("--timeframe", type=str, default=None, help="Один таймфрейм: 1h, 4h, 1d")
    parser.add_argument("--n-trials", type=int, default=N_TRIALS, help=f"Число trials (default {N_TRIALS})")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS, help=f"Таймаут в секундах (default {TIMEOUT_SECONDS})")
    parser.add_argument("--output", type=str, default="optimization/tz_ml_optimization.md", help="Путь к markdown-отчёту")
    parser.add_argument("--output-excel", type=str, default="optimization/optimization_results.xlsx", help="Путь к Excel-отчёту")
    args = parser.parse_args()

    # Загружаем конфигурацию инструментов
    with open("config/instruments.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    instruments = [i for i in config["instruments"] if i.get("enabled", True)]
    if args.figi:
        instruments = [i for i in instruments if i["figi"] == args.figi]

    timeframes = ["4h", "1d"]
    if args.timeframe:
        timeframes = [args.timeframe]

    results: List[OptimizationResult] = []
    for inst in instruments:
        for tf in timeframes:
            try:
                res = optimize_single(
                    figi=inst["figi"],
                    ticker=inst["ticker"],
                    timeframe=tf,
                    n_trials=args.n_trials,
                    timeout=args.timeout,
                )
                if res:
                    results.append(res)
            except Exception as e:
                logger.error(f"Ошибка при оптимизации {inst['ticker']} {tf}: {e}", exc_info=True)

    if not results:
        logger.error("Нет успешных результатов оптимизации.")
        return

    report = build_markdown_report(results)
    save_report(report, args.output)
    save_excel_report(results, args.output_excel)
    logger.info("Optimization finished. See reports above.")


if __name__ == "__main__":
    main()