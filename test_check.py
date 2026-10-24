from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe
from src.brokers.broker_factory import BrokerFactory
from dotenv import load_dotenv
import os
load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")


broker = BrokerFactory.create("sdk", token)
history = HistoryManager(broker)
candles = history.get_candles(
    figi='BBG000R04X57', timeframe=Timeframe.H4)

candles = [candle for candle in candles if candle.volume and candle.volume > 0]

print(candles)