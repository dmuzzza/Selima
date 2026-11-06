"""Backtest vs live parity (T3-8).

Both paths run on the same candles and must agree on **decisions**: which bar
produced a signal, the direction, and the TP/SL percentages. They cannot agree
on everything, and the differences that remain are listed in
`KNOWN_DIVERGENCES` with a reason, a task reference and a review date.

What is compared
----------------
* `check_last_signal(prefix)` against `detect_signals(prefix)` for every bar.
* Live decision from `TradingScheduler._process_instrument` against the
  backtest trade of `TradeSimulator` for the same signal.
* TP/SL percentages, direction, signal bar, one-position rule, idempotency.

What is deliberately not compared
---------------------------------
* Execution price: live pays the market, the backtest pays `open` plus a cost
  model (ADR-016).
* Exit time and exit price: in live the exit happens at the broker through stop
  orders, so there is nothing to measure inside the test.
* Absolute TP/SL prices: the bases differ (see `KNOWN_DIVERGENCES`).

No network, no token, no PostgreSQL: candles are synthetic, the broker-side
components are stubs, and `TRADING_MODE` is patched to `live` in-process.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from config.trading_mode import TradingMode
from src.backtest.exit_policy import MissingExitPolicyError
from src.brokers.position_manager import TradeParameters
from src.brokers.trade_executor import TradeExecutionResult, TradeExecutor
from src.market_data.models import Candle
from src.strategies.dual_ma_crossover_after_testing import DualMACrossoverStrategy
from src.trading.models import TradeStatus
from src.trading.orchestrator import TradingOrchestrator

scheduler = pytest.importorskip("scheduler")

BASE_PRICE = 100.0
PERIOD = 80
AMPLITUDE = 0.02
BAR = timedelta(hours=4)
SBER_FIGI = "BBG004730N88"
CRU6_FIGI = "FUTCNY092600"


# ═══════════════════════════════════════════════════════════════════════════
# Известные расхождения: поле, причина, задача, дата пересмотра
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class KnownDivergence:
    """A difference that the parity test accepts on purpose.

    `review_by` keeps temporary exceptions from becoming permanent: a stale
    entry means the task was never closed.
    """

    field: str
    reason: str
    task: str
    review_by: str


KNOWN_DIVERGENCES: tuple[KnownDivergence, ...] = (
    KnownDivergence(
        field="tp_price / sl_price (absolute)",
        reason=(
            "live builds levels from the last trade price (trade_executor.py:143), "
            "the backtest from the raw candle open (ADR-017); percentages match"
        ),
        task="T3-8a",
        review_by="2027-05-01",
    ),
    KnownDivergence(
        field="tp_price / sl_price rounding",
        reason=(
            "live quantizes levels to min_price_increment, the backtest does not; "
            "follows from the different bases above"
        ),
        task="T3-8a",
        review_by="2027-05-01",
    ),
    KnownDivergence(
        field="entry_filter",
        reason=(
            "backtest applies entry_filter (EMA200 + adaptive deviation) on the entry "
            "bar, live never calls it: live takes trades the backtest rejects"
        ),
        task="T3-8b",
        review_by="2026-12-01",
    ),
    KnownDivergence(
        field="candles with volume == 0",
        reason=(
            "live drops zero-volume candles and requires >= 51 bars, the backtest uses "
            "every candle, so indicator values and signals can differ"
        ),
        task="T3-8b",
        review_by="2026-12-01",
    ),
    KnownDivergence(
        field="entry bar offset",
        reason=(
            "live acts on the last closed bar, the backtest enters at the open of the "
            "next bar (ADR-012): the same signal, one bar apart by construction"
        ),
        task="n/a (by design)",
        review_by="-",
    ),
    KnownDivergence(
        field="exit_time / exit_price",
        reason=(
            "live exits at the broker through stop orders, the backtest simulates the "
            "touch inside a bar; nothing to measure in live during the test"
        ),
        task="n/a (by design)",
        review_by="-",
    ),
)


def divergence(field: str) -> KnownDivergence:
    """Look up a registered divergence; an unregistered one is a test failure."""
    for entry in KNOWN_DIVERGENCES:
        if entry.field == field:
            return entry
    raise AssertionError(
        f"Unregistered backtest/live divergence: {field!r}. "
        "Add it to KNOWN_DIVERGENCES with a reason, a task and a review date."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Данные: детерминированные пересечения SMA50 x MA Adaptive
# ═══════════════════════════════════════════════════════════════════════════


def make_candles(
    count: int = 400,
    *,
    amplitude: float = AMPLITUDE,
    volume: int = 1_000,
    start: datetime | None = None,
) -> list[Candle]:
    """Smooth oscillation: MA14 crosses SMA50 every half period, so signals land
    on known bars (200, 240, 280, 320, 360) instead of wherever a random walk
    happens to go."""
    moment = start or datetime(2024, 1, 1)
    candles: list[Candle] = []
    for index in range(count):
        price = BASE_PRICE * (1 + amplitude * math.sin(2 * math.pi * index / PERIOD))
        candles.append(
            Candle(
                open_time=moment + index * BAR,
                open=price,
                high=price * 1.004,
                low=price * 0.996,
                close=price,
                volume=volume,
                is_complete=True,
            )
        )
    return candles


SIGNAL_BARS = (200, 240, 280, 320, 360)
TRADED_SIGNAL_BARS = (200, 280, 360)


def with_zero_volume(candles: list[Candle], every: int = 10) -> list[Candle]:
    """Same prices, but every `every`-th bar has no volume, like a stale bar."""
    return [
        Candle(
            open_time=candle.open_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=0 if index % every == 0 else candle.volume,
            is_complete=candle.is_complete,
        )
        for index, candle in enumerate(candles)
    ]


def live_view(candles: list[Candle]) -> list[Candle]:
    """The candle series live actually works with (scheduler.py:468)."""
    return [candle for candle in candles if candle.volume and candle.volume > 0]


def detect(candles: list[Candle], ticker: str, timeframe: str = "4h"):
    strategy = DualMACrossoverStrategy(timeframe=timeframe, config={"ticker": ticker})
    return strategy, strategy.detect_signals(candles)


def test_dataset_produces_the_expected_signals():
    """Guard: if the generator drifts, every parity number below is meaningless."""
    _, signals = detect(make_candles(), "SBER")

    assert [signal.candle_index for signal in signals] == list(SIGNAL_BARS)
    assert [signal.signal_type for signal in signals] == [
        "BEARISH",
        "BULLISH",
        "BEARISH",
        "BULLISH",
        "BEARISH",
    ]


def test_dataset_is_deterministic():
    first = make_candles()
    second = make_candles()

    assert [(c.open_time, c.open, c.close) for c in first] == [
        (c.open_time, c.open, c.close) for c in second
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Заглушки: брокер, репозиторий, менеджер позиций, исполнитель
# ═══════════════════════════════════════════════════════════════════════════


class FakeTradeRepo:
    """In-memory repository that mimics the unique index on `signal_key`."""

    def __init__(self, *, has_open: bool = False) -> None:
        self.has_open = has_open
        self.saved: list = []
        self.keys: set[str] = set()

    def has_open_trade_on_timeframe_accounts(self, **kwargs) -> bool:
        return self.has_open

    def get_open_trades_by_ticker(self, **kwargs) -> list:
        return []

    def create_pending_if_absent(self, trade) -> bool:
        if trade.signal_key in self.keys:
            return False
        self.keys.add(trade.signal_key)
        self.saved.append(trade)
        return True

    def save(self, trade) -> None:
        self.saved.append(trade)

    def open_trades(self) -> list:
        unique: dict[int, object] = {}
        for trade in self.saved:
            if trade.status is TradeStatus.OPEN:
                unique[id(trade)] = trade
        return list(unique.values())


class StubPositionManager:
    """Returns trade parameters without asking the broker for lots or limits."""

    def __init__(self, market_price: float) -> None:
        self.market_price = market_price
        self.last_params: TradeParameters | None = None

    def calculate_trade_parameters(self, **kwargs) -> TradeParameters:
        lot_size = 10
        lots = 10
        params = TradeParameters(
            ticker=kwargs["ticker"],
            figi=kwargs["figi"],
            instrument_name="stub",
            direction=kwargs["direction"],
            strategy_name=kwargs["strategy_name"],
            tp_percent=kwargs["tp_percent"],
            sl_percent=kwargs["sl_percent"],
            position_size=self.market_price * lot_size * lots,
            margin_required=self.market_price * lot_size * lots,
            leverage=1.0,
            lots=lots,
            lot_size=lot_size,
            last_price=self.market_price,
            lot_value=self.market_price * lot_size,
            min_price_increment=0.01,
            open_positions_count=0,
            remaining_positions=1,
        )
        self.last_params = params
        return params


class StubTradeExecutor(TradeExecutor):
    """Real level calculation, no orders: `_calculate_tp_price` stays the
    production code, only the network call is replaced."""

    def __init__(self, market_price: float, execution_price: float) -> None:
        super().__init__(token="stub-token", account_id="stub-account")
        self.market_price = market_price
        self.execution_price = execution_price

    def execute_trade(self, trade_params) -> TradeExecutionResult:
        tp_price = self._calculate_tp_price(
            trade_params.last_price,
            trade_params.direction,
            trade_params.tp_percent,
            trade_params.min_price_increment,
        )
        sl_price = self._calculate_sl_price(
            trade_params.last_price,
            trade_params.direction,
            trade_params.sl_percent,
            trade_params.min_price_increment,
        )
        return TradeExecutionResult(
            success=True,
            market_order_id="stub-market",
            execution_price=self.execution_price,
            executed_lots=trade_params.lots,
            tp_order_id="stub-tp",
            tp_price=tp_price,
            sl_order_id="stub-sl",
            sl_price=sl_price,
        )


class FakeAccountSelector:
    def get_all_accounts(self) -> list[str]:
        return ["acc-1234"]

    def get_account_id(self, direction: str) -> str:
        return "acc-1234"


def run_live(
    monkeypatch,
    candles: list[Candle],
    *,
    ticker: str,
    figi: str,
    market_price: float,
    execution_price: float | None = None,
    has_open_position: bool = False,
) -> SimpleNamespace:
    """Run one `_process_instrument` cycle with stubs and return what happened."""
    repo = FakeTradeRepo(has_open=has_open_position)
    created: list[TradingOrchestrator] = []

    def orchestrator_factory(**kwargs):
        # Real orchestrator: _process_single_signal, signal_key and statuses run.
        orchestrator = TradingOrchestrator(
            token="stub-token",
            account_id=kwargs["account_id"],
            strategy_name=kwargs["strategy_name"],
            timeframe=kwargs["timeframe"],
            repository=kwargs["repository"],
        )
        orchestrator.position_manager = StubPositionManager(market_price)  # type: ignore[assignment]
        orchestrator.trade_executor = StubTradeExecutor(
            market_price, execution_price if execution_price is not None else market_price
        )
        orchestrator.get_position_id = lambda figi, max_retries=3: "stub-position"  # type: ignore[method-assign,assignment]
        created.append(orchestrator)
        return orchestrator

    fake_self = SimpleNamespace(
        history_manager=SimpleNamespace(get_candles=lambda figi, timeframe: candles),
        account_selector=FakeAccountSelector(),
        _trade_repositories=lambda: (repo, None),
        token="stub-token",
    )
    instrument = SimpleNamespace(ticker=ticker, figi=figi)

    monkeypatch.setattr(scheduler, "TradingOrchestrator", orchestrator_factory)
    monkeypatch.setattr(scheduler, "TRADING_MODE", TradingMode.LIVE)
    monkeypatch.setattr(scheduler, "TEST_MODE", False)

    result = scheduler.TradingScheduler._process_instrument(
        fake_self, instrument, "4h", "acc-1234"
    )
    return SimpleNamespace(
        result=result,
        repo=repo,
        orchestrators=created,
        trades=repo.open_trades(),
    )


def backtest_trade_after_signal(
    candles: list[Candle], signal_index: int, ticker: str
):
    """Backtest trade opened by the signal at `signal_index` (entry at the next bar)."""
    strategy = DualMACrossoverStrategy(timeframe="4h", config={"ticker": ticker})
    trades = strategy.run(candles[: signal_index + 2])["trades"]
    for trade in trades:
        if trade.entry_index == signal_index + 1:
            return trade
    return None


def expected_levels(entry_price: float, direction: str, tp_percent: float, sl_percent: float):
    if direction == "LONG":
        return entry_price * (1 + tp_percent / 100), entry_price * (1 - sl_percent / 100)
    return entry_price * (1 - tp_percent / 100), entry_price * (1 + sl_percent / 100)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Паритет сигналов: check_last_signal == detect_signals на каждом баре
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(("ticker", "figi"), [("SBER", SBER_FIGI), ("CRU6", CRU6_FIGI)])
@pytest.mark.parametrize("dataset", ["all bars have volume", "every 10th bar has zero volume"])
def test_last_signal_matches_detect_signals_on_every_bar(ticker, figi, dataset):
    candles = make_candles()
    if dataset == "every 10th bar has zero volume":
        candles = with_zero_volume(candles)
    strategy = DualMACrossoverStrategy(timeframe="4h", config={"ticker": ticker})

    checked = 0
    for index in range(SIGNAL_BARS[0], len(candles)):
        prefix = candles[: index + 1]
        expected = [s for s in strategy.detect_signals(prefix) if s.candle_index == index]
        actual = strategy.check_last_signal(prefix)

        if not expected:
            assert actual is None, f"{ticker} bar {index}: unexpected signal {actual}"
            continue

        assert len(expected) == 1
        assert actual is not None, f"{ticker} bar {index}: signal lost"
        assert actual.signal_type == expected[0].signal_type
        assert actual.candle_index == expected[0].candle_index
        assert actual.timestamp == expected[0].timestamp
        checked += 1

    assert checked == len(SIGNAL_BARS)


def test_zero_volume_bars_shift_signals_between_paths():
    """Known parity defect (T3-8b): zero-volume bars are dropped only in live.

    Indicators are computed over a window of bars, so removing zero-volume bars
    changes the moving averages and moves the crossover to another bar. The
    backtest trades the bar live never sees.
    """
    candles = with_zero_volume(make_candles())
    strategy = DualMACrossoverStrategy(timeframe="4h", config={"ticker": "SBER"})

    backtest_signals = strategy.detect_signals(candles)
    live_signals = strategy.detect_signals(live_view(candles))

    backtest_times = [signal.timestamp for signal in backtest_signals]
    live_times = [signal.timestamp for signal in live_signals]

    assert len(candles) - len(live_view(candles)) == 40
    assert backtest_times != live_times, (
        "if these ever match, the zero-volume filter stopped affecting indicators "
        "and the T3-8b exception must be re-checked"
    )
    assert divergence("candles with volume == 0").task == "T3-8b"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Паритет решения: направление, проценты, бар сигнала
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(("ticker", "figi"), [("SBER", SBER_FIGI), ("CRU6", CRU6_FIGI)])
@pytest.mark.parametrize("signal_index", TRADED_SIGNAL_BARS)
def test_live_and_backtest_agree_on_direction_and_percentages(
    monkeypatch, ticker, figi, signal_index
):
    candles = make_candles()
    backtest = backtest_trade_after_signal(candles, signal_index, ticker)
    assert backtest is not None, "dataset must produce a backtest trade"

    live = run_live(
        monkeypatch,
        candles[: signal_index + 1],
        ticker=ticker,
        figi=figi,
        market_price=candles[signal_index].close,
    )

    assert live.result == {"signals_count": 1, "trades_count": 1}
    assert len(live.trades) == 1
    trade = live.trades[0]

    backtest_direction = "LONG" if backtest.is_long() else "SHORT"
    assert trade.direction == backtest_direction
    # Percentages come from config/tp_sl.json on both sides.
    assert trade.tp_percent > 0
    assert trade.sl_percent > 0

    # The signal bar is the same: live keys idempotency on the candle open time.
    signal = candles[signal_index]
    assert trade.signal_key is not None
    assert signal.open_time.replace(microsecond=0).isoformat() in trade.signal_key

    # Backtest levels are the configured percentages of the raw entry open.
    entry_open = candles[signal_index + 1].open
    expected_tp, expected_sl = expected_levels(
        entry_open, backtest_direction, trade.tp_percent, trade.sl_percent
    )
    assert backtest.tp_level == pytest.approx(expected_tp, rel=1e-9)
    assert backtest.sl_level == pytest.approx(expected_sl, rel=1e-9)


@pytest.mark.parametrize(("ticker", "figi"), [("SBER", SBER_FIGI), ("CRU6", CRU6_FIGI)])
def test_live_refuses_a_second_position_like_backtest(monkeypatch, ticker, figi):
    """One position at a time: the backtest skips the signal, live reports a duplicate."""
    candles = make_candles()

    # Bar 240 carries a BULLISH signal, but the trade opened at 201 is still open.
    backtest_trades = DualMACrossoverStrategy(
        timeframe="4h", config={"ticker": ticker}
    ).run(candles[:241])["trades"]
    assert len(backtest_trades) == 1
    assert backtest_trades[0].entry_index == 201

    live = run_live(
        monkeypatch,
        candles[:241],
        ticker=ticker,
        figi=figi,
        market_price=candles[240].close,
        has_open_position=True,
    )

    assert live.result == {"signals_count": 1, "trades_count": 0}
    assert live.trades == []


def test_live_is_idempotent_on_the_same_candle(monkeypatch):
    """A second cycle on the same closed bar must not create a second trade."""
    candles = make_candles()[:201]
    repo = FakeTradeRepo()
    created: list[TradingOrchestrator] = []

    def orchestrator_factory(**kwargs):
        orchestrator = TradingOrchestrator(
            token="stub-token",
            account_id=kwargs["account_id"],
            strategy_name=kwargs["strategy_name"],
            timeframe=kwargs["timeframe"],
            repository=kwargs["repository"],
        )
        orchestrator.position_manager = StubPositionManager(candles[-1].close)  # type: ignore[assignment]
        orchestrator.trade_executor = StubTradeExecutor(
            candles[-1].close, candles[-1].close
        )
        orchestrator.get_position_id = lambda figi, max_retries=3: "stub-position"  # type: ignore[method-assign,assignment]
        created.append(orchestrator)
        return orchestrator

    fake_self = SimpleNamespace(
        history_manager=SimpleNamespace(get_candles=lambda figi, timeframe: candles),
        account_selector=FakeAccountSelector(),
        _trade_repositories=lambda: (repo, None),
        token="stub-token",
    )
    instrument = SimpleNamespace(ticker="SBER", figi=SBER_FIGI)

    monkeypatch.setattr(scheduler, "TradingOrchestrator", orchestrator_factory)
    monkeypatch.setattr(scheduler, "TRADING_MODE", TradingMode.LIVE)
    monkeypatch.setattr(scheduler, "TEST_MODE", False)

    first = scheduler.TradingScheduler._process_instrument(
        fake_self, instrument, "4h", "acc-1234"
    )
    second = scheduler.TradingScheduler._process_instrument(
        fake_self, instrument, "4h", "acc-1234"
    )

    assert first == {"signals_count": 1, "trades_count": 1}
    assert second == {"signals_count": 1, "trades_count": 0}
    assert len(repo.open_trades()) == 1
    assert len(repo.keys) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. База уровней TP/SL: live — last_price, бэктест — сырой open
# ═══════════════════════════════════════════════════════════════════════════


def test_level_base_is_registered_and_differs(monkeypatch):
    """Live levels are a percentage of the market price, backtest of the open.

    The test asserts the difference instead of hiding it: the absolute prices
    must not match, the percentages must.
    """
    candles = make_candles()
    signal_index = 200
    entry_open = candles[signal_index + 1].open
    market_price = entry_open * 1.01  # live sees a price 1% above the open

    live = run_live(
        monkeypatch,
        candles[: signal_index + 1],
        ticker="SBER",
        figi=SBER_FIGI,
        market_price=market_price,
        execution_price=market_price,
    )
    backtest = backtest_trade_after_signal(candles, signal_index, "SBER")

    trade = live.trades[0]
    assert trade.tp_percent == pytest.approx(2.3)
    assert trade.sl_percent == pytest.approx(2.2)

    # Live: from last_price (rounded to the 0.01 tick by the real executor).
    live_tp, live_sl = expected_levels(
        market_price, trade.direction, trade.tp_percent, trade.sl_percent
    )
    assert trade.tp_price == pytest.approx(live_tp, abs=0.005)
    assert trade.sl_price == pytest.approx(live_sl, abs=0.005)

    # Backtest: from the raw open, no rounding.
    backtest_tp, backtest_sl = expected_levels(
        entry_open, trade.direction, trade.tp_percent, trade.sl_percent
    )
    assert backtest.tp_level == pytest.approx(backtest_tp, rel=1e-9)
    assert backtest.sl_level == pytest.approx(backtest_sl, rel=1e-9)

    # Same percentage, different base -> different absolute price.
    assert trade.tp_price != pytest.approx(backtest.tp_level, rel=1e-6)
    assert divergence("tp_price / sl_price (absolute)").task == "T3-8a"


def test_backtest_keeps_levels_on_the_raw_open_with_costs():
    """ADR-017 invariant: costs change the fill price, never the level."""
    candles = make_candles()
    plain = backtest_trade_after_signal(candles, 200, "SBER")

    from src.backtest.costs import BpsCosts

    strategy = DualMACrossoverStrategy(
        timeframe="4h",
        config={"ticker": "SBER", "costs": BpsCosts(spread_bps=10.0, slippage_bps=10.0)},
    )
    trades = strategy.run(candles[:202])["trades"]
    with_costs = next(t for t in trades if t.entry_index == 201)

    assert with_costs.tp_level == pytest.approx(plain.tp_level, rel=1e-12)
    assert with_costs.sl_level == pytest.approx(plain.sl_level, rel=1e-12)
    # The fill price moved, the level did not.
    assert with_costs.entry_price < plain.entry_price


# ═══════════════════════════════════════════════════════════════════════════
# 4. Регрессии известных дефектов
# ═══════════════════════════════════════════════════════════════════════════


def test_ticker_without_exit_policy_is_skipped_by_both_paths(monkeypatch):
    """ALRS 4h has no entry in config/tp_sl.json (T3-4a)."""
    candles = make_candles()

    with pytest.raises(MissingExitPolicyError):
        DualMACrossoverStrategy(timeframe="4h", config={"ticker": "ALRS"})

    live = run_live(
        monkeypatch,
        candles[:201],
        ticker="ALRS",
        figi="BBG004S68B31",
        market_price=candles[200].close,
    )

    assert live.result == {"signals_count": 0, "trades_count": 0}
    assert live.orchestrators == []  # never reached the broker


def test_live_takes_trades_the_backtest_rejects_by_entry_filter(monkeypatch):
    """Known parity defect (T3-8b): `entry_filter` runs only in the backtest.

    When the filter rejects an entry, the backtest has no trade while live still
    sends the order. This test locks the defect: it must fail when T3-8b is
    fixed, and the fix then removes the exception from KNOWN_DIVERGENCES.
    """
    candles = make_candles()
    signal_index = 200

    monkeypatch.setattr(
        DualMACrossoverStrategy, "entry_filter", lambda self, index, candle, signal: False
    )

    strategy = DualMACrossoverStrategy(timeframe="4h", config={"ticker": "SBER"})
    assert strategy.run(candles[: signal_index + 2])["trades"] == []

    live = run_live(
        monkeypatch,
        candles[: signal_index + 1],
        ticker="SBER",
        figi=SBER_FIGI,
        market_price=candles[signal_index].close,
    )

    assert live.result == {"signals_count": 1, "trades_count": 1}
    assert divergence("entry_filter").task == "T3-8b"


def test_zero_volume_bar_is_dropped_only_in_live(monkeypatch):
    """Known parity defect (T3-8b): live filters `volume > 0`, the backtest does not.

    The last bar carries the signal and has zero volume, so live never sees it.
    """
    candles = make_candles()[:201]
    candles[-1] = Candle(
        open_time=candles[-1].open_time,
        open=candles[-1].open,
        high=candles[-1].high,
        low=candles[-1].low,
        close=candles[-1].close,
        volume=0,
        is_complete=True,
    )

    strategy = DualMACrossoverStrategy(timeframe="4h", config={"ticker": "SBER"})
    assert [s.candle_index for s in strategy.detect_signals(candles)] == [200]

    live = run_live(
        monkeypatch,
        candles,
        ticker="SBER",
        figi=SBER_FIGI,
        market_price=candles[-1].close,
    )

    assert live.result == {"signals_count": 0, "trades_count": 0}
    assert divergence("candles with volume == 0").task == "T3-8b"


def test_allow_short_false_blocks_shorts_in_both_paths(monkeypatch):
    """Both paths refuse BEARISH when allow_short=False (T1-4 regression)."""
    candles = make_candles()[:201]

    strategy = DualMACrossoverStrategy(
        timeframe="4h", config={"ticker": "SBER", "allow_short": False}
    )
    assert strategy.check_last_signal(candles) is None
    assert strategy.run(candles)["trades"] == []

    # The scheduler builds its own strategy; assert the shared method is used.
    assert "check_last_signal" not in DualMACrossoverStrategy.__dict__


# ═══════════════════════════════════════════════════════════════════════════
# 5. Гигиена списка исключений
# ═══════════════════════════════════════════════════════════════════════════


def test_every_known_divergence_is_documented():
    fields = [entry.field for entry in KNOWN_DIVERGENCES]
    assert len(fields) == len(set(fields)), "duplicate field in KNOWN_DIVERGENCES"

    for entry in KNOWN_DIVERGENCES:
        assert entry.field.strip()
        assert len(entry.reason) > 20, f"{entry.field}: reason must explain why"
        assert entry.task.startswith("T") or entry.task == "n/a (by design)"
        if entry.task.startswith("T"):
            assert entry.review_by != "-", f"{entry.field}: temporary exception needs a review date"
            datetime.strptime(entry.review_by, "%Y-%m-%d")


def test_unregistered_divergence_is_rejected():
    with pytest.raises(AssertionError, match="Unregistered backtest/live divergence"):
        divergence("some new field nobody documented")
