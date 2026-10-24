"""
Data access layer для Dashboard.

Чистые функции для загрузки свечей и работы с маппингами тикеров.
Вынесено из app_dashboard.py для устранения дублирования.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.market_data.history_manager import HistoryManager
from dashboard.config import TIMEFRAME_MAP, MIN_CANDLES_FOR_STRATEGY

logger = logging.getLogger(__name__)

# Глобальные ссылки — устанавливаются при инициализации dashboard
_history: Optional[HistoryManager] = None
_broker = None
_ticker_to_figi: dict[str, str] = {}
_figi_to_ticker: dict[str, str] = {}
_available_combinations: dict[str, list[str]] = {}


def init(history: HistoryManager, broker) -> None:
    """
    Инициализировать data-слой: сохранить ссылки и построить маппинги тикеров.

    Вызывается один раз при старте dashboard.
    """
    global _history, _broker, _ticker_to_figi, _figi_to_ticker, _available_combinations
    _history = history
    _broker = broker

    _available_combinations = history.get_available_combinations()
    logger.info(f"✓ Available combinations: {_available_combinations}")

    # Строим маппинги ticker ↔ figi
    session = history.Session()
    try:
        from src.market_data.history_manager import MarketData
        rows = session.query(MarketData.figi, MarketData.ticker).distinct().all()
        for figi, ticker in rows:
            _ticker_to_figi[ticker] = figi
            _figi_to_ticker[figi] = ticker
    finally:
        session.close()

    logger.info(f"✓ Created ticker mapping: {_ticker_to_figi}")


def get_available_combinations() -> dict[str, list[str]]:
    """Вернуть словарь {figi: [timeframe, ...]}."""
    return _available_combinations


def get_ticker(figi: str) -> str:
    """FIGI → тикер (или сам FIGI, если не найден)."""
    return _figi_to_ticker.get(figi, figi)


def get_figi_for_timeframe(timeframe: str) -> list[str]:
    """Все FIGI, для которых есть данные на указанном таймфрейме."""
    return [
        figi for figi, tfs in _available_combinations.items()
        if timeframe in tfs
    ]


def load_candles(
    figi: str,
    timeframe_str: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Candle]:
    """
    Загрузить и отфильтровать свечи для инструмента.

    Args:
        figi: Идентификатор инструмента
        timeframe_str: Строка таймфрейма ("1h", "4h", ...)
        start_date: Начальная дата ISO-формата (опционально)
        end_date: Конечная дата ISO-формата (опционально)

    Returns:
        Список свечей (пустой, если данных недостаточно)
    """
    if _history is None:
        raise RuntimeError("Data layer not initialized. Call dashboard.data.init() first.")

    timeframe = TIMEFRAME_MAP.get(timeframe_str, Timeframe.H1)
    candles = _history.get_candles(figi, timeframe)

    # Фильтр: только непустые свечи
    candles = [c for c in candles if c.volume and c.volume > 0]

    # Фильтр по датам
    if start_date:
        sd = datetime.strptime(start_date.split('T')[0], '%Y-%m-%d')
        candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]
    if end_date:
        ed = datetime.strptime(end_date.split('T')[0], '%Y-%m-%d')
        candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed + timedelta(days=1)]

    return candles


def has_enough_candles(candles: List[Candle], min_count: int = None) -> bool:
    """Достаточно ли свечей для анализа."""
    if min_count is None:
        min_count = MIN_CANDLES_FOR_STRATEGY
    return len(candles) >= min_count
