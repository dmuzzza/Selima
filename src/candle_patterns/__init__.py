"""
Candle Patterns Strategy Package

Стратегия на основе свечных паттернов (Doji, Hammer, Engulfing, Shooting Star и др.)
Адаптирована под архитектуру BaseStrategy.
"""

from .strategy import CandlePatternStrategy
from .config import get_config, get_default_config
from .signal_validator import (
    SignalValidator,
    create_default_validator,
    create_strict_validator,
    create_custom_validator,
)

__all__ = [
    "CandlePatternStrategy",
    "get_config",
    "get_default_config",
    "SignalValidator",
    "create_default_validator",
    "create_strict_validator",
    "create_custom_validator",
]
