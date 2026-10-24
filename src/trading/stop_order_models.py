"""
Stop Order Models

Модели для работы со стоп-ордерами.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class StopOrderStatus(Enum):
    """Статус стоп-ордера (то, что храним в БД)."""

    ACTIVE = "ACTIVE"        # Активен на бирже
    EXECUTED = "EXECUTED"    # Исполнен
    CANCELLED = "CANCELLED"  # Отменён (в т.ч. OCO)
    EXPIRED = "EXPIRED"      # Истёк
    UNKNOWN = "UNKNOWN"      # Неизвестный статус/не смогли распарсить


@dataclass
class StopOrderRecord:
    """
    Запись о стоп-ордере в БД.
    """
    # Идентификаторы
    stop_order_id: str          # ID ордера из API
    position_id: str            # ID позиции из API (связь с live_trades!)
    trade_id: str
    
    # Параметры
    order_type: str             # "TP" | "SL"
    ticker: str
    figi: str
    
    # Цены и объёмы
    stop_price: float
    lots_requested: int
    
    # Временные метки
    created_at: datetime
    last_checked_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    
    # Статус
    status: StopOrderStatus = StopOrderStatus.ACTIVE
    
    # Счёт
    account_id: str = ""
    account_id_short: str = ""  # Последние 4 символа
    
    # Дополнительно
    metadata: dict = field(default_factory=dict)
    id: Optional[int] = None
    
    def __post_init__(self):
        # Автоматически генерируем короткий ID счёта
        if self.account_id and not self.account_id_short:
            self.account_id_short = self.account_id[-4:]
    
    def __repr__(self):
        return (
            f"StopOrder({self.order_type} {self.ticker} @ {self.stop_price:.2f}, "
            f"status={self.status.value}, account=***{self.account_id_short})"
        )
