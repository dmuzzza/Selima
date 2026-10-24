"""
SMA Crossover Strategy Package

Стратегия пересечения цены через линию SMA50.
"""

from .strategy import SMACrossoverStrategy
from .config import get_config, get_default_config
from .signal_validator import (
    SignalValidator,
    create_default_validator,
    create_strict_validator,
    create_custom_validator,
)

__all__ = [
    "SMACrossoverStrategy",
    "get_config",
    "get_default_config",
    "SignalValidator",
    "create_default_validator",
    "create_strict_validator",
    "create_custom_validator",
]