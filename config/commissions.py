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
from datetime import date, datetime
from os import getenv


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

COMMISSION_MODES = ("ALL", "LONG_ONLY", "SHORT_ONLY", "NONE")


def _read_commission_mode() -> str:
    """Commission mode from the environment; `SHORT_ONLY` is the legacy default.

    A gross run must be really gross, so the mode is switchable without touching
    the code: `COMMISSION_MODE=NONE` disables both transaction and margin fees.
    """
    raw = getenv("COMMISSION_MODE", "SHORT_ONLY").strip().upper()
    if raw not in COMMISSION_MODES:
        raise ValueError(
            f"COMMISSION_MODE must be one of {', '.join(COMMISSION_MODES)}, got {raw!r}"
        )
    return raw


COMMISSION_MODE = _read_commission_mode()


# ═══════════════════════════════════════════════════════════════════════════
# МАРЖИНАЛЬНАЯ КОМИССИЯ (ежедневная)
# ═══════════════════════════════════════════════════════════════════════════
#
# Тариф брокера (T-Bank, «плата за перенос непокрытой позиции», ADR-018):
#   - платит только непокрытая часть: позиция минус собственные средства;
#   - меньше MARGIN_MIN_UNCOVERED — платы нет;
#   - позиция, закрытая в тот же торговый день, платы не несёт;
#   - иначе — тариф брокера за каждый перенесённый день.
#
# Раньше плата бралась со всей позиции и минимум за один день, что для позиции
# на собственные средства (position_size_percent <= 100) давало лишние рубли.

# Минимальная непокрытая позиция, с которой брокер начинает списывать плату.
MARGIN_MIN_UNCOVERED = 5_000.0

# Считать ли шорт полностью непокрытым. У брокера шорт — это заём бумаг, поэтому
# плата за перенос берётся со всей стоимости шорта, даже если денег на счёте
# достаточно (ADR-018).
#
# Дефолт — True, и это КОНСЕРВАТИВНОЕ значение, НЕ СВЕРЕННОЕ с отчётом брокера.
# Обоснование: ошибка издержек должна быть консервативной. Занижение издержек
# даёт ложный edge, завышение — только отсекает часть прибыли, поэтому
# асимметрия цены ошибки решает в пользу True. `MARGIN_SHORT_UNCOVERED=0` —
# режим чувствительности, когда денежное обеспечение считаем покрытием шорта.
# TODO(T2-17): сверить с реальным отчётом брокера и зафиксировать факт.
def _read_margin_short_uncovered() -> bool:
    """Whether a short is charged as fully uncovered; default is conservative."""
    return getenv("MARGIN_SHORT_UNCOVERED", "1").strip().lower() in {"1", "true", "yes"}


MARGIN_SHORT_UNCOVERED = _read_margin_short_uncovered()

MARGIN_FEE_BRACKETS: list[MarginFeeBracket] = [
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


def margin_nights(entry_time: datetime, exit_time: datetime) -> int:
    """Number of trading-day boundaries the position was carried over.

    The broker charges per transfer: a position opened and closed on the same
    trading day pays nothing. A calendar-date change is used as a proxy for the
    trading-day boundary (MOEX day ends at 23:50 MSK, so the only case it
    misreads is a trade spanning 23:50-00:00).
    """
    entry_date: date = entry_time.date()
    exit_date: date = exit_time.date()
    return max(0, (exit_date - entry_date).days)


def calculate_margin_fee(
    position_amount: float,
    own_funds: float,
    nights: int,
    is_short: bool = False,
    short_uncovered: bool | None = None,
) -> float:
    """Margin fee for holding an uncovered position over `nights` days.

    Args:
        position_amount: Position notional in rubles.
        own_funds: Own money allocated to the position (balance at entry).
        nights: Trading-day boundaries crossed; 0 for an intraday trade.
        is_short: Short positions can be treated as fully uncovered
            (`MARGIN_SHORT_UNCOVERED`), because the broker lends the securities.
        short_uncovered: Overrides `MARGIN_SHORT_UNCOVERED`; used by the audit to
            replay a snapshot with the model it was produced with.

    Returns:
        float: Fee in rubles; 0 when the position is covered by own funds,
        below the broker threshold, or closed intraday.
    """
    treat_short_as_uncovered = (
        MARGIN_SHORT_UNCOVERED if short_uncovered is None else short_uncovered
    )
    uncovered = position_amount if (is_short and treat_short_as_uncovered) else max(
        0.0, position_amount - own_funds
    )
    if uncovered < MARGIN_MIN_UNCOVERED or nights <= 0:
        return 0.0
    return get_margin_fee_bracket(uncovered).daily_fee * nights


def describe_commissions() -> dict[str, object]:
    """Commission settings that affect PnL, written into every report snapshot.

    Sits next to `costs` in the baseline/walk-forward JSON: without the margin
    flag the same PnL number can come from two different models (ADR-018).
    """
    return {
        "mode": COMMISSION_MODE,
        "transaction_percent": TRANSACTION_COMMISSION_PERCENT,
        "margin_short_uncovered": MARGIN_SHORT_UNCOVERED,
        "margin_min_uncovered": MARGIN_MIN_UNCOVERED,
    }