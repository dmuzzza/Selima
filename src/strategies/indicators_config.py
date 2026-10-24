"""
Конфигурация параметров технических индикаторов (Indicators Config V1).

Централизованное хранилище параметров для всех индикаторов:
- SMA: Простая скользящая средняя
- EMA: Экспоненциальная скользящая средняя
- RSI: Индекс относительной силы
- MACD: Moving Average Convergence Divergence
- Bollinger Bands: Полосы Боллинджера
- ATR: Average True Range
- Stochastic: Стохастический осциллятор

Каждый индикатор независимо конфигурируется и может быть включён/отключен.

═══════════════════════════════════════════════════════════════
СТРУКТУРА:

INDICATORS_CONFIG = {
    "SMA": {
        "enabled": bool,
        "period": int,
        ...другие параметры
    },
    "EMA": {...},
    ...
}

Функции доступа:
- get_indicator_config(name) -> Dict  : Получить конфиг одного индикатора
- get_all_indicators() -> List[str]   : Получить список всех индикаторов
═══════════════════════════════════════════════════════════════
"""


# ═══════════════════════════════════════════════════════════════
# 🔧 КОНФИГУРАЦИЯ ИНДИКАТОРОВ
# ═══════════════════════════════════════════════════════════════

INDICATORS_CONFIG = {
    # ───────────────────────────────────────────────────────────
    # SMA - Простая скользящая средняя
    # ───────────────────────────────────────────────────────────
    "SMA": {
        "enabled": True,
        "period": 50,  # Стандартный период
        "description": "Простая скользящая средняя",
    },

    "MA_ADAPTIVE": {
        "enabled": True,
        "period": 3,
        "description": "Adaptive Moving Average (EMA-based with dynamic period)"
    },

    # ───────────────────────────────────────────────────────────
    # EMA - Экспоненциальная скользящая средняя
    # ───────────────────────────────────────────────────────────
    "EMA": {
        "enabled": False,
        "period": 50,  # Стандартный период EMA
        "description": "Экспоненциальная скользящая средняя",
    },

    # ───────────────────────────────────────────────────────────
    # RSI - Индекс относительной силы (0-100)
    # ───────────────────────────────────────────────────────────
    "RSI": {
        "enabled": False,
        "period": 14,  # Классический период RSI
        "overbought": 70,  # Уровень перекупленности
        "oversold": 30,  # Уровень перепроданности
        "description": "Индекс относительной силы",
    },

    # ───────────────────────────────────────────────────────────
    # MACD - Moving Average Convergence Divergence
    # ───────────────────────────────────────────────────────────
    "MACD": {
        "enabled": False,
        "fast_period": 12,  # Быстрая EMA
        "slow_period": 26,  # Медленная EMA
        "signal_period": 9,  # Signal line (EMA от MACD)
        "description": "Moving Average Convergence Divergence",
    },

    # ───────────────────────────────────────────────────────────
    # Bollinger Bands - Полосы Боллинджера
    # ───────────────────────────────────────────────────────────
    "BOLLINGER_BANDS": {
        "enabled": False,
        "period": 20,  # Период SMA для средней линии
        "std_dev_multiplier": 2.0,  # Множитель стандартного отклонения
        "description": "Полосы Боллинджера (волатильность)",
    },

    # ───────────────────────────────────────────────────────────
    # ATR - Average True Range
    # ───────────────────────────────────────────────────────────
    "ATR": {
        "enabled": False,
        "period": 14,  # Классический период ATR
        "description": "Средний истинный диапазон (волатильность)",
    },

    # ───────────────────────────────────────────────────────────
    # Stochastic - Стохастический осциллятор
    # ───────────────────────────────────────────────────────────
    "STOCHASTIC": {
        "enabled": False,
        "period": 14,  # Период поиска высот/низов
        "smooth_k": 3,  # Сглаживание %K
        "smooth_d": 3,  # Сглаживание %D (Signal line)
        "overbought": 80,  # Уровень перекупленности
        "oversold": 20,  # Уровень перепроданности
        "description": "Стохастический осциллятор",
    },
}


# ═══════════════════════════════════════════════════════════════
# 📋 ФУНКЦИИ ДОСТУПА К КОНФИГУ
# ═══════════════════════════════════════════════════════════════

def get_indicator_config(indicator_name: str) -> dict:
    """
    Получить конфигурацию конкретного индикатора.

    Args:
        indicator_name: Имя индикатора (e.g., "SMA", "RSI", "MACD")

    Returns:
        dict: Конфигурация индикатора или пустой словарь если не найден

    Example:
        >>> config = get_indicator_config("RSI")
        >>> period = config.get("period", 14)
    """
    return INDICATORS_CONFIG.get(indicator_name, {})


def get_all_indicators() -> list:
    """
    Получить список всех доступных индикаторов.

    Returns:
        list: Список имён индикаторов

    Example:
        >>> indicators = get_all_indicators()
        >>> # ['SMA', 'EMA', 'RSI', 'MACD', 'BOLLINGER_BANDS', 'ATR', 'STOCHASTIC']
    """
    return list(INDICATORS_CONFIG.keys())


def get_enabled_indicators() -> list:
    """
    Получить список включённых индикаторов.

    Returns:
        list: Список имён активных индикаторов

    Example:
        >>> enabled = get_enabled_indicators()
    """
    return [name for name, config in INDICATORS_CONFIG.items() if config.get("enabled", True)]


def is_indicator_enabled(indicator_name: str) -> bool:
    """
    Проверить, включён ли индикатор.

    Args:
        indicator_name: Имя индикатора

    Returns:
        bool: True если включён, False если отключён или не существует

    Example:
        >>> if is_indicator_enabled("RSI"):
        ...     calculate_rsi()
    """
    config = get_indicator_config(indicator_name)
    return config.get("enabled", False)
