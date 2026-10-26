"""Tests for the shared trade simulation engine."""

from datetime import datetime, timedelta

import pytest

from src.backtest.simulator import ExitRules, SizingRules, TradeSimulator
from src.market_data.models import Candle
from src.strategies.base_strategy import Signal

START = datetime(2026, 1, 1, 12, 0, 0)


def make_candles(ohlc: list[tuple[float, float, float, float]]) -> list[Candle]:
    """Build candles from (open, high, low, close) tuples, one hour apart."""
    return [
        Candle(
            open_time=START + timedelta(hours=i),
            open=o,
            high=h,
            low=low,
            close=c,
            volume=100,
            is_complete=True,
        )
        for i, (o, h, low, c) in enumerate(ohlc)
    ]


def simple_pnl(trade, balance: float) -> None:
    """PnL without commissions: shares are bought for the whole position size."""
    shares = trade.quantity / trade.entry_price
    if trade.is_long():
        pnl = shares * (trade.exit_price - trade.entry_price)
    else:
        pnl = shares * (trade.entry_price - trade.exit_price)
    trade.pnl_abs = pnl
    trade.pnl_percent = pnl / trade.quantity * 100.0


def run(candles, signals, *, tp=10.0, sl=5.0, allow_short=True, entry_filter=None, percent=100.0):
    simulator = TradeSimulator(
        exit_rules=ExitRules(tp_percent=tp, sl_percent=sl),
        sizing=SizingRules(initial_balance=1000.0, position_size_percent=percent),
        on_close=simple_pnl,
        entry_filter=entry_filter,
        allow_short=allow_short,
    )
    return simulator.run(candles, signals)


def signal(index: int, signal_type: str = "BULLISH") -> Signal:
    return Signal(
        timestamp=START + timedelta(hours=index),
        candle_index=index,
        signal_type=signal_type,
        price=100.0,
        metadata={},
    )


def test_entry_is_next_candle_open():
    candles = make_candles([
        (100.0, 101.0, 99.0, 100.0),   # 0: signal candle
        (102.0, 103.0, 101.0, 102.5),  # 1: entry here, at open
    ])
    trades = run(candles, [signal(0)], tp=50.0, sl=50.0)

    assert len(trades) == 1
    assert trades[0].entry_index == 1
    assert trades[0].entry_price == 102.0
    assert trades[0].entry_time == candles[1].open_time


def test_long_take_profit():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 112.0, 100.0, 111.0),  # high 112 >= tp 110
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "TP"
    assert trades[0].exit_price == pytest.approx(110.0)
    assert trades[0].exit_index == 2
    assert trades[0].pnl_abs > 0


def test_long_stop_loss():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 101.0, 90.0, 91.0),  # low 90 <= sl 95
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(95.0)
    assert trades[0].pnl_abs < 0


def test_short_take_profit():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 85.0, 86.0),  # low 85 <= tp 90
    ])
    trades = run(candles, [signal(0, "BEARISH")], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "TP"
    assert trades[0].exit_price == pytest.approx(90.0)
    assert trades[0].pnl_abs > 0


def test_short_stop_loss():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 110.0, 99.0, 109.0),  # high 110 >= sl 105
    ])
    trades = run(candles, [signal(0, "BEARISH")], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(105.0)
    assert trades[0].pnl_abs < 0


def test_only_one_position_at_a_time():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ])
    # Three signals in a row: the first one opens, the rest are ignored
    trades = run(candles, [signal(0), signal(1), signal(2)], tp=50.0, sl=50.0)

    assert len(trades) == 1
    assert trades[0].entry_index == 1


def test_new_position_after_previous_closes():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),  # 0 signal 1
        (100.0, 100.5, 99.5, 100.0),   # 1 entry 1
        (100.0, 106.0, 99.5, 105.0),   # 2 TP (+10% would be 110; use tp=5 -> 105)
        (105.0, 105.5, 104.5, 105.0),  # 3 signal 2 -> entry at 4
        (105.0, 105.5, 104.5, 105.0),
    ])
    trades = run(candles, [signal(0), signal(3)], tp=5.0, sl=5.0)

    assert len(trades) == 2
    assert trades[0].exit_type == "TP"
    assert trades[1].entry_index == 4


def test_allow_short_false_skips_bearish_signals():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ])
    trades = run(candles, [signal(0, "BEARISH")], allow_short=False)

    assert trades == []


