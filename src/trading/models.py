"""
Live Trading Models

Модели данных для реальной торговли (не симуляции).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


def build_signal_key(
    *,
    figi: str,
    timeframe: str,
    candle_open_time: datetime,
    direction: str,
) -> str:
    """
    Build a stable idempotency key for one trading signal.

    One signal (instrument + timeframe + candle + direction) must produce at most
    one live trade, even if the scheduler runs several times or restarts between
    sending the order and recording it. Microseconds are dropped so that the key
    survives serialization round-trips (database, logs).
    """
    candle = candle_open_time.replace(microsecond=0).isoformat()
    return f"{figi}|{timeframe}|{candle}|{direction}"


class TradeStatus(Enum):
    """Статус реальной сделки."""
    PENDING = "PENDING"           # Ожидает исполнения
    OPEN = "OPEN"                 # Открыта
    CLOSED = "CLOSED"             # Закрыта (TP или SL)
    FAILED = "FAILED"             # Ошибка при открытии
    CANCELLED = "CANCELLED"       # Отменена


class OrderType(Enum):
    """Тип ордера."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class LiveTrade:
    """
    Реальная торговая сделка (не симуляция).
    Расширенная версия Trade для реальной торговли.
    """
    # Идентификаторы
    trade_id: str                                # Наш внутренний ID
    position_id: Optional[str] = None           # ⭐ НОВОЕ: ID позиции из API
    
    # Метаданные
    strategy_name: str = ""
    timeframe: str = ""
    timestamp: datetime = None
    
    # Инструмент
    ticker: str = ""
    figi: str = ""
    
    # Направление
    direction: str = ""                         # "LONG" | "SHORT"

    # Идемпотентность: один сигнал -> максимум одна сделка (unique index в БД)
    signal_key: Optional[str] = None
    
    # Вход
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    lots: Optional[int] = None
    lot_size: Optional[int] = 1
    position_size: Optional[float] = None
    
    # Выход
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_type: Optional[str] = None             # "TP" | "SL" | "MANUAL"
    
    # PnL
    pnl_abs: Optional[float] = None
    pnl_percent: Optional[float] = None
    
    # TP/SL
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_percent: float = 0.0
    sl_percent: float = 0.0
    
    # Статус
    status: TradeStatus = TradeStatus.PENDING
    
    # Счёт
    account_id: str = ""
    account_id_short: str = ""                  # ⭐ НОВОЕ: Последние 4 символа
    
    # Флаги
    orders_collected: bool = False              # ⭐ НОВОЕ: Ордера собраны
    
    # Ошибки
    error_message: Optional[str] = None
    
    # Дополнительно
    broker: str = "tinkoff"
    metadata: dict = field(default_factory=dict)
    
    def __post_init__(self):
        # Автоматически генерируем короткий ID счёта
        if self.account_id and not self.account_id_short:
            self.account_id_short = self.account_id[-4:]
    
    def __repr__(self):
        return (
            f"LiveTrade({self.trade_id}, {self.ticker} {self.direction}, "
            f"{self.status.value}, "
            f"size={self.position_size or 'N/A'}₽, "
            f"account=***{self.account_id_short})"
        )
