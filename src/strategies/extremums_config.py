"""
Конфигурация для детектора экстремумов (V-образные пики и впадины).

Многоуровневая система калибровки по таймфреймам.

Параметры:
- window_size: количество свечей слева и справа для проверки
- threshold_percent: минимальная разница в процентах
- use_high_low: если True - используует high/low, если False - open/close

Логика:
1. Если передан конкретный timeframe (e.g., "1h") → используем его параметры
2. Если timeframe не известен → используем DEFAULT (дефолт для всех)
3. Если параметр отсутствует в ТФ конфиге → берём из DEFAULT
"""

# ═══════════════════════════════════════════════════════════════
# ДЕФОЛТНЫЕ ПАРАМЕТРЫ (используются как фолбэк)
# ═══════════════════════════════════════════════════════════════
DEFAULT_EXTREMUMS_CONFIG = {
    "enabled": True,
    "window_size": 7,           # Стандартное окно для большинства ТФ
    "threshold_percent": 1.0,   # Базовый порог 1%
    "use_high_low": False,      # Используем OPEN/CLOSE по умолчанию
}

# ═══════════════════════════════════════════════════════════════
# ПАРАМЕТРЫ ПО ТАЙМФРЕЙМАМ
# ═══════════════════════════════════════════════════════════════
TIMEFRAME_EXTREMUMS_CONFIG = {
    # МАЛЫЕ ТАЙМФРЕЙМЫ (больше шума, нужен мягче фильтр)
    "1m": {
        "window_size": 5,           # Меньше окно для респонсивности
        "threshold_percent": 0.5,   # Мягче порог (только явные экстремумы)
        "use_high_low": True,       # На малых ТФ high/low важнее
    },
    "5m": {
        "window_size": 5,
        "threshold_percent": 0.6,
        "use_high_low": True,
    },
    "15m": {
        "window_size": 6,
        "threshold_percent": 0.7,
        "use_high_low": True,
    },
    
    # СРЕДНИЕ ТАЙМФРЕЙМЫ (хороший баланс)
    "1h": {
        "window_size": 7,           # Стандартное окно
        "threshold_percent": 0.5,   # Слегка мягче дефолта (ловит скрытые экстремумы)
        "use_high_low": False,      # На часовом достаточно open/close
    },
    "4h": {
        "window_size": 7,
        "threshold_percent": 0.9,   # Дефолт (как в DEFAULT)
        "use_high_low": False,
    },
    
    # БОЛЬШИЕ ТАЙМФРЕЙМЫ (очень чистые, нужен жёсткий фильтр)
    "1d": {
        "window_size": 8,           # Больше окно (ищем более значимые экстремумы)
        "threshold_percent": 1.0,   # Жёстче порог (только очень явные)
        "use_high_low": False,
    },
    "1w": {
        "window_size": 9,
        "threshold_percent": 1.5,   # Очень жёсткий фильтр
        "use_high_low": False,
    },
}


def get_extremums_config(timeframe: str = None) -> dict:
    """
    Получить конфигурацию для конкретного таймфрейма.
    
    Логика:
    1. Если timeframe есть в TIMEFRAME_EXTREMUMS_CONFIG → используем его
    2. Иначе → используем DEFAULT и логируем предупреждение
    3. Все отсутствующие параметры дополняются из DEFAULT
    
    Args:
        timeframe: Строка таймфрейма (e.g., "1h", "4h", "1d")
                   Если None или неизвестен → используем DEFAULT
    
    Returns:
        dict: Полная конфигурация для LevelsDetector
    
    Examples:
        >>> get_extremums_config("1h")
        {'enabled': True, 'window_size': 7, 'threshold_percent': 0.8, 'use_high_low': False}
        
        >>> get_extremums_config("1m")
        {'enabled': True, 'window_size': 5, 'threshold_percent': 0.5, 'use_high_low': True}
        
        >>> get_extremums_config("unknown_tf")
        # Логирует предупреждение, возвращает DEFAULT
        {'enabled': True, 'window_size': 7, 'threshold_percent': 1.0, 'use_high_low': False}
    """
    
    import logging
    logger = logging.getLogger(__name__)
    
    # Начинаем с копии DEFAULT (никогда не мутируем оригинал!)
    config = DEFAULT_EXTREMUMS_CONFIG.copy()
    
    # Если timeframe не передан → возвращаем DEFAULT
    if not timeframe:
        return config
    
    # Если timeframe существует в конфиге → мержим параметры
    if timeframe in TIMEFRAME_EXTREMUMS_CONFIG:
        tf_config = TIMEFRAME_EXTREMUMS_CONFIG[timeframe]
        config.update(tf_config)
        logger.debug(f"✅ Loaded extremums config for timeframe: {timeframe}")
    else:
        # Неизвестный таймфрейм — логируем и используем DEFAULT
        logger.warning(
            f"⚠️  Timeframe '{timeframe}' not found in TIMEFRAME_EXTREMUMS_CONFIG. "
            f"Using DEFAULT config. Known timeframes: {list(TIMEFRAME_EXTREMUMS_CONFIG.keys())}"
        )
    
    return config


# ═══════════════════════════════════════════════════════════════
# УДОБНЫЕ КОНСТАНТЫ ДЛЯ БЫСТРОГО ДОСТУПА
# ═══════════════════════════════════════════════════════════════

# Группы таймфреймов
SMALL_TIMEFRAMES = ["1m", "5m", "15m"]
MEDIUM_TIMEFRAMES = ["1h", "4h"]
LARGE_TIMEFRAMES = ["1d", "1w"]
ALL_TIMEFRAMES = SMALL_TIMEFRAMES + MEDIUM_TIMEFRAMES + LARGE_TIMEFRAMES
