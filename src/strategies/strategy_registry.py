"""
Strategy Registry V2

Стратегия регистрируется декоратором на своём классе:

    @register_strategy
    class MyStrategy(BaseStrategy):
        code = "my_strategy"
        name = "My Strategy"
        description = "..."

Правок в этом файле при добавлении стратегии не требуется: при первом обращении
к реестру импортируются все пакеты внутри `src/strategies`, и каждый регистрирует
себя как побочный эффект импорта. Пакеты с префиксом `_` пропускаются, поэтому
шаблон `_template` в реестр не попадает.

Обнаружение ленивое (при первом запросе): это убирает побочные эффекты импорта
и разрывает циклический импорт — стратегии импортируют `register_strategy`
отсюда, а этот модуль импортирует пакеты стратегий.

═══════════════════════════════════════════════════════════════════════════
"""

import importlib
import logging
import pkgutil

from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# РЕЕСТР
# ═══════════════════════════════════════════════════════════════════════════

# Заполняется декораторами; пуст до первого обращения (ленивое обнаружение).
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {}

_discovered = False


def register_strategy(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """
    Декоратор класса: добавить стратегию в реестр под её `code`.

    Raises:
        ValueError: пустой `code` или код уже занят другим классом.
    """
    code = getattr(cls, "code", "")
    if not code:
        raise ValueError(
            f"{cls.__name__} must define a non-empty 'code' class attribute"
        )

    existing = STRATEGY_REGISTRY.get(code)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Strategy code '{code}' is already registered by {existing.__name__}"
        )

    STRATEGY_REGISTRY[code] = cls
    logger.debug(f"Registered strategy '{code}' -> {cls.__name__}")
    return cls


def _discover_strategies() -> None:
    """Импортировать все пакеты стратегий, чтобы сработали декораторы."""
    package = importlib.import_module("src.strategies")
    for module_info in pkgutil.iter_modules(package.__path__):
        if not module_info.ispkg or module_info.name.startswith("_"):
            continue
        importlib.import_module(f"{package.__name__}.{module_info.name}")


def _ensure_discovered() -> None:
    """Выполнить обнаружение один раз (защита от повторного входа)."""
    global _discovered
    if _discovered:
        return
    _discovered = True
    _discover_strategies()


# ═══════════════════════════════════════════════════════════════════════════
# ФУНКЦИИ ДОСТУПА К РЕЕСТРУ
# ═══════════════════════════════════════════════════════════════════════════


def get_available_strategies() -> dict[str, str]:
    """
    Получить список доступных стратегий для UI.

    Экземпляры не создаются: `code` и `name` — атрибуты класса.

    Returns:
        Dict: {код_стратегии: название_стратегии}
    """
    _ensure_discovered()
    return {
        code: (strategy_class.name or code)
        for code, strategy_class in STRATEGY_REGISTRY.items()
    }


def get_strategy_class(strategy_code: str) -> type[BaseStrategy]:
    """
    Получить класс стратегии по коду.

    Args:
        strategy_code: Код стратегии (e.g., "sma_crossover")

    Returns:
        Type[BaseStrategy]: Класс стратегии

    Raises:
        ValueError: Если стратегия не найдена
    """
    _ensure_discovered()
    if strategy_code not in STRATEGY_REGISTRY:
        available = ", ".join(sorted(STRATEGY_REGISTRY))
        raise ValueError(
            f"Unknown strategy: '{strategy_code}'. "
            f"Available strategies: {available}"
        )

    return STRATEGY_REGISTRY[strategy_code]


def list_strategies() -> None:
    """Залогировать список всех зарегистрированных стратегий."""
    strategies = get_available_strategies()

    if not strategies:
        logger.info("No strategies registered yet.")
        return

    logger.info("Available strategies:")
    for code, name in strategies.items():
        logger.info(f"  - {code}: {name}")
