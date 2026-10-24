from abc import ABC, abstractmethod
from datetime import datetime
from typing import List
from src.market_data.models import Candle


class AbstractBroker(ABC):
    """Абстрактный интерфейс брокера - работает с SDK и gRPC"""

    @abstractmethod
    def get_candles(
        self,
        figi: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Candle]:
        pass

    @abstractmethod
    def get_accounts(self) -> list:
        pass