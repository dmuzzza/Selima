"""
Dual MA Crossover Strategy Configuration

Конфигурация для стратегии пересечения SMA50 × MA Adaptive.

═══════════════════════════════════════════════════════════════════════════
"""

# ═══════════════════════════════════════════════════════════════════════════
# 🔧 ДЕФОЛТНАЯ КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # Глобальные параметры
    "initial_balance": 50000.0,
    "position_size_percent": 100.0,
    "allow_short": True,
    
    # MA параметры
    "sma_period": 50,  # Быстрая SMA
    "adaptive_period": 14,  # MA Adaptive период

    # EMA фильтр (трендовый фильтр)
    "use_ema_filter": True,    # Включить EMA 200 фильтр
    "ema_period": 200,         # Период EMA для фильтра

    # Фильтр отклонения от MA Adaptive
    "max_adaptive_deviation_percent": 1.2,  # Макс. отклонение цены входа от MA Adaptive, %
    
    # TP/SL по таймфреймам и инструментам
    # Формат: {<timeframe>: {"default": {tp, sl}, "<TICKER>": {tp, sl}, ...}}
    "timeframes": {
        "4h": {
            # "default": {"tp_percent": 3.5, "sl_percent": 1.4},
            "AFKS":       {"tp_percent": 4.7, "sl_percent": 1.1},
            "AFLT":       {"tp_percent": 2.3, "sl_percent": 1.4},
            "CHMF":       {"tp_percent": 5.1, "sl_percent": 3.8},
            "CNYRUB_TOM": {"tp_percent": 2.4, "sl_percent": 1.0},
            "CRU6":       {"tp_percent": 3.8, "sl_percent": 1.0},
            "EUTR":       {"tp_percent": 5.1, "sl_percent": 3.9},
            "FLOT":       {"tp_percent": 3.9, "sl_percent": 3.6},
            "GAZP":       {"tp_percent": 2.8, "sl_percent": 1.0},
            "GLDRUB_TOM": {"tp_percent": 2.5, "sl_percent": 1.1},
            "LKOH":       {"tp_percent": 3.0, "sl_percent": 1.3},
            "MGNT":       {"tp_percent": 4.1, "sl_percent": 2.1},
            "MOEX":       {"tp_percent": 3.9, "sl_percent": 1.0},
            "NVTK":       {"tp_percent": 2.9, "sl_percent": 2.2},
            "OZON":       {"tp_percent": 4.8, "sl_percent": 3.8},
            "PIKK":       {"tp_percent": 2.2, "sl_percent": 1.9},
            "POSI":       {"tp_percent": 4.4, "sl_percent": 3.7},
            "SBER":       {"tp_percent": 2.3, "sl_percent": 2.2},
            "SNGSP":      {"tp_percent": 2.8, "sl_percent": 2.2},
            "T":          {"tp_percent": 3.8, "sl_percent": 1.2},
            "VTBR":       {"tp_percent": 2.2, "sl_percent": 1.4},
            "X5":         {"tp_percent": 4.5, "sl_percent": 1.5},
        },
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


def get_config(timeframe: str, custom_config: dict = None, ticker: str = None) -> dict:
    """
    Получить конфигурацию для конкретного таймфрейма и инструмента.

    Args:
        timeframe: Таймфрейм (e.g., "4h")
        custom_config: Кастомные параметры (опционально).
                       Если переданы tp_percent/sl_percent — они не перезаписываются.
        ticker: Тикер инструмента для подбора индивидуальных TP/SL.
                Если не указан — берутся default значения таймфрейма.

    Returns:
        dict: Конфигурация с параметрами
    """
    config = get_default_config()

    # Применяем кастомные параметры (могут содержать tp_percent/sl_percent)
    if custom_config:
        config.update(custom_config)

    # TP/SL из конфига: ищем сначала под инструмент, потом default
    tf_config = config["timeframes"].get(timeframe, {})
    if isinstance(tf_config, dict):
        tf_default = tf_config.get("default", {})
        instr_config = tf_config.get(ticker, {}) if ticker else {}
    else:
        # Старый формат (без default/instruments) — оставляем как fallback
        tf_default = tf_config
        instr_config = {}

    # Применяем ТОЛЬКО если не заданы явно в custom_config
    if "tp_percent" not in (custom_config or {}):
        config["tp_percent"] = instr_config.get("tp_percent",
                                                  tf_default.get("tp_percent", 1.5))
    if "sl_percent" not in (custom_config or {}):
        config["sl_percent"] = instr_config.get("sl_percent",
                                                tf_default.get("sl_percent", 1.0))

    return config
