"""
SMA Crossover Strategy Configuration V2

Конфигурация параметров для стратегии пересечения цены через SMA.
Адаптирована для работы с новой архитектурой BaseStrategy.

═══════════════════════════════════════════════════════════════════════════
"""

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 ДЕФОЛТНАЯ КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # Глобальные параметры
    "initial_balance": 450000.0,
    "position_size_percent": 100.0,
    "allow_short": False,
    
    # SMA параметры
    "sma_period": 50,
    
    # TP/SL по таймфреймам
    "timeframes": {
        "1h": {"tp_percent": 1.2, "sl_percent": 0.7},
        "4h":  {"tp_percent": 3.5, "sl_percent": 1.0}, #{"tp_percent": 2.7, "sl_percent": 1.4},
        "1d": {"tp_percent": 3.5, "sl_percent": 2.6},
        "30m": {"tp_percent": 1.0, "sl_percent": 0.6},
    },
    
    # Правила пробоев (опционально)
    "breakout": {
        "enabled": False,  # Отключено по умолчанию
        "candle_1_must_match_direction": True,
        "candle_2_must_confirm": True,
    },
    # ═══════════════════════════════════════════════════════════════════
    # 🔍 ВАЛИДАЦИЯ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════
    "validation": {
        "enabled": True,  # Включить валидацию
        "mode": "custom",  # "default" | "strict" | "custom"
        
        # Настройки для custom режима
        "custom_rules": {
            "candle_direction": True,      # Проверка направления свечи пробоя
            "confirmation": False,           # Подтверждение следующей свечой
            "min_breakout_distance": 0.2,  # Минимальная сила пробоя (%)
        }
    },
    # ═══════════════════════════════════════════════════════
    # 📈 ФИЛЬТР ТРЕНДА
    # ═══════════════════════════════════════════════════════
    "trend_filter": {
        "enabled": True,
        "sma_period": 200,  # SMA для определения тренда
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 📋 ФУНКЦИИ ДОСТУПА
# ═══════════════════════════════════════════════════════════════════════════

def get_default_config() -> dict:
    """
    Получить дефолтную конфигурацию стратегии.
    
    Returns:
        dict: Полная конфигурация с дефолтными значениями
    """
    return DEFAULT_CONFIG.copy()


def get_config(timeframe: str, custom_config: dict = None) -> dict:
    """
    Получить конфигурацию для конкретного таймфрейма.
    
    Args:
        timeframe: Таймфрейм (e.g., "1h", "4h")
        custom_config: Кастомные параметры (опционально)
    
    Returns:
        dict: Конфигурация с параметрами для таймфрейма
    """
    config = get_default_config()
    
    # Применяем кастомные параметры
    if custom_config:
        config.update(custom_config)
    
    # Добавляем TP/SL для конкретного таймфрейма
    tf_config = config["timeframes"].get(timeframe, {})
    config["tp_percent"] = tf_config.get("tp_percent", 1.5)
    config["sl_percent"] = tf_config.get("sl_percent", 1.0)
    
    return config
