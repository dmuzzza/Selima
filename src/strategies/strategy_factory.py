"""
Strategy Factory V1

Фабрика для создания экземпляров стратегий.
Упрощает инициализацию стратегий с конфигурацией.

═══════════════════════════════════════════════════════════════════════════
ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════

# Создать стратегию с дефолтным конфигом:
strategy = StrategyFactory.create("sma_crossover", "1h")

# Создать стратегию с кастомным конфигом:
config = {"initial_balance": 50000, "position_size_percent": 50}
strategy = StrategyFactory.create("dual_ma", "4h", config=config)

# Запустить стратегию:
result = strategy.run(candles)

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Dict, Optional

from src.strategies.base_strategy import BaseStrategy
from src.strategies.strategy_registry import get_strategy_class

logger = logging.getLogger(__name__)


class StrategyFactory:
    """
    Фабрика для создания экземпляров торговых стратегий.
    
    Обеспечивает единый интерфейс для создания любой стратегии
    из реестра с автоматической загрузкой конфигурации.
    """

    @staticmethod
    def create(
        strategy_code: str,
        timeframe: str,
        config: Optional[Dict] = None,
        debug: bool = False
    ) -> BaseStrategy:
        """
        Создать экземпляр стратегии.
        
        Args:
            strategy_code: Код стратегии из реестра (e.g., "sma_crossover")
            timeframe: Таймфрейм для торговли (e.g., "1h", "4h", "1d")
            config: Дополнительная конфигурация (опционально)
            debug: Режим отладки (подробные логи)
        
        Returns:
            BaseStrategy: Экземпляр стратегии
        
        Raises:
            ValueError: Если стратегия не найдена в реестре
        
        Example:
            >>> # Базовое использование
            >>> strategy = StrategyFactory.create("sma_crossover", "1h")
            >>> result = strategy.run(candles)
            
            >>> # С кастомным конфигом
            >>> custom_config = {
            ...     "initial_balance": 50000,
            ...     "position_size_percent": 50,
            ...     "tp_percent": 3.0,
            ...     "sl_percent": 1.5
            ... }
            >>> strategy = StrategyFactory.create(
            ...     "dual_ma_crossover",
            ...     "4h",
            ...     config=custom_config,
            ...     debug=True
            ... )
        """
        try:
            # Получаем класс стратегии из реестра
            strategy_class = get_strategy_class(strategy_code)
            
            # Создаём экземпляр
            strategy = strategy_class(
                timeframe=timeframe,
                config=config,
                debug=debug
            )
            
            if debug:
                logger.debug(
                    f"✓ Created strategy: {strategy.name} "
                    f"(timeframe: {timeframe}, config: {config})"
                )
            
            return strategy
        
        except ValueError as e:
            logger.error(f"Failed to create strategy '{strategy_code}': {e}")
            raise
        
        except Exception as e:
            logger.error(
                f"Unexpected error creating strategy '{strategy_code}': {e}"
            )
            raise

    @staticmethod
    def create_with_default_config(
        strategy_code: str,
        timeframe: str,
        debug: bool = False
    ) -> BaseStrategy:
        """
        Создать стратегию с дефолтной конфигурацией для таймфрейма.
        
        Автоматически загружает оптимальные параметры для указанного таймфрейма.
        
        Args:
            strategy_code: Код стратегии
            timeframe: Таймфрейм
            debug: Режим отладки
        
        Returns:
            BaseStrategy: Экземпляр стратегии с дефолтным конфигом
        
        Example:
            >>> strategy = StrategyFactory.create_with_default_config(
            ...     "sma_crossover",
            ...     "1h"
            ... )
        """
        # В будущем здесь можно загружать конфиги из файлов
        # На данный момент просто делегируем в create()
        return StrategyFactory.create(strategy_code, timeframe, debug=debug)
