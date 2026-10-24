# config/commissions.py
"""
Конфигурация комиссий брокера.

Единые комиссии для бэктестинга и live-торговли.

═══════════════════════════════════════════════════════════════════════════
РЕЖИМ ПРИМЕНЕНИЯ КОМИССИЙ (COMMISSION_MODE):
    "ALL"        — комиссии на все сделки (LONG + SHORT)
    "LONG_ONLY"  — комиссии только на LONG
    "SHORT_ONLY" — комиссии только на SHORT
    "NONE"       — комиссии отключены
═══════════════════════════════════════════════════════════════════════════
"""

from dataclasses import dataclass
from typing import List


@dataclass
class MarginFeeBracket:
    """Диапазон суммы сделки для маржинальной комиссии"""
    min_amount: float      # Минимальная сумма (включительно)
    max_amount: float      # Максимальная сумма (включительно)
    daily_fee: float       # Комиссия в рублях в день


# ═══════════════════════════════════════════════════════════════════════════
# ТРАНЗАКЦИОННАЯ КОМИССИЯ
# ═══════════════════════════════════════════════════════════════════════════

# Комиссия за одну операцию (открытие ИЛИ закрытие)
TRANSACTION_COMMISSION_PERCENT = 0.05  # 0.05% от суммы сделки


# ═══════════════════════════════════════════════════════════════════════════
# РЕЖИМ ПРИМЕНЕНИЯ КОМИССИЙ
# ═══════════════════════════════════════════════════════════════════════════

# "ALL" | "LONG_ONLY" | "SHORT_ONLY" | "NONE"
COMMISSION_MODE = "SHORT_ONLY"


# ═══════════════════════════════════════════════════════════════════════════
# МАРЖИНАЛЬНАЯ КОМИССИЯ (ежедневная)
# ═══════════════════════════════════════════════════════════════════════════

MARGIN_FEE_BRACKETS: List[MarginFeeBracket] = [
    MarginFeeBracket(min_amount=0,          max_amount=50_000,    daily_fee= 40), #40 
    MarginFeeBracket(min_amount=50_000,     max_amount=100_000,   daily_fee=75), #75
    MarginFeeBracket(min_amount=100_000,    max_amount=250_000,   daily_fee=180), #180
    MarginFeeBracket(min_amount=250_000,    max_amount=500_000,   daily_fee=350), #350
    MarginFeeBracket(min_amount=500_000,    max_amount=1_000_000, daily_fee=700), #700
    MarginFeeBracket(min_amount=1_000_000,  max_amount=2_500_000, daily_fee=1750), #1750
    MarginFeeBracket(min_amount=2_500_000,  max_amount=5_000_000, daily_fee=3500), #3500
] 


# ═══════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════

def get_margin_fee_bracket(position_amount: float) -> MarginFeeBracket:
    """
    Получить диапазон маржинальной комиссии для суммы позиции.
    
    Args:
        position_amount: Сумма позиции в рублях
    
    Returns:
        MarginFeeBracket: Соответствующий диапазон
    """
    for bracket in MARGIN_FEE_BRACKETS:
        if bracket.min_amount <= position_amount <= bracket.max_amount:
            return bracket
    
    # Если сумма больше максимального диапазона → последний bracket
    return MARGIN_FEE_BRACKETS[-1]


def calculate_daily_margin_fee(position_amount: float) -> float:
    """
    Рассчитать ежедневную маржинальную комиссию для суммы позиции.
    
    Args:
        position_amount: Сумма позиции в рублях
    
    Returns:
        float: Комиссия в рублях в день
    """
    bracket = get_margin_fee_bracket(position_amount)
    return bracket.daily_fee