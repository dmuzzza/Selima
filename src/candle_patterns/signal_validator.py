"""
Candle Pattern Signal Validator

Модуль валидации сигналов свечных паттернов.
Адаптирован из sma_crossover/signal_validator.py под специфику паттернов.

═══════════════════════════════════════════════════════════════════════════
АРХИТЕКТУРА:
═══════════════════════════════════════════════════════════════════════════

1. ValidationRule (базовый класс)
2. Конкретные правила (CandleDirectionRule, ConfirmationCandleRule, MinMovementRule)
3. SignalValidator (оркестратор)
4. Фабрики валидаторов

ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════

from .signal_validator import SignalValidator, create_default_validator

validator = create_default_validator()
is_valid = validator.validate(candles, signal)

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import List, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

from src.market_data.models import Candle
from src.strategies.base_strategy import Signal

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# БАЗОВЫЕ СТРУКТУРЫ
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationContext:
    """Контекст для валидации сигнала паттерна."""
    candles: List[Candle]
    signal: Signal
    signal_candle: Candle
    prev_candle: Optional[Candle]
    next_candle: Optional[Candle]


@dataclass
class ValidationResult:
    """Результат валидации правила."""
    is_valid: bool
    rule_name: str
    reason: Optional[str] = None

    def __repr__(self):
        if self.is_valid:
            return f"✓ {self.rule_name}: PASSED"
        return f"✗ {self.rule_name}: FAILED ({self.reason})"


# ═══════════════════════════════════════════════════════════════════════════
# БАЗОВЫЙ КЛАСС ПРАВИЛА
# ═══════════════════════════════════════════════════════════════════════════

class ValidationRule(ABC):
    """Базовый класс для правила валидации."""

    def __init__(self, name: str, enabled: bool = True, debug: bool = False):
        self.name = name
        self.enabled = enabled
        self.debug = debug

    @abstractmethod
    def validate(self, ctx: ValidationContext) -> ValidationResult:
        pass

    def __repr__(self):
        return f"{self.name} [{'ON' if self.enabled else 'OFF'}]"


# ═══════════════════════════════════════════════════════════════════════════
# ПРАВИЛА ВАЛИДАЦИИ
# ═══════════════════════════════════════════════════════════════════════════

class CandleDirectionRule(ValidationRule):
    """
    Правило 1: Направление свечи должно совпадать с типом сигнала.

    BULLISH → свеча должна быть бычьей (close > open)
    BEARISH → свеча должна быть медвежьей (close < open)
    """

    def __init__(self, enabled: bool = True, debug: bool = False):
        super().__init__(
            name="Candle Direction",
            enabled=enabled,
            debug=debug
        )

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        candle = ctx.signal_candle
        signal_type = ctx.signal.signal_type

        is_bullish_candle = candle.close > candle.open
        is_bearish_candle = candle.close < candle.open

        if signal_type == "BULLISH" and not is_bullish_candle:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"BULLISH signal but candle is bearish "
                       f"(close={candle.close:.2f} < open={candle.open:.2f})"
            )

        if signal_type == "BEARISH" and not is_bearish_candle:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"BEARISH signal but candle is bullish "
                       f"(close={candle.close:.2f} > open={candle.open:.2f})"
            )

        return ValidationResult(is_valid=True, rule_name=self.name)


class ConfirmationCandleRule(ValidationRule):
    """
    Правило 2: Следующая свеча должна подтвердить направление сигнала.

    BULLISH → следующая свеча должна быть бычьей
    BEARISH → следующая свеча должна быть медвежьей
    """

    def __init__(self, enabled: bool = True, debug: bool = False):
        super().__init__(
            name="Confirmation Candle",
            enabled=enabled,
            debug=debug
        )

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        if ctx.next_candle is None:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason="No next candle available for confirmation"
            )

        next_candle = ctx.next_candle
        signal_type = ctx.signal.signal_type

        is_bullish = next_candle.close > next_candle.open
        is_bearish = next_candle.close < next_candle.open

        if signal_type == "BULLISH" and not is_bullish:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"BULLISH signal not confirmed "
                       f"(next candle bearish: close={next_candle.close:.2f} < open={next_candle.open:.2f})"
            )

        if signal_type == "BEARISH" and not is_bearish:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"BEARISH signal not confirmed "
                       f"(next candle bullish: close={next_candle.close:.2f} > open={next_candle.open:.2f})"
            )

        return ValidationResult(is_valid=True, rule_name=self.name)


class MinMovementRule(ValidationRule):
    """
    Правило 3: Минимальное движение цены (сила сигнала).

    |close - open| / open должно быть не меньше min_distance_percent.
    """

    def __init__(self, min_distance_percent: float = 0.1, enabled: bool = True, debug: bool = False):
        super().__init__(
            name=f"Min Movement ({min_distance_percent}%)",
            enabled=enabled,
            debug=debug
        )
        self.min_distance_percent = min_distance_percent

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        candle = ctx.signal_candle

        if candle.open == 0:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason="Invalid candle: open price is 0"
            )

        movement_percent = abs(candle.close - candle.open) / candle.open * 100

        if movement_percent < self.min_distance_percent:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"Movement too small: {movement_percent:.3f}% < {self.min_distance_percent}%"
            )

        return ValidationResult(is_valid=True, rule_name=self.name)


# ═══════════════════════════════════════════════════════════════════════════
# ВАЛИДАТОР СИГНАЛОВ (ОРКЕСТРАТОР)
# ═══════════════════════════════════════════════════════════════════════════

class SignalValidator:
    """
    Валидатор сигналов — применяет набор правил к сигналу.
    """

    def __init__(self, rules: List[ValidationRule], debug: bool = False):
        """
        Args:
            rules: Список правил валидации
            debug: Режим отладки
        """
        self.rules = rules
        self.debug = debug
        self.logger = logging.getLogger(__name__)

    def validate(
        self,
        candles: List[Candle],
        signal: Signal,
    ) -> bool:
        """
        Валидировать сигнал.

        Args:
            candles: Все свечи
            signal: Сигнал для проверки

        Returns:
            bool: True если сигнал валиден
        """
        ctx = self._prepare_context(candles, signal)

        if ctx is None:
            if self.debug:
                self.logger.debug("Cannot prepare context for validation")
            return False

        for rule in self.rules:
            if not rule.enabled:
                continue

            result = rule.validate(ctx)

            if self.debug:
                self.logger.debug(f"  {result}")

            if not result.is_valid:
                if self.debug:
                    self.logger.debug(f"✗ Signal REJECTED: {result.reason}")
                return False

        if self.debug:
            self.logger.debug("✓ Signal ACCEPTED")
        return True

    def _prepare_context(
        self,
        candles: List[Candle],
        signal: Signal,
    ) -> Optional[ValidationContext]:
        """Подготовить контекст для валидации."""
        idx = signal.candle_index

        if idx >= len(candles):
            return None

        signal_candle = candles[idx]
        prev_candle = candles[idx - 1] if idx > 0 else None
        next_candle = candles[idx + 1] if idx + 1 < len(candles) else None

        return ValidationContext(
            candles=candles,
            signal=signal,
            signal_candle=signal_candle,
            prev_candle=prev_candle,
            next_candle=next_candle,
        )

    def add_rule(self, rule: ValidationRule) -> None:
        """Добавить правило в валидатор."""
        self.rules.append(rule)

    def remove_rule(self, rule_name: str) -> None:
        """Удалить правило по имени."""
        self.rules = [r for r in self.rules if r.name != rule_name]

    def list_rules(self) -> None:
        """Вывести список правил."""
        print("Validation Rules:")
        for i, rule in enumerate(self.rules, 1):
            print(f"  {i}. {rule}")


# ═══════════════════════════════════════════════════════════════════════════
# ФАБРИКИ ВАЛИДАТОРОВ
# ═══════════════════════════════════════════════════════════════════════════

def create_default_validator(debug: bool = False) -> SignalValidator:
    """
    Создать валидатор с дефолтными правилами.

    Args:
        debug: Режим отладки

    Returns:
        SignalValidator: Готовый валидатор
    """
    rules = [
        CandleDirectionRule(enabled=True, debug=debug),
        ConfirmationCandleRule(enabled=True, debug=debug),
        MinMovementRule(min_distance_percent=0.1, enabled=True, debug=debug),
    ]
    return SignalValidator(rules=rules, debug=debug)


def create_strict_validator(debug: bool = False) -> SignalValidator:
    """
    Создать строгий валидатор (все правила с более жёсткими порогами).

    Args:
        debug: Режим отладки

    Returns:
        SignalValidator: Строгий валидатор
    """
    rules = [
        CandleDirectionRule(enabled=True, debug=debug),
        ConfirmationCandleRule(enabled=True, debug=debug),
        MinMovementRule(min_distance_percent=0.2, enabled=True, debug=debug),
    ]
    return SignalValidator(rules=rules, debug=debug)


def create_custom_validator(
    candle_direction: bool = True,
    confirmation: bool = True,
    min_distance: float = 0.1,
    debug: bool = False
) -> SignalValidator:
    """
    Создать кастомный валидатор с настраиваемыми параметрами.

    Args:
        candle_direction: Включить проверку направления свечи
        confirmation: Включить проверку подтверждения
        min_distance: Минимальное движение цены в % (0 = отключено)
        debug: Режим отладки

    Returns:
        SignalValidator: Кастомный валидатор
    """
    rules = []

    if candle_direction:
        rules.append(CandleDirectionRule(enabled=True, debug=debug))

    if confirmation:
        rules.append(ConfirmationCandleRule(enabled=True, debug=debug))

    if min_distance > 0:
        rules.append(MinMovementRule(min_distance_percent=min_distance, enabled=True, debug=debug))

    return SignalValidator(rules=rules, debug=debug)
