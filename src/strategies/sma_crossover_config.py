"""
SMA Crossover Strategy Configuration V1

Конфигурация параметров для стратегии пересечения цены через SMA.
Поддерживает несколько таймфреймов с разными параметрами.

═══════════════════════════════════════════════════════════════════════════
СТРУКТУРА:
═══════════════════════════════════════════════════════════════════════════

STRATEGY_CONFIG = {
    "global": {
        "initial_balance": float,
        "position_size_percent": float,
        "enable_commission": bool,
        "commission_percent": float,
    },
    "sma_period": int,
    "timeframes": {
        "1h": {
            "tp_percent": float,
            "sl_percent": float,
        },
        "4h": {...},
        "1d": {...},
    }
}

ФУНКЦИИ ДОСТУПА:
- get_strategy_config() -> Dict
- get_timeframe_config(timeframe: str) -> Dict
- get_global_config() -> Dict
- get_sma_period() -> int

═══════════════════════════════════════════════════════════════════════════
"""

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 ГЛОБАЛЬНАЯ КОНФИГУРАЦИЯ СТРАТЕГИИ
# ═══════════════════════════════════════════════════════════════════════════

STRATEGY_CONFIG = {
    # ───────────────────────────────────────────────────────────────────────
    # ГЛОБАЛЬНЫЕ ПАРАМЕТРЫ
    # ───────────────────────────────────────────────────────────────────────
    "global": {
        "initial_balance": 100000.0,  # Начальный баланс (₽)
        "position_size_percent": 100.0,  # % от баланса на сделку (100 = всё)
        "enable_commission": False,  # Считать ли комиссии
        "commission_percent": 0.00,  # Комиссия в % (T-Bank: 0.05%)
        "description": "Global strategy settings",
    },

    # ───────────────────────────────────────────────────────────────────────
    # SMA ПАРАМЕТР (БАЗОВЫЙ)
    # ───────────────────────────────────────────────────────────────────────
    "sma_period": 50,  # Период SMA для всех таймфреймов
    "sma_description": "Simple Moving Average period",

    # ───────────────────────────────────────────────────────────────────────
    # ТАЙМФРЕЙМ-СПЕЦИФИЧНЫЕ ПАРАМЕТРЫ
    # ───────────────────────────────────────────────────────────────────────
    "timeframes": {
        # ───────────────────────────────────────────────────────────────────
        # 1H ТАЙМФРЕЙМ
        # ───────────────────────────────────────────────────────────────────
        "1h": {
            "tp_percent": 1.1,  # Take Profit в %
            "sl_percent": 0.6,  # Stop Loss в %
            "description": "1-hour timeframe settings",
        },

        # ───────────────────────────────────────────────────────────────────
        # 4H ТАЙМФРЕЙМ
        # ───────────────────────────────────────────────────────────────────
        "4h": {
            "tp_percent": 1.7,
            "sl_percent": 1.0,
            "description": "4-hour timeframe settings",
        },

        # ───────────────────────────────────────────────────────────────────
        # 1D ТАЙМФРЕЙМ
        # ───────────────────────────────────────────────────────────────────
        "1d": {
            "tp_percent": 3.0,
            "sl_percent": 1.8,
            "description": "1-day timeframe settings",
        },

        # Дополнительные таймфреймы (опционально)
        "30m": {
            "tp_percent": 1.0,
            "sl_percent": 0.5,
            "description": "30-minute timeframe settings",
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 📋 ФУНКЦИИ ДОСТУПА К КОНФИГУРАЦИИ
# ═══════════════════════════════════════════════════════════════════════════


def get_strategy_config() -> dict:
    """
    Получить полную конфигурацию стратегии.

    Returns:
        dict: Полная конфигурация STRATEGY_CONFIG

    Example:
        >>> config = get_strategy_config()
        >>> print(config["global"]["initial_balance"])
        100000.0
    """
    return STRATEGY_CONFIG.copy()


def get_global_config() -> dict:
    """
    Получить глобальные параметры стратегии.

    Returns:
        dict: Параметры из "global" ключа

    Example:
        >>> global_cfg = get_global_config()
        >>> balance = global_cfg["initial_balance"]
    """
    return STRATEGY_CONFIG.get("global", {})


def get_sma_period() -> int:
    """
    Получить период SMA для всех таймфреймов.

    Returns:
        int: Период SMA

    Example:
        >>> period = get_sma_period()
        >>> # 50
    """
    return STRATEGY_CONFIG.get("sma_period", 50)


def get_timeframe_config(timeframe: str) -> dict:
    """
    Получить конфигурацию для конкретного таймфрейма.

    Args:
        timeframe: Код таймфрейма (e.g., "1h", "4h", "1d")

    Returns:
        dict: Конфигурация таймфрейма или пустой словарь если не найден

    Example:
        >>> cfg = get_timeframe_config("1h")
        >>> tp = cfg.get("tp_percent")
        >>> sl = cfg.get("sl_percent")
    """
    return STRATEGY_CONFIG.get("timeframes", {}).get(timeframe, {})


def get_tp_sl(timeframe: str) -> dict:
    """
    Получить TP и SL для таймфрейма (удобная функция).

    Args:
        timeframe: Код таймфрейма (e.g., "1h", "4h", "1d")

    Returns:
        dict: {"tp_percent": float, "sl_percent": float}

    Example:
        >>> tp_sl = get_tp_sl("4h")
        >>> print(f"TP: {tp_sl['tp_percent']}%, SL: {tp_sl['sl_percent']}%")
        TP: 2.5%, SL: 1.5%
    """
    cfg = get_timeframe_config(timeframe)
    return {
        "tp_percent": cfg.get("tp_percent", 1.5),
        "sl_percent": cfg.get("sl_percent", 1.0),
    }


def get_initial_balance() -> float:
    """
    Получить начальный баланс.

    Returns:
        float: Начальный баланс в рублях

    Example:
        >>> balance = get_initial_balance()
        >>> # 100000.0
    """
    return get_global_config().get("initial_balance", 100000.0)


def get_position_size_percent() -> float:
    """
    Получить размер позиции в процентах от баланса.

    Returns:
        float: Процент баланса для одной сделки

    Example:
        >>> size = get_position_size_percent()
        >>> # 100.0
    """
    return get_global_config().get("position_size_percent", 100.0)


def get_all_timeframes() -> list:
    """
    Получить список всех доступных таймфреймов.

    Returns:
        list: Список кодов таймфреймов

    Example:
        >>> tfs = get_all_timeframes()
        >>> # ['1h', '4h', '1d', '30m']
    """
    return list(STRATEGY_CONFIG.get("timeframes", {}).keys())


# ═══════════════════════════════════════════════════════════════════════════
# 🔨 КОНФИГУРАЦИЯ ПРОБОЕВ (BREAKOUT RULES)
# ═══════════════════════════════════════════════════════════════════════════

BREAKOUT_CONFIG = {
    "enabled": True,  # Включить ли проверку пробоев
    "description": "Breakout validation rules for SMA crossover signals",
    
    # ───────────────────────────────────────────────────────────────────────
    # ПРАВИЛА ВАЛИДАЦИИ ПРОБОЯ
    # ───────────────────────────────────────────────────────────────────────
    "validation_rules": {
        # Проверяем свечу с сигналом (candle_1)
        "candle_1": {
            "must_be_bullish_for_bullish_signal": True,  # BULLISH сигнал → свеча должна быть бычья
            "must_be_bearish_for_bearish_signal": True,  # BEARISH сигнал → свеча должна быть медвежья
            "description": "Свеча пробоя должна совпадать по направлению с сигналом"
        },
        
        # Проверяем свечу ДО входа (candle_2 - следующая после сигнала)
        "candle_2": {
            "must_confirm_direction": True,  # Свеча 2 должна быть того же направления
            "description": "Подтверждающая свеча должна быть того же направления, что и свеча 1"
        },
        
        # Свеча входа (candle_3)
        "candle_3": {
            "enter_on_open": True,  # Входим по открытию 3-й свечи
            "description": "Вход происходит по цене открытия 3-й свечи"
        }
    }
}


def get_breakout_config() -> dict:
    """
    Получить полную конфигурацию пробоев.

    Returns:
        dict: Конфигурация BREAKOUT_CONFIG

    Example:
        >>> config = get_breakout_config()
        >>> if config.get("enabled"):
        ...     print("Breakout validation enabled")
    """
    return BREAKOUT_CONFIG.copy()


def is_breakout_enabled() -> bool:
    """
    Проверить, включены ли проверки пробоев.

    Returns:
        bool: True если пробои включены

    Example:
        >>> if is_breakout_enabled():
        ...     validate_breakout()
    """
    return BREAKOUT_CONFIG.get("enabled", False)


def get_breakout_rules() -> dict:
    """
    Получить правила валидации пробоев.

    Returns:
        dict: Словарь с правилами для candle_1, candle_2, candle_3

    Example:
        >>> rules = get_breakout_rules()
        >>> rules["candle_1"]["must_be_bullish_for_bullish_signal"]
        True
    """
    return BREAKOUT_CONFIG.get("validation_rules", {})



def toggle_breakout(enabled: bool) -> None:
    """
    Включить/выключить проверку пробоев.

    Args:
        enabled: True для включения, False для отключения

    Example:
        >>> toggle_breakout(False)  # Отключить пробои
    """
    BREAKOUT_CONFIG["enabled"] = enabled
    logger_msg = "enabled" if enabled else "disabled"
    print(f"✓ Breakout validation {logger_msg}")