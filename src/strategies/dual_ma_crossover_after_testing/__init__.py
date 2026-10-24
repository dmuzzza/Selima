"""
Dual MA Crossover Strategy Package

Стратегия пересечения двух скользящих средних:
- SMA50 (быстрая)
- MA Adaptive (медленная)
"""

from .strategy import DualMACrossoverStrategy
from .config import get_config, get_default_config

__all__ = ["DualMACrossoverStrategy", "get_config", "get_default_config"]