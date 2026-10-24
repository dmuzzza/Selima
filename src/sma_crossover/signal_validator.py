"""
SMA Crossover Signal Validator

Модуль валидации сигналов пробоев и отскоков.
Расширяемая архитектура для постепенного добавления правил.

═══════════════════════════════════════════════════════════════════════════
АРХИТЕКТУРА:
═══════════════════════════════════════════════════════════════════════════

1. ValidationRule (базовый класс для правил)
2. Конкретные правила (BreakoutCandleDirectionRule, ConfirmationCandleRule, etc.)
3. SignalValidator (оркестратор, применяет правила)
4. Фабрика правил (easy configuration)

ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════

from .signal_validator import SignalValidator, create_default_validator

validator = create_default_validator()
is_valid = validator.validate(candles, signal, sma_series)

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import List, Optional, Protocol
from dataclasses import dataclass
from abc import ABC, abstractmethod

from src.market_data.models import Candle
from src.strategies.base_strategy import Signal

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 📊 БАЗОВЫЕ СТРУКТУРЫ
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationContext:
    """
    Контекст для валидации сигнала.
    Содержит всю необходимую информацию для проверки.
    """
    candles: List[Candle]           # Все свечи
    signal: Signal                   # Сигнал для проверки
    sma_series: List[Optional[float]]  # Значения SMA
    signal_candle: Candle           # Свеча с сигналом
    prev_candle: Optional[Candle]   # Предыдущая свеча
    next_candle: Optional[Candle]   # Следующая свеча (для подтверждения)
    sma_value: float                # SMA на момент сигнала
    prev_sma: Optional[float]       # SMA предыдущей свечи


@dataclass
class ValidationResult:
    """Результат валидации правила."""
    is_valid: bool          # Прошла ли валидация
    rule_name: str          # Название правила
    reason: Optional[str] = None  # Причина отклонения (если не прошла)
    
    def __repr__(self):
        if self.is_valid:
            return f"✓ {self.rule_name}: PASSED"
        return f"✗ {self.rule_name}: FAILED ({self.reason})"


# ═══════════════════════════════════════════════════════════════════════════
# 🔧 БАЗОВЫЙ КЛАСС ПРАВИЛА
# ═══════════════════════════════════════════════════════════════════════════

class ValidationRule(ABC):
    """
    Базовый класс для правила валидации сигнала.
    
    Каждое правило:
    - Имеет уникальное имя
    - Проверяет одно конкретное условие
    - Возвращает ValidationResult
    """
    
    def __init__(self, name: str, enabled: bool = True, debug: bool = False):
        """
        Args:
            name: Название правила
            enabled: Включено ли правило
            debug: Режим отладки
        """
        self.name = name
        self.enabled = enabled
        self.debug = debug
    
    @abstractmethod
    def validate(self, ctx: ValidationContext) -> ValidationResult:
        """
        Проверить правило.
        
        Args:
            ctx: Контекст валидации
            
        Returns:
            ValidationResult: Результат проверки
        """
        pass
    
    def __repr__(self):
        status = "ON" if self.enabled else "OFF"
        return f"{self.name} [{status}]"


# ═══════════════════════════════════════════════════════════════════════════
# 📋 ПРАВИЛА ВАЛИДАЦИИ ПРОБОЕВ (BREAKOUT)
# ═══════════════════════════════════════════════════════════════════════════

class BreakoutCandleDirectionRule(ValidationRule):
    """
    Правило 1: Направление свечи пробоя должно совпадать с сигналом.
    
    BULLISH сигнал → свеча должна быть бычьей (close > open)
    BEARISH сигнал → свеча должна быть медвежьей (close < open)
    """
    
    def __init__(self, enabled: bool = True, debug: bool = False):
        super().__init__(
            name="Breakout Candle Direction",
            enabled=enabled,
            debug=debug
        )
    
    def validate(self, ctx: ValidationContext) -> ValidationResult:
        candle = ctx.signal_candle
        signal_type = ctx.signal.signal_type
        
        is_bullish_candle = candle.close > candle.open
        is_bearish_candle = candle.close < candle.open
        
        # BULLISH сигнал → свеча должна быть бычьей
        if signal_type == "BULLISH":
            if not is_bullish_candle:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.name,
                    reason=f"BULLISH signal but candle is bearish (close={candle.close:.2f} < open={candle.open:.2f})"
                )
        
        # BEARISH сигнал → свеча должна быть медвежьей
        elif signal_type == "BEARISH":
            if not is_bearish_candle:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.name,
                    reason=f"BEARISH signal but candle is bullish (close={candle.close:.2f} > open={candle.open:.2f})"
                )
        
        return ValidationResult(is_valid=True, rule_name=self.name)


class ConfirmationCandleRule(ValidationRule):
    """
    Правило 2: Следующая свеча должна подтвердить направление.
    
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
        
        # BULLISH → следующая свеча должна быть бычьей
        if signal_type == "BULLISH":
            if not is_bullish:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.name,
                    reason=f"BULLISH signal not confirmed (next candle bearish: close={next_candle.close:.2f} < open={next_candle.open:.2f})"
                )
        
        # BEARISH → следующая свеча должна быть медвежьей
        elif signal_type == "BEARISH":
            if not is_bearish:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.name,
                    reason=f"BEARISH signal not confirmed (next candle bullish: close={next_candle.close:.2f} > open={next_candle.open:.2f})"
                )
        
        return ValidationResult(is_valid=True, rule_name=self.name)


