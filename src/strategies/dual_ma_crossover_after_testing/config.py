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

    TP/SL здесь не подставляются: они берутся из `config/tp_sl.json` по
    `(code, timeframe, ticker)` в `BaseStrategy` (T3-4, ADR-019). Явные
    `tp_percent`/`sl_percent` в `custom_config` имеют приоритет — так передаёт
    сетку walk-forward.

    Args:
        timeframe: Таймфрейм (e.g., "4h")
        custom_config: Кастомные параметры (опционально)
        ticker: Тикер инструмента (сохраняется в конфиге для выбора TP/SL)

    Returns:
        dict: Конфигурация с параметрами
    """
    config = get_default_config()

    if custom_config:
        config.update(custom_config)

    if ticker:
        config["ticker"] = ticker

    return config
