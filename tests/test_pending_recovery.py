"""Recovery of stale PENDING live trades (T2-6 tail)."""

from datetime import datetime

from src.trading.models import LiveTrade, TradeStatus
from src.trading.pending_recovery import PendingRecovery

ACCOUNT = "2000123456"
FIGI = "BBG004730N88"
SIGNAL_TIME = datetime(2026, 10, 24, 12, 0, 0)


class Quotation:
    """Minimal stand-in for t_tech Quotation/MoneyValue."""

    def __init__(self, value: float):
        self.units = int(value)
        self.nano = int(round((value - int(value)) * 1e9))


class Position:
    def __init__(self, figi: str, quantity: float, price: float, lots: int):
        self.figi = figi
        self.quantity = Quotation(quantity)
        self.average_position_price = Quotation(price)
        self.quantity_lots = lots
        self.position_uid = f"pos-{figi}"


class Portfolio:
    def __init__(self, positions):
        self.securities = positions
        self.futures = []


class Operation:
    def __init__(self, op_type):
        self.type = op_type


class OperationsResponse:
    def __init__(self, operations):
        self.operations = operations


class Instrument:
    def __init__(self, lot: int):
        self.lot = lot


class InstrumentResponse:
    def __init__(self, lot: int):
        self.instrument = Instrument(lot)


class FakeOperations:
    def __init__(self, portfolio, operations=None, fail=False):
        self._portfolio = portfolio
        self._operations = operations or []
        self._fail = fail

    def get_portfolio(self, account_id):
        return self._portfolio

    def get_operations(self, **kwargs):
        if self._fail:
            raise RuntimeError("API is down")
        return OperationsResponse(self._operations)


class FakeInstruments:
    def get_instrument_by(self, **kwargs):
        return InstrumentResponse(lot=10)


class FakeClient:
    def __init__(self, portfolio, operations=None, fail=False):
        self.operations = FakeOperations(portfolio, operations, fail)
        self.instruments = FakeInstruments()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTradeRepository:
    def __init__(self, trades):
        self.trades = trades
        self.saved = []

    def get_stale_pending(self, older_than_minutes):
        return list(self.trades)

    def save(self, trade) -> bool:
        self.saved.append(trade)
        return True


def make_trade(**kwargs) -> LiveTrade:
    params = dict(
        trade_id="SBER_LONG_abc123",
        ticker="SBER",
        figi=FIGI,
        direction="LONG",
        timeframe="4h",
        timestamp=SIGNAL_TIME,
        signal_key=f"{FIGI}|4h|2026-10-24T12:00:00|LONG",
        status=TradeStatus.PENDING,
        account_id=ACCOUNT,
        tp_percent=2.3,
        sl_percent=2.2,
    )
    params.update(kwargs)
    return LiveTrade(**params)


def run_recovery(trade, client) -> tuple[FakeTradeRepository, object]:
    repository = FakeTradeRepository([trade])
    recovery = PendingRecovery(
        token="test-token",
        trade_repo=repository,
        client_factory=lambda token: client,
    )
    return repository, recovery.recover()


def test_position_on_exchange_adopts_pending_trade():
    """Position exists: the record becomes OPEN with the exchange data."""
    trade = make_trade()
    client = FakeClient(Portfolio([Position(FIGI, quantity=10.0, price=250.5, lots=1)]))

    repository, stats = run_recovery(trade, client)

    assert stats.adopted == 1
    assert trade.status is TradeStatus.OPEN
    assert trade.position_id == f"pos-{FIGI}"
    assert trade.entry_price == 250.5
    assert trade.lots == 1
    assert trade.lot_size == 10
    assert trade.position_size == 2505.0
    assert trade.signal_key is not None
    assert repository.saved == [trade]


def test_no_position_and_no_operations_cancels_and_frees_signal_key():
    """Order was never sent: cancel the record so the signal can be retried."""
    trade = make_trade()
    client = FakeClient(Portfolio([]), operations=[])

    _, stats = run_recovery(trade, client)

    assert stats.cancelled == 1
    assert trade.status is TradeStatus.CANCELLED
    assert trade.signal_key is None
    assert "recovered" in (trade.error_message or "")


def test_no_position_but_operations_keep_signal_key_for_review():
    """Opened and closed while we were down: a retry would duplicate the entry."""
    from t_tech.invest.schemas import OperationType

    trade = make_trade()
    client = FakeClient(
        Portfolio([]),
        operations=[Operation(OperationType.OPERATION_TYPE_SELL)],
    )

    _, stats = run_recovery(trade, client)

    assert stats.needs_review == 1
    assert trade.status is TradeStatus.PENDING
    assert trade.signal_key is not None
    assert "review" in (trade.error_message or "")


def test_operations_api_failure_is_treated_as_review():
    """Unknown exchange state must not free the key."""
    trade = make_trade()
    client = FakeClient(Portfolio([]), fail=True)

    _, stats = run_recovery(trade, client)

    assert stats.needs_review == 1
    assert trade.status is TradeStatus.PENDING
    assert trade.signal_key is not None


def test_short_position_uses_absolute_size():
    """SHORT positions come back with negative quantity: size stays positive."""
    trade = make_trade(direction="SHORT", signal_key=f"{FIGI}|4h|2026-10-24T12:00:00|SHORT")
    client = FakeClient(Portfolio([Position(FIGI, quantity=-10.0, price=250.0, lots=-1)]))

    _, stats = run_recovery(trade, client)

    assert stats.adopted == 1
    assert trade.lots == 1
    assert trade.position_size == 2500.0
