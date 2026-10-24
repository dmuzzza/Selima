"""
Candle Patterns Strategy Configuration

Конфигурация параметров для стратегии на основе свечных паттернов.
"""

# ═══════════════════════════════════════════════════════════════════════════
# ДЕФОЛТНАЯ КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    # Глобальные параметры
    "initial_balance": 1000000.0,
    "position_size_percent": 100.0,

    # Размер окна для анализа паттернов (сколько свечей передаётся детектору)
    "pattern_window_size": 3,

    # TP/SL по таймфреймам
    "timeframes": {
        "1h":  {"tp_percent": 1.5, "sl_percent": 1.0},
        "4h":  {"tp_percent": 3.5, "sl_percent": 1.0},
        "1d":  {"tp_percent": 3.7, "sl_percent": 2.8},
        "30m": {"tp_percent": 1.0, "sl_percent": 0.7},
    },

    # ═══════════════════════════════════════════════════════════════════
    # 🕯️ ПАТТЕРНЫ — какие паттерны включены и их параметры
    # ═══════════════════════════════════════════════════════════════════
    "patterns": {
        "doji": {
            "enabled": False,
            "body_threshold": 0.07,       # тело ≤ 7% от диапазона
        },
        "hammer": {
            "enabled": False,
            "shadow_ratio": 2.0,          # нижняя тень ≥ 2× тела
            "upper_shadow_max": 0.1,      # верхняя тень ≤ 10% диапазона
        },
        "shooting_star": {
            "enabled": False,
            "shadow_ratio": 2.0,          # верхняя тень ≥ 2× тела
            "lower_shadow_max": 0.1,      # нижняя тень ≤ 10% диапазона
        },
        "evening_star_doji": {
            "enabled": True,
            "filters": {
                "body_ratio": {"enabled": False},
                "candle_size": {"enabled": False},
                "volume": {"enabled": False},
                "ma_trend": {"enabled": False},
                "ma_trend100": {"enabled": False},
                "rsi": {"enabled": False},
            },
        },
        "engulfing": {
            "enabled": True,

            # ═══════════════════════════════════════════════════════════
            # ФИЛЬТРЫ ПОГЛОЩЕНИЯ (подключаются как секции filters.{имя})
            # Каждый фильтр можно включить/выключить и настроить пороги
            # ═══════════════════════════════════════════════════════════
            "filters": {
                "body_ratio": {
                    "enabled": True,
                    "min_ratio": 1.5,    # тело поглощающей ≥ 1.5× тела предыдущей
                },
                "candle_size": {
                    "enabled": True,
                    "min_ratio": 1.5,    # размер свечи ≥ 1.5× среднего размера
                    "lookback": 100,     # за последние 100 свечей
                },
                "volume": {
                    "enabled": True,
                    "min_ratio": 1.5,    # объём ≥ 1.5× среднего
                    "lookback": 20,      # за последние 20 свечей
                },
                "ma_trend": {
                    "enabled": True,
                    "period": 15,         # период скользящей средней
                    "lookback": 15,      # анализировать направление MA9 за 15 свечей
                },
                "ma_trend100": {
                    "enabled": True,
                    "period": 100,       # период глобальной скользящей средней
                    "lookback": 30,      # анализировать направление MA100 за 30 свечей
                },
                "rsi": {
                    "enabled": False,              # отключен по умолчанию
                    "period": 14,                  # период RSI
                    "oversold_threshold": 30,      # BULLISH только при RSI ≤ 30 (перепроданность)
                    "overbought_threshold": 70,    # BEARISH только при RSI ≥ 70 (перекупленность)
                },
            },
        },
    },

    # ═══════════════════════════════════════════════════════════════════
    # 🔍 ВАЛИДАЦИЯ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════
    "validation": {
        "enabled": True,
        "mode": "custom",  # "default" | "strict" | "custom"

        "custom_rules": {
            "candle_direction": True,       # Направление свечи паттерна
            "confirmation": False,          # Подтверждение следующей свечой
            "min_breakout_distance": 0.1,   # Минимальное движение от цены паттерна (%)
        }
    },

    # ═══════════════════════════════════════════════════════════════════
    # 📈 ФИЛЬТР ТРЕНДА
    # ═══════════════════════════════════════════════════════════════════
    "trend_filter": {
        "enabled": False,
        "sma_period": 100,  # SMA для определения тренда
    },
}


def get_default_config() -> dict:
    """Получить дефолтную конфигурацию стратегии."""
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
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)

    if custom_config:
        _deep_update(config, custom_config)

    tf_config = config["timeframes"].get(timeframe, {})
    config["tp_percent"] = tf_config.get("tp_percent", 1.5)
    config["sl_percent"] = tf_config.get("sl_percent", 1.0)

    return config


def _deep_update(base: dict, override: dict) -> None:
    """Рекурсивно обновить словарь (для вложенных структур конфига)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