class MinBreakoutDistanceRule(ValidationRule):
    """
    Правило 3: Минимальное расстояние от SMA (сила пробоя).
    
    Цена должна уйти от SMA минимум на X% для подтверждения пробоя.
    """
    
    def __init__(self, min_distance_percent: float = 0.2, enabled: bool = True, debug: bool = False):
        """
        Args:
            min_distance_percent: Минимальное расстояние от SMA в % (например, 0.2 = 0.2%)
        """
        super().__init__(
            name=f"Min Breakout Distance ({min_distance_percent}%)",
            enabled=enabled,
            debug=debug
        )
        self.min_distance_percent = min_distance_percent
    
    def validate(self, ctx: ValidationContext) -> ValidationResult:
        candle = ctx.signal_candle
        sma = ctx.sma_value
        
        distance_percent = abs(candle.close - sma) / sma * 100
        
        if distance_percent < self.min_distance_percent:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"Breakout too weak: {distance_percent:.3f}% < {self.min_distance_percent}%"
            )
        
        return ValidationResult(is_valid=True, rule_name=self.name)


# ═══════════════════════════════════════════════════════════════════════════
# 📋 ПРАВИЛА ВАЛИДАЦИИ ОТСКОКОВ (BOUNCE)
# ═══════════════════════════════════════════════════════════════════════════

class BounceZoneRule(ValidationRule):
    """
    Правило 4: Цена должна находиться в зоне отскока от SMA.
    
    Цена приближается к SMA, но НЕ пересекает её.
    Находится в пределах bounce_zone_percent от SMA.
    """
    
    def __init__(self, bounce_zone_percent: float = 0.5, enabled: bool = True, debug: bool = False):
        """
        Args:
            bounce_zone_percent: Ширина зоны отскока в % (например, 0.5 = 0.5%)
        """
        super().__init__(
            name=f"Bounce Zone ({bounce_zone_percent}%)",
            enabled=enabled,
            debug=debug
        )
        self.bounce_zone_percent = bounce_zone_percent
    
    def validate(self, ctx: ValidationContext) -> ValidationResult:
        candle = ctx.signal_candle
        sma = ctx.sma_value
        
        distance_percent = abs(candle.close - sma) / sma * 100
        
        # Цена должна быть близко к SMA
        if distance_percent > self.bounce_zone_percent:
            return ValidationResult(
                is_valid=False,
                rule_name=self.name,
                reason=f"Too far from SMA: {distance_percent:.3f}% > {self.bounce_zone_percent}%"
            )
        
        # Цена НЕ должна пересекать SMA (для отскока)
        # TODO: Добавить логику непересечения
        
        return ValidationResult(is_valid=True, rule_name=self.name)


# ═══════════════════════════════════════════════════════════════════════════
# 🎯 ВАЛИДАТОР СИГНАЛОВ (ОРКЕСТРАТОР)
# ═══════════════════════════════════════════════════════════════════════════