def test_entry_filter_can_reject():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ])
    trades = run(candles, [signal(0)], entry_filter=lambda idx, candle, sig: False)

    assert trades == []


def test_entry_filter_receives_entry_candle():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (102.0, 102.5, 101.5, 102.0),
    ])
    seen = {}

    def entry_filter(idx, candle, sig):
        seen["idx"] = idx
        seen["open"] = candle.open
        return True

    run(candles, [signal(0)], entry_filter=entry_filter)

    assert seen == {"idx": 1, "open": 102.0}


def test_open_position_is_returned_at_the_end():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ])
    trades = run(candles, [signal(0)], tp=50.0, sl=50.0)

    assert len(trades) == 1
    assert trades[0].is_open()
    assert trades[0].exit_price is None


def test_position_size_percent():
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
    ])
    trades = run(candles, [signal(0)], tp=50.0, sl=50.0, percent=25.0)

    assert trades[0].quantity == pytest.approx(250.0)


def test_long_stop_loss_wins_when_candle_touches_both_levels():
    """Same candle touches TP and SL: the pessimistic outcome (SL) is reported."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 112.0, 90.0, 91.0),  # high 112 >= tp 110, low 90 <= sl 95
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(95.0)
    assert trades[0].pnl_abs < 0


def test_short_stop_loss_wins_when_candle_touches_both_levels():
    """Mirror case for SHORT: SL at 105 and TP at 90 inside one candle."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 110.0, 85.0, 109.0),  # high 110 >= sl 105, low 85 <= tp 90
    ])
    trades = run(candles, [signal(0, "BEARISH")], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(105.0)
    assert trades[0].pnl_abs < 0


def test_long_gap_down_fills_stop_at_open():
    """Candle opens below SL: the level was never traded, fill is the open."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (90.0, 92.0, 88.0, 91.0),  # opens at 90, below SL 95
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(90.0)
    assert trades[0].pnl_abs < 0


def test_long_gap_up_fills_take_profit_at_open():
    """Candle opens above TP: fill is the open, which is better than the level."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (115.0, 118.0, 114.0, 117.0),  # opens at 115, above TP 110
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "TP"
    assert trades[0].exit_price == pytest.approx(115.0)


def test_short_gap_up_fills_stop_at_open():
    """Mirror case: SHORT candle opens above SL."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (110.0, 112.0, 109.0, 111.0),  # opens at 110, above SL 105
    ])
    trades = run(candles, [signal(0, "BEARISH")], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "SL"
    assert trades[0].exit_price == pytest.approx(110.0)
    assert trades[0].pnl_abs < 0


def test_short_gap_down_fills_take_profit_at_open():
    """Mirror case: SHORT candle opens below TP."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (85.0, 86.0, 84.0, 85.5),  # opens at 85, below TP 90
    ])
    trades = run(candles, [signal(0, "BEARISH")], tp=10.0, sl=5.0)

    assert trades[0].exit_type == "TP"
    assert trades[0].exit_price == pytest.approx(85.0)


def test_exit_time_is_the_close_of_the_bar_by_default():
    """Without bar_duration the raw candle open time is kept (legacy behaviour)."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 112.0, 100.0, 111.0),
    ])
    trades = run(candles, [signal(0)], tp=10.0, sl=5.0)

    assert trades[0].exit_time == candles[2].open_time


def test_exit_time_is_bar_close_when_bar_duration_is_given():
    """Exit is reported at the end of the bar that triggered it, not at its open."""
    candles = make_candles([
        (100.0, 100.0, 100.0, 100.0),
        (100.0, 101.0, 99.5, 100.5),
        (100.5, 112.0, 100.0, 111.0),
    ])
    simulator = TradeSimulator(
        exit_rules=ExitRules(tp_percent=10.0, sl_percent=5.0),
        sizing=SizingRules(initial_balance=1000.0, position_size_percent=100.0),
        on_close=simple_pnl,
        bar_duration=timedelta(hours=1),
    )
    trades = simulator.run(candles, [signal(0)])

    assert trades[0].exit_time == candles[2].open_time + timedelta(hours=1)


def test_exit_rules_levels():
    rules = ExitRules(tp_percent=2.0, sl_percent=1.0)

    assert rules.levels(100.0, is_long=True) == (pytest.approx(102.0), pytest.approx(99.0))
    assert rules.levels(100.0, is_long=False) == (pytest.approx(98.0), pytest.approx(101.0))
