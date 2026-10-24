"""
Dry-run проверка боевой логики сигналов БЕЗ отправки в VK.

Использует ТЕ ЖЕ компоненты, что и scheduler_notifier.py в проде:
- HistoryManager (реальные данные из БД, без моков)
- DualMACrossoverStrategy (реальная стратегия)
- Ту же фильтрацию и тот же порог len(candles) >= 201

Запуск:
    python dry_run_signal_check.py
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.data_updater import DataUpdater
from src.market_data.timeframe import Timeframe
from src.strategies.dual_ma_crossover import DualMACrossoverStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DryRunCheck")

load_dotenv()
TOKEN = os.getenv("TINKOFF_API_TOKEN")
CONFIG_PATH = "config/instruments.json"
TRADING_TIMEFRAMES = ["4h"]  # держим в синхроне со scheduler_notifier.py

TIMEFRAME_MAP = {"4h": Timeframe.H4}


def check_instrument(history_manager: HistoryManager, instrument, timeframe: str) -> dict:
    """Полностью повторяет боевую ветку _process_instrument, но без отправки VK."""
    tf_enum = TIMEFRAME_MAP.get(timeframe)
    if not tf_enum:
        return {"ticker": instrument.ticker, "status": "unknown_timeframe"}

    candles = history_manager.get_candles(instrument.figi, tf_enum)
    candles = [c for c in candles if c.is_complete]  # см. фикс п.2

    if not candles or len(candles) < 201:
        return {"ticker": instrument.ticker, "status": "insufficient_data", "count": len(candles)}

    strategy = DualMACrossoverStrategy(timeframe=timeframe, config=None, debug=False)
    signals = strategy.detect_signals(candles)

    last_idx = len(candles) - 1
    signal = next((s for s in signals if s.candle_index == last_idx), None)

    if not signal:
        return {"ticker": instrument.ticker, "status": "no_signal", "last_candle_time": candles[-1].open_time}

    return {
        "ticker": instrument.ticker,
        "status": "SIGNAL_WOULD_BE_SENT",
        "signal_type": signal.signal_type,
        "price": signal.price,
        "candle_time": candles[-1].open_time,
        "tp_percent": strategy.tp_percent,
        "sl_percent": strategy.sl_percent,
    }


def main():
    broker = BrokerFactory.create("sdk", TOKEN)
    history_manager = HistoryManager(broker)
    data_updater = DataUpdater(token=TOKEN, config_path=CONFIG_PATH)

    instruments = data_updater.get_enabled_instruments()
    logger.info(f"Проверка {len(instruments)} инструментов на {TRADING_TIMEFRAMES}")

    for timeframe in TRADING_TIMEFRAMES:
        print(f"\n{'='*70}\nТАЙМФРЕЙМ: {timeframe}\n{'='*70}")
        for instrument in instruments:
            if timeframe not in instrument.timeframes:
                continue
            result = check_instrument(history_manager, instrument, timeframe)
            print(result)


if __name__ == "__main__":
    main()
