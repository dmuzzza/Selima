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

import math
from dataclasses import dataclass
from datetime import date, datetime
from os import getenv

# Version of the broker tariff table below. Bumped when the numbers change, so a
# report can be matched to the model that produced it (ADR-018).
MARGIN_TABLE_VERSION = 2


@dataclass(frozen=True)
class MarginFeeBracket:
    """Один диапазон занятой суммы и плата за сутки.

    Границы: `min_amount` не включается, `max_amount` включается. Диапазоны
    идут подряд без перекрытий, у последнего `max_amount = inf`.
    """

    min_amount: float
    max_amount: float
    daily_fee: float = 0.0       # фиксированная плата, ₽/сутки
    daily_percent: float = 0.0   # плата процентом от занятой суммы, %/сутки

    def fee_for(self, amount: float) -> float:
        """Плата за одни сутки для занятой суммы внутри этого диапазона."""
        return self.daily_fee + amount * self.daily_percent / 100.0


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
# Тариф брокера (T-Bank, «стоимость маржиналки», ADR-018):
#   - база — сумма, занятая у брокера: стоимость шорта, для лонга с плечом —
#     только непокрытая часть (позиция минус собственные средства);
#   - плата за каждый календарный день переноса, включая выходные и праздники;
#   - сделка, закрытая до конца торгового дня, платы не несёт;
#   - границы диапазонов включают верхнюю границу: ровно 50 000 ₽ — 40 ₽,
#     50 001 ₽ — 75 ₽.
#
# Broker charges nothing on three accounts up to 5 000 RUB. The number of accounts
# under the robot is unknown, so the model charges the paid rate and stays
# conservative. Replace with 0.0 to model a free account.
MARGIN_FEE_UP_TO_5K = 5.0

# Below one kopeck a "borrowed" remainder is a floating-point artefact of
# `balance * percent / 100`, not a loan: a fully covered position must pay zero.
MARGIN_MIN_BORROWED = 0.01

# Tariff table: `min_amount` is exclusive, `max_amount` is inclusive.
MARGIN_FEE_BRACKETS: list[MarginFeeBracket] = [
    MarginFeeBracket(min_amount=0,          max_amount=5_000,      daily_fee=MARGIN_FEE_UP_TO_5K),
    MarginFeeBracket(min_amount=5_000,      max_amount=50_000,     daily_fee=40),
    MarginFeeBracket(min_amount=50_000,     max_amount=100_000,    daily_fee=75),
    MarginFeeBracket(min_amount=100_000,    max_amount=250_000,    daily_fee=180),
    MarginFeeBracket(min_amount=250_000,    max_amount=500_000,    daily_fee=350),
    MarginFeeBracket(min_amount=500_000,    max_amount=1_000_000,  daily_fee=700),
    MarginFeeBracket(min_amount=1_000_000,  max_amount=2_500_000,  daily_fee=1_750),
    MarginFeeBracket(min_amount=2_500_000,  max_amount=5_000_000,  daily_fee=3_500),
    MarginFeeBracket(min_amount=5_000_000,  max_amount=10_000_000, daily_fee=6_900),
    MarginFeeBracket(min_amount=10_000_000, max_amount=25_000_000, daily_percent=0.068),
    MarginFeeBracket(min_amount=25_000_000, max_amount=50_000_000, daily_percent=0.065),
    MarginFeeBracket(min_amount=50_000_000, max_amount=math.inf,    daily_percent=0.057),
]


# ═══════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════


def _validate_number(value: object, name: str, *, allow_negative: bool = False) -> float:
    """Reject values that cannot be an amount: NaN, inf, non-numbers, negatives.

    A covered position (`borrowed <= 0`) is a valid state, not an error: it is
    handled by the callers and yields a zero fee.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if number < 0 and not allow_negative:
        raise ValueError(f"{name} must not be negative, got {number!r}")
    return number


def check_bracket_table(brackets: list[MarginFeeBracket]) -> None:
    """Ensure the table covers `(0, +inf)` with no gaps and no overlaps."""
    if not brackets:
        raise ValueError("margin fee table is empty")
    expected_min = 0.0
    for bracket in brackets:
        if bracket.min_amount != expected_min:
            raise ValueError(
                f"margin fee table has a gap or overlap at {bracket.min_amount!r}: "
                f"expected {expected_min!r}"
            )
        if bracket.max_amount <= bracket.min_amount:
            raise ValueError(
                f"margin fee bracket [{bracket.min_amount!r}; {bracket.max_amount!r}] "
                "has no positive width"
            )
        if bracket.daily_fee < 0 or bracket.daily_percent < 0:
            raise ValueError("margin fee bracket must not have a negative rate")
        if bracket.daily_fee and bracket.daily_percent:
            raise ValueError("margin fee bracket must use either rubles or percent, not both")
        expected_min = bracket.max_amount
    if expected_min != math.inf:
        raise ValueError(
            f"margin fee table ends at {expected_min!r}: amounts above it would be "
            "silently clipped"
        )


check_bracket_table(MARGIN_FEE_BRACKETS)


def get_margin_fee_bracket(borrowed_amount: float) -> MarginFeeBracket:
    """Диапазон тарифа для занятой суммы.

    Args:
        borrowed_amount: Сумма, занятая у брокера, в рублях; должна быть > 0.

    Returns:
        MarginFeeBracket: Соответствующий диапазон.

    Raises:
        ValueError: Сумма отрицательная, NaN, не число или равна нулю — диапазона
            для «нет заёма» нет, вызывающий код обязан вернуть 0 сам.
    """
    amount = _validate_number(borrowed_amount, "borrowed_amount")
    if amount == 0.0:
        raise ValueError("borrowed_amount must be positive; a covered position pays no fee")
    for bracket in MARGIN_FEE_BRACKETS:
        if bracket.min_amount < amount <= bracket.max_amount:
            return bracket
    # The table is validated on import, so this line is unreachable.
    raise ValueError(f"no margin fee bracket for {amount!r}")


def calculate_daily_margin_fee(borrowed_amount: float) -> float:
    """Плата за одни сутки переноса занятой суммы.

    Args:
        borrowed_amount: Сумма, занятая у брокера, в рублях.

    Returns:
        float: Плата в рублях за сутки; 0 для незанятой суммы.
    """
    amount = _validate_number(borrowed_amount, "borrowed_amount")
    if amount <= 0.0:
        return 0.0
    return get_margin_fee_bracket(amount).fee_for(amount)


def margin_nights(entry_time: datetime, exit_time: datetime) -> int:
    """Number of calendar days the position was carried over.

    The broker charges for every calendar day, weekends and holidays included:
    Friday -> Monday is 3 days. A calendar-date change is used as a proxy for the
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
    """Margin fee for holding a borrowed amount over `nights` calendar days.

    Args:
        position_amount: Position notional in rubles.
        own_funds: Own money allocated to the position (balance at entry).
        nights: Calendar days carried over; 0 for an intraday trade.
        is_short: A short is a securities loan, so the broker charges its full
            value (ADR-018).
        short_uncovered: Legacy override kept only to replay snapshots written
            before the tariff table revision. `None` (default) charges a short in
            full; `False` reproduces the old sensitivity mode where cash
            collateral covered the short.

    Returns:
        float: Fee in rubles; 0 when the position is covered by own funds or
        closed intraday.
    """
    position = _validate_number(position_amount, "position_amount")
    own = _validate_number(own_funds, "own_funds", allow_negative=True)
    if nights < 0:
        raise ValueError(f"nights must not be negative, got {nights!r}")

    treat_short_as_uncovered = True if short_uncovered is None else short_uncovered
    borrowed = (
        position
        if is_short and treat_short_as_uncovered
        else max(0.0, position - own)
    )

    if borrowed < MARGIN_MIN_BORROWED or nights == 0:
        return 0.0
    return calculate_daily_margin_fee(borrowed) * nights


