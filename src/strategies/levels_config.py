"""
Конфигурация для детектора уровней поддержки и сопротивления.

Система категорийных уровней с разными параметрами:
- SWING: макро-уровни (3+ касания)
- INTRADAY: среднесрочные уровни (2+ касания)
- MICROSTRUCTURE: микро-уровни (1+ касание)

Параметры:
- clustering_tolerance: радиус группировки цен (по таймфреймам)
- min_touches: минимум касаний для определения уровня
- max_breakouts_allowed: количество пробоев перед "смертью" уровня
- lookback_candles: сколько свечей анализируем для поиска уровней
- priority: приоритет отрисовки (выше = виднее)
"""

# ═══════════════════════════════════════════════════════════════
# TOLERANCE ПО ТАЙМФРЕЙМАМ (Ценовое окно для кластеризации)
# ═══════════════════════════════════════════════════════════════
# Логика: разные ТФ имеют разные "типичные" движения
# На 1m оно меньше, на 1d больше
TIMEFRAME_CLUSTERING_TOLERANCE = {
    "1m": 0.003,    # ±0.3% - очень узко, микро-движения
    "5m": 0.005,    # ±0.5% - маленькие движения
    "15m": 0.008,   # ±0.8% - средние
    "1h": 0.012,    # ±1.2% - часовой (вместо 1.5%!)
    "4h": 0.007,    # ±2.0% - большой ТФ
    "1d": 0.011,    # ±3.0% - дневной
    "1w": 0.050,    # ±5.0% - недельный
}

# ═══════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ ПО КАТЕГОРИЯМ
# ═══════════════════════════════════════════════════════════════
LEVELS_CATEGORIES_CONFIG = {
    "SWING": {
        "enabled": False,
        "description": "Макро-уровни (долгоживущие, много касаний)",
        "min_touches": 3,           # Минимум 3 касания
        "max_breakouts_allowed": 2, # После 2 пробоев уровень умирает
        "lookback_candles": 300,    # Ищем уровни в последних 300 свечах
        "priority": 10,             # Приоритет отрисовки (выше = виднее)
        "color": "red",             # Красный цвет на графике
        "line_width": 2,            # Толщина линии
        "opacity": 1.0,             # Полная видимость
        "line_dash": "solid",       # Сплошная линия
    },
    
    "INTRADAY": {
        "enabled": False,
        "description": "Среднесрочные уровни (модерные касания)",
        "min_touches": 2,           # 2 касания
        "max_breakouts_allowed": 2, # После 1 пробоя умирает
        "lookback_candles": 100,    # В последних 100 свечах
        "priority": 5,              # Видна в середине
        "color": "orange",          # Оранжевый
        "line_width": 1.5,
        "opacity": 0.8,
        "line_dash": "solid",
    },
    
    "MICROSTRUCTURE": {
        "enabled": True,
        "description": "Микро-уровни (одиночные касания, недолгожилые)",
        "min_touches": 1,           # Даже одно касание
        "max_breakouts_allowed": 0, # Один пробой → готово
        "lookback_candles": 50,     # В последних 50 свечах
        "priority": 1,              # Видна в фоне
        "color": "gray",            # Серый
        "line_width": 1,
        "opacity": 0.4,
        "line_dash": "dash",        # Пунктир
    }
}

# ═══════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ ПРОБОЯ
# ═══════════════════════════════════════════════════════════════
BREAKOUT_CONFIG = {
    # Тип пробоя: "close" (закрытие тела) или "wick" (wick)
    "breakout_type": "close",
    
    # Допуск за уровень для пробоя (в процентах)
    # Например, если уровень 100, то пробой > 100.1 (0.1%)
    "breakout_tolerance": 0.01,  # 0.1%
    
    # Требует ли пробой противоположного типа свечи?
    # True: пробой вверх = зелёная свеча (close > open)
    # True: пробой вниз = красная свеча (close < open)
    "breakout_requires_opposite_candle": True,
}

# ═══════════════════════════════════════════════════════════════
# ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ TOLERANCE ПО ТАЙМФРЕЙМУ
# ═══════════════════════════════════════════════════════════════
def get_clustering_tolerance(timeframe: str) -> float:
    """
    Получить tolerance для кластеризации уровней по таймфрейму.
    
    Args:
        timeframe: Строка таймфрейма (e.g., "1h", "4h", "1d")
    
    Returns:
        float: Tolerance в долях (0.01 = 1%)
    
    Examples:
        >>> get_clustering_tolerance("1h")
        0.012
        
        >>> get_clustering_tolerance("1d")
        0.030
        
        >>> get_clustering_tolerance("unknown_tf")
        0.015  # Default
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if timeframe in TIMEFRAME_CLUSTERING_TOLERANCE:
        tolerance = TIMEFRAME_CLUSTERING_TOLERANCE[timeframe]
        logger.debug(f"✅ Clustering tolerance for {timeframe}: {tolerance*100:.2f}%")
        return tolerance
    else:
        logger.warning(
            f"⚠️  Timeframe '{timeframe}' not found in TIMEFRAME_CLUSTERING_TOLERANCE. "
            f"Using default 1.5%. Known: {list(TIMEFRAME_CLUSTERING_TOLERANCE.keys())}"
        )
        return 0.012  # Default 1.5%


def get_category_config(category: str) -> dict:
    """
    Получить конфигурацию для конкретной категории уровней.
    
    Args:
        category: Категория ("SWING", "INTRADAY", "MICROSTRUCTURE")
    
    Returns:
        dict: Полная конфигурация категории
    
    Examples:
        >>> cfg = get_category_config("SWING")
        >>> cfg["min_touches"]
        3
        
        >>> cfg["priority"]
        10
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if category not in LEVELS_CATEGORIES_CONFIG:
        logger.warning(
            f"⚠️  Category '{category}' not found. Using SWING as default. "
            f"Available: {list(LEVELS_CATEGORIES_CONFIG.keys())}"
        )
        category = "SWING"
    
    config = LEVELS_CATEGORIES_CONFIG[category].copy()
    logger.debug(f"✅ Loaded config for category: {category}")
    return config


def get_all_categories() -> list:
    """Получить все доступные категории уровней"""
    return list(LEVELS_CATEGORIES_CONFIG.keys())
