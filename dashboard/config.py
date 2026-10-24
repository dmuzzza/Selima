"""
Общие константы и маппинги для Dashboard.
Вынесено из app_dashboard.py для устранения дублирования.
"""

from src.market_data.timeframe import Timeframe

# ═══════════════════════════════════════════════════════════════
# TIMEFRAME MAP — единый маппинг строк → Timeframe enum
# (ранее дублировался в 4 местах)
# ═══════════════════════════════════════════════════════════════
TIMEFRAME_MAP: dict[str, Timeframe] = {
    "30m": Timeframe.M30,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
}

# Обратный маппинг для label
TIMEFRAME_LABELS: dict[str, str] = {
    "30m": "30 minutes",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}

# ═══════════════════════════════════════════════════════════════
# LEVELS_CONFIG — параметры уровней по ТФ (для обратной совместимости)
# ═══════════════════════════════════════════════════════════════
LEVELS_CONFIG: dict[str, dict] = {
    "30m": {"lookback_candles": 200, "clustering_range": 0.005, "min_touches": 2},
    "1h": {"lookback_candles": 150, "clustering_range": 0.01, "min_touches": 2},
    "4h": {"lookback_candles": 100, "clustering_range": 0.02, "min_touches": 2},
    "1d": {"lookback_candles": 50, "clustering_range": 0.05, "min_touches": 1},
}

# ═══════════════════════════════════════════════════════════════
# DEFAULT BALANCE — используется, если не удаётся получить из стратегии
# ═══════════════════════════════════════════════════════════════
DEFAULT_INITIAL_BALANCE: float = 5000.0

# ═══════════════════════════════════════════════════════════════
# Минимальное количество свечей для запуска стратегии
# ═══════════════════════════════════════════════════════════════
MIN_CANDLES_FOR_STRATEGY: int = 50
