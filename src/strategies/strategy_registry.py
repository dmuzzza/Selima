"""
Strategy Registry V1

Реестр всех доступных торговых стратегий.
Централизованное место для регистрации и получения стратегий.

═══════════════════════════════════════════════════════════════════════════
"""

from typing import Dict, Type
from src.strategies.base_strategy import BaseStrategy

# ═══════════════════════════════════════════════════════════════════════════
# ИМПОРТЫ ВСЕХ СТРАТЕГИЙ
# ═══════════════════════════════════════════════════════════════════════════

from src.strategies.sma_crossover.strategy import SMACrossoverStrategy
from src.strategies.dual_ma_crossover_after_testing.strategy import (
    DualMACrossoverStrategy as DualMACrossoverAfterTestingStrategy,
)

# TODO(T0-1): restore remaining strategies when their packages are added back.
# Planned codes: dual_ma_crossover, dual_ma_ma_crossover, dual_ma_crossover_adaptive,
# candle_patterns, candle_patterns_2, macd_plus, macd_plus_2_update,
# horizontal_levels, mfi_bb.

# Реестр стратегий
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "sma_crossover": SMACrossoverStrategy,
    "dual_ma_crossover_after_testing": DualMACrossoverAfterTestingStrategy,
}


# ═══════════════════════════════════════════════════════════════════════════
# ФУНКЦИИ ДОСТУПА К РЕЕСТРУ
# ═══════════════════════════════════════════════════════════════════════════


def get_available_strategies() -> Dict[str, str]:
    """
    Получить список доступных стратегий для UI.
    
    Returns:
        Dict: {код_стратегии: название_стратегии}
    
    Example:
        >>> strategies = get_available_strategies()
        >>> # {"sma_crossover": "SMA Crossover", "dual_ma": "Dual MA Crossover"}
    """
    result = {}
    for code, strategy_class in STRATEGY_REGISTRY.items():
        try:
            # Создаём временный экземпляр для получения имени
            instance = strategy_class(timeframe="1h")
            result[code] = instance.name
        except Exception as e:
            print(f"Warning: Failed to get name for strategy '{code}': {e}")
            result[code] = code  # Fallback к коду
    
    return result


def get_strategy_class(strategy_code: str) -> Type[BaseStrategy]:
    """
    Получить класс стратегии по коду.
    
    Args:
        strategy_code: Код стратегии (e.g., "sma_crossover")
    
    Returns:
        Type[BaseStrategy]: Класс стратегии
    
    Raises:
        ValueError: Если стратегия не найдена
    """
    if strategy_code not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise ValueError(
            f"Unknown strategy: '{strategy_code}'. "
            f"Available strategies: {available}"
        )
    
    return STRATEGY_REGISTRY[strategy_code]


def register_strategy(code: str, strategy_class: Type[BaseStrategy]) -> None:
    """
    Зарегистрировать новую стратегию в реестре.
    
    Args:
        code: Уникальный код стратегии
        strategy_class: Класс стратегии (наследник BaseStrategy)
    """
    if code in STRATEGY_REGISTRY:
        print(f"Warning: Strategy '{code}' already registered. Overwriting...")
    
    STRATEGY_REGISTRY[code] = strategy_class
    print(f"✓ Strategy '{code}' registered successfully")


def list_strategies() -> None:
    """
    Вывести список всех зарегистрированных стратегий.
    """
    strategies = get_available_strategies()
    
    if not strategies:
        print("No strategies registered yet.")
        return
    
    print("Available strategies:")
    for code, name in strategies.items():
        print(f"  - {code}: {name}")