@dataclass(frozen=True)
class TradeCommissions:
    """Broker fees of one trade, in rubles."""

    open: float = 0.0
    close: float = 0.0
    margin: float = 0.0

    @property
    def total(self) -> float:
        return self.open + self.close + self.margin


def calculate_trade_commissions(
    *,
    quantity: float,
    entry_price: float,
    exit_price: float | None,
    entry_time: datetime,
    exit_time: datetime | None,
    is_long: bool,
    own_funds: float | None = None,
    mode: str | None = None,
    short_uncovered: bool | None = None,
) -> TradeCommissions:
    """Transaction and margin fees of one trade — the only implementation.

    Used by `BaseStrategy._calculate_commissions()` (backtest) and by
    `scripts/cost_audit.py` (attribution), so the two cannot drift apart.

    Args:
        quantity: Position notional in rubles.
        entry_price: Raw entry price, used to scale the closing fee.
        exit_price: Execution exit price; `None` for an open position.
        entry_time: Entry timestamp.
        exit_time: Exit timestamp; `None` for an open position.
        is_long: Direction, selects the `COMMISSION_MODE` branch.
        own_funds: Own money at entry; defaults to `quantity` (fully covered).
        mode: Commission mode; defaults to the module `COMMISSION_MODE`.
        short_uncovered: Legacy replay override, see `calculate_margin_fee`.
    """
    commission_mode = COMMISSION_MODE if mode is None else mode
    if commission_mode not in COMMISSION_MODES:
        raise ValueError(
            f"commission mode must be one of {', '.join(COMMISSION_MODES)}, "
            f"got {commission_mode!r}"
        )
    if commission_mode == "NONE":
        return TradeCommissions()
    if commission_mode == "LONG_ONLY" and not is_long:
        return TradeCommissions()
    if commission_mode == "SHORT_ONLY" and is_long:
        return TradeCommissions()

    notional = _validate_number(quantity, "quantity", allow_negative=True)
    raw_entry = _validate_number(entry_price, "entry_price")
    if raw_entry <= 0.0:
        raise ValueError(f"entry_price must be positive, got {raw_entry!r}")

    rate = TRANSACTION_COMMISSION_PERCENT / 100.0
    open_fee = notional * rate
    close_fee = 0.0
    if exit_price is not None:
        raw_exit = _validate_number(exit_price, "exit_price")
        close_fee = notional * (raw_exit / raw_entry) * rate

    margin = 0.0
    if exit_time is not None:
        margin = calculate_margin_fee(
            notional,
            own_funds if own_funds is not None else notional,
            margin_nights(entry_time, exit_time),
            is_short=not is_long,
            short_uncovered=short_uncovered,
        )

    return TradeCommissions(open=open_fee, close=close_fee, margin=margin)


def describe_commissions() -> dict[str, object]:
    """Commission settings that affect PnL, written into every report snapshot.

    Sits next to `costs` in the baseline/walk-forward JSON: without the tariff
    table the same PnL number can come from two different models (ADR-018).
    """
    return {
        "mode": COMMISSION_MODE,
        "transaction_percent": TRANSACTION_COMMISSION_PERCENT,
        "margin_table_version": MARGIN_TABLE_VERSION,
        "margin_short_base": "notional",
        "margin_brackets": [
            {
                "max_amount": None if math.isinf(bracket.max_amount) else bracket.max_amount,
                "daily_fee": bracket.daily_fee,
                "daily_percent": bracket.daily_percent,
            }
            for bracket in MARGIN_FEE_BRACKETS
        ],
    }