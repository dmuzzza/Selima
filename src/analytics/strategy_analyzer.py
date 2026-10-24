"""
StrategyAnalyzer — универсальный инструмент для полного анализа торговой стратегии.
Поддерживает кастомные метрики, интерпретацию и подготовку отчёта.
"""

from typing import Dict, List, Any, Callable, Optional
import numpy as np


class StrategyAnalyzer:
    def __init__(self, strategy_result: Dict[str, Any]):
        """
        Инициализация анализатора.

        :param strategy_result: результат выполнения BaseStrategy.run()
            {
                "signals": [...],
                "trades": [...],
                "stats": {...},
                "config": {...},
                "timeframe": "1h"
            }
        """
        self.signals = strategy_result.get("signals", [])
        self.trades = strategy_result.get("trades", [])
        self.stats = strategy_result.get("stats", {})
        self.config = strategy_result.get("config", {})
        self.timeframe = strategy_result.get("timeframe", "N/A")

        # Словарь для хранения всех метрик
        self.metrics: Dict[str, float] = {}

    def run(self) -> Dict[str, Any]:
        """
        Запускает полный анализ и возвращает словарь с результатами.
        Можно легко расширять — добавляй новые методы и вызывай их здесь.
        """
        self.metrics.update(self._basic_performance())
        self.metrics.update(self._trade_efficiency())
        self.metrics.update(self._risk_metrics())
        self.metrics.update(self._consistency_metrics())
        self.metrics.update(self._signal_quality())
        self.metrics.update(self._mfe_mae_insights())

        summary = self._generate_summary()
        recommendations = self._generate_recommendations()

        return {
            "metrics": self.metrics,
            "summary": summary,
            "recommendations": recommendations,
            "total_trades": len(self.trades),
            "timeframe": self.timeframe
        }

    # ────────────── 🔹 Базовая доходность ──────────────
    def _basic_performance(self) -> Dict[str, float]:
        """Простые метрики из stats или рассчитанные."""
        return {
            "total_return_pct": self.stats.get("total_return", 0.0),
            "win_rate_pct": self.stats.get("win_rate", 0.0),
            "profit_factor": self.stats.get("profit_factor", 0.0),
            "expectancy_per_trade": self.stats.get("expectancy", 0.0),  # средняя прибыль на сделку
        }

    # ────────────── 🔹 Эффективность сделок ──────────────
    def _trade_efficiency(self) -> Dict[str, float]:
        """Метрики по качеству сделок."""
        if not self.trades:
            return {"avg_pnl_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}

        pnl_list = [t["pnl_percent"] for t in self.trades]

        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p < 0]

        return {
            "avg_pnl_pct": np.mean(pnl_list) if pnl_list else 0.0,
            "avg_win_pct": np.mean(wins) if wins else 0.0,
            "avg_loss_pct": np.mean(losses) if losses else 0.0,
            "win_loss_ratio": (np.mean(wins) / abs(np.mean(losses))) if wins and losses else 0.0,
        }

    # ────────────── 🔹 Риски ──────────────
    def _risk_metrics(self) -> Dict[str, float]:
        """Метрики риска и просадок."""
        return {
            "max_drawdown_pct": self.stats.get("max_drawdown", 0.0),
            "volatility_annualized": self.stats.get("volatility", 0.0),
            "sharpe_ratio": self.stats.get("sharpe_ratio", 0.0),
            "sortino_ratio": self.stats.get("sortino_ratio", 0.0),
            "calmar_ratio": self._safe_divide(
                self.stats.get("total_return", 0.0),
                abs(self.stats.get("max_drawdown", -1))
            ),
        }

    # ────────────── 🔹 Стабильность ──────────────
    def _consistency_metrics(self) -> Dict[str, float]:
        """Насколько равномерно идёт прибыль."""
        durations = [t.get("bars_in_trade", 1) for t in self.trades]
        returns = [t["pnl_percent"] for t in self.trades]

        return {
            "avg_trade_duration_bars": np.mean(durations) if durations else 0,
            "std_pnl_pct": np.std(returns) if returns else 0.0,  # волатильность сделок
            "profit_streak_max": self._max_streak([1 if t["pnl_percent"] > 0 else 0 for t in self.trades]),
            "loss_streak_max": self._max_streak([1 if t["pnl_percent"] < 0 else 0 for t in self.trades]),
        }

    # ────────────── 🔹 Качество сигналов ──────────────
    def _signal_quality(self) -> Dict[str, float]:
        """Как сигналы превращаются в сделки."""
        trades_with_signal = [t for t in self.trades if t.get("signal")]
        hit_rate = len(trades_with_signal) / len(self.signals) if self.signals else 0

        return {
            "signal_hit_rate": hit_rate * 100,
            "signals_per_month": self._estimate_frequency(),  # примерная частота
        }

    # ────────────── 🔹 MFE / MAE анализ (потенциал) ──────────────
    def _mfe_mae_insights(self) -> Dict[str, float]:
        """Анализ максимального выигрыша и убытка по каждой сделке."""
        if not self.trades:
            return {"avg_mfe_minus_exit_pct": 0.0, "missed_profit_potential_pct": 0.0}

        mfe_diffs = []
        for trade in self.trades:
            entry = trade["entry_price"]
            mfe = trade.get("mfe_price")
            exit = trade["exit_price"]

            if mfe and entry != 0:
                mfe_pct = (mfe - entry) / entry * 100 * (1 if trade["signal_type"] == "BULLISH" else -1)
                exit_pct = (exit - entry) / entry * 100 * (1 if trade["signal_type"] == "BULLISH" else -1)
                mfe_diffs.append(mfe_pct - exit_pct)

        avg_missed = np.mean(mfe_diffs) if mfe_diffs else 0.0
        return {
            "avg_mfe_minus_exit_pct": avg_missed,
            "missed_profit_potential_pct": max(avg_missed, 0),
        }

    # ────────────── 🔹 Вспомогательные методы ──────────────
    @staticmethod
    def _safe_divide(a: float, b: float, default: float = 0.0) -> float:
        """Безопасное деление на ноль."""
        return a / b if b != 0 else default

    @staticmethod
    def _max_streak(binary_list: List[int]) -> int:
        """Находит максимальную серию подряд (для профита/убытка)."""
        max_streak = current = 0
        for value in binary_list:
            current = current + 1 if value else 0
            max_streak = max(max_streak, current)
        return max_streak

    def _estimate_frequency(self) -> float:
        """Оценка количества сигналов в месяц (примерно)."""
        if not self.signals:
            return 0.0
        time_range_days = (self.signals[-1]["timestamp"] - self.signals[0]["timestamp"]).days
        months = max(time_range_days / 30, 1)
        return len(self.signals) / months

    # ────────────── 🔹 Интерпретация ──────────────
    def _generate_summary(self) -> str:
        """Генерация краткого текстового вывода."""
        tr = self.metrics["total_return_pct"]
        dd = self.metrics["max_drawdown_pct"]
        wr = self.metrics["win_rate_pct"]

        if tr > 15 and dd > -15:
            return "Стратегия прибыльна и низкорискованна."
        elif tr > 10 and dd < -25:
            return "Стратегия прибыльна, но с высокой просадкой."
        elif tr > 0 and wr < 40:
            return "Стратегия зарабатывает на крупных сделках, но редко попадает в тренд."
        else:
            return "Стратегия неэффективна или требует оптимизации."

    def _generate_recommendations(self) -> List[str]:
        """Рекомендации на основе метрик."""
        recs = []

        if self.metrics["max_drawdown_pct"] < -25:
            recs.append("Высокая просадка. Рассмотрите динамический размер позиции или tighter SL.")
        if self.metrics["win_rate_pct"] < 50 and self.metrics["avg_win_pct"] > abs(self.metrics["avg_loss_pct"]) * 2:
            recs.append("Низкий процент побед, но высокий риск-ревард — возможно, стоит фильтровать ложные входы.")
        if self.metrics["missed_profit_potential_pct"] > 2:
            recs.append("Цена часто уходила дальше после выхода — проверьте логику Take Profit.")
        if not recs:
            recs.append("Стратегия сбалансирована. Готова к тестированию на реальном счёте.")

        return recs


