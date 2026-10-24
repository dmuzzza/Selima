"""
Pattern Detectors Package

Каждый паттерн — отдельный класс-детектор, наследующий BasePatternDetector.
Здесь — реестр всех доступных детекторов паттернов.
"""

from .base import BasePatternDetector, PatternEvent
from .engulfing import EngulfingDetector
from .evening_star_doji import EveningStarDojiDetector

# from .doji import DojiDetector
# from .hammer import HammerDetector
# from .shooting_star import ShootingStarDetector

# ═══════════════════════════════════════════════════════════════════════════
# РЕЕСТР ДЕТЕКТОРОВ ПАТТЕРНОВ
# ═══════════════════════════════════════════════════════════════════════════

# Ключ = код паттерна (совпадает с ключом в DEFAULT_CONFIG["patterns"])
PATTERN_DETECTORS = {
    "engulfing": EngulfingDetector,
    "evening_star_doji": EveningStarDojiDetector,
    # "doji": DojiDetector,
    # "hammer": HammerDetector,
    # "shooting_star": ShootingStarDetector,
}


def get_enabled_detectors(patterns_config: dict) -> list:
    """
    Получить список включённых детекторов на основе конфига.

    Args:
        patterns_config: Секция "patterns" из конфига стратегии

    Returns:
        list[BasePatternDetector]: Список инициализированных детекторов
    """
    detectors = []
    for pattern_code, detector_class in PATTERN_DETECTORS.items():
        pattern_cfg = patterns_config.get(pattern_code, {})
        if pattern_cfg.get("enabled", False):
            detectors.append(detector_class(config=pattern_cfg))
    return detectors


__all__ = [
    "BasePatternDetector",
    "PatternEvent",
    "PATTERN_DETECTORS",
    "get_enabled_detectors",
]