class SignalValidator:
    """
    Валидатор сигналов.
    
    Применяет набор правил к сигналу и определяет, валиден ли он.
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
        sma_series: List[Optional[float]]
    ) -> bool:
        """
        Валидировать сигнал.
        
        Args:
            candles: Все свечи
            signal: Сигнал для проверки
            sma_series: Значения SMA
            
        Returns:
            bool: True если сигнал валиден, False если нет
        """
        # Подготовка контекста
        ctx = self._prepare_context(candles, signal, sma_series)
        
        if ctx is None:
            if self.debug:
                self.logger.debug(f"Cannot prepare context for signal at idx={signal.candle_index}")
            return False
        
        # Применяем все включённые правила
        for rule in self.rules:
            if not rule.enabled:
                continue
            
            result = rule.validate(ctx)
            
            if self.debug:
                self.logger.debug(f"  {result}")
            
            if not result.is_valid:
                if self.debug:
                    self.logger.debug(f"✗ Signal REJECTED at idx={signal.candle_index}: {result.reason}")
                return False
        
        if self.debug:
            self.logger.debug(f"✓ Signal ACCEPTED at idx={signal.candle_index}")
        
        return True
    
    def _prepare_context(
        self,
        candles: List[Candle],
        signal: Signal,
        sma_series: List[Optional[float]]
    ) -> Optional[ValidationContext]:
        """
        Подготовить контекст для валидации.
        
        Returns:
            ValidationContext или None если данные недоступны
        """
        idx = signal.candle_index
        
        # Проверяем границы
        if idx >= len(candles) or idx >= len(sma_series):
            return None
        
        signal_candle = candles[idx]
        sma_value = sma_series[idx]
        
        if sma_value is None:
            return None
        
        # Предыдущая свеча
        prev_candle = candles[idx - 1] if idx > 0 else None
        prev_sma = sma_series[idx - 1] if idx > 0 else None
        
        # Следующая свеча (для подтверждения)
        next_candle = candles[idx + 1] if idx + 1 < len(candles) else None
        
        return ValidationContext(
            candles=candles,
            signal=signal,
            sma_series=sma_series,
            signal_candle=signal_candle,
            prev_candle=prev_candle,
            next_candle=next_candle,
            sma_value=sma_value,
            prev_sma=prev_sma
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
# 🏭 ФАБРИКА ВАЛИДАТОРОВ
# ═══════════════════════════════════════════════════════════════════════════

def create_default_validator(debug: bool = False) -> SignalValidator:
    """
    Создать валидатор с дефолтными правилами для пробоев.
    
    Args:
        debug: Режим отладки
        
    Returns:
        SignalValidator: Готовый валидатор
    """
    rules = [
        BreakoutCandleDirectionRule(enabled=True, debug=debug),
        ConfirmationCandleRule(enabled=True, debug=debug),
        MinBreakoutDistanceRule(min_distance_percent=0.2, enabled=True, debug=debug),
    ]
    
    return SignalValidator(rules=rules, debug=debug)


def create_strict_validator(debug: bool = False) -> SignalValidator:
    """
    Создать строгий валидатор (все правила включены).
    
    Args:
        debug: Режим отладки
        
    Returns:
        SignalValidator: Строгий валидатор
    """
    rules = [
        BreakoutCandleDirectionRule(enabled=True, debug=debug),
        ConfirmationCandleRule(enabled=True, debug=debug),
        MinBreakoutDistanceRule(min_distance_percent=0.3, enabled=True, debug=debug),
        # Можно добавить дополнительные правила
    ]
    
    return SignalValidator(rules=rules, debug=debug)


def create_custom_validator(
    candle_direction: bool = True,
    confirmation: bool = True,
    min_distance: float = 0.2,
    debug: bool = False
) -> SignalValidator:
    """
    Создать кастомный валидатор с настраиваемыми параметрами.
    
    Args:
        candle_direction: Включить проверку направления свечи
        confirmation: Включить проверку подтверждения
        min_distance: Минимальное расстояние от SMA (0 = отключено)
        debug: Режим отладки
        
    Returns:
        SignalValidator: Кастомный валидатор
    """
    rules = []
    
    if candle_direction:
        rules.append(BreakoutCandleDirectionRule(enabled=True, debug=debug))
    
    if confirmation:
        rules.append(ConfirmationCandleRule(enabled=True, debug=debug))
    
    if min_distance > 0:
        rules.append(MinBreakoutDistanceRule(min_distance_percent=min_distance, enabled=True, debug=debug))
    
    return SignalValidator(rules=rules, debug=debug)
