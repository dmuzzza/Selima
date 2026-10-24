import logging
from typing import List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.market_data.models import Candle
from src.market_data.timeframe import Timeframe
from src.market_data.history_manager import HistoryManager
from src.strategies.base_strategy import BaseStrategy, Signal

logger = logging.getLogger(__name__)


class OrderType(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """Ордер на покупку/продажу"""
    type: OrderType
    figi: str
    timestamp: datetime
    entry_price: float
    quantity: int = 1
    
    def __repr__(self):
        return f"Order({self.type.value}, {self.figi}, price={self.entry_price})"


@dataclass
class Trade:
    """Закрытая сделка"""
    figi: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int = 1
    
    @property
    def profit_percent(self) -> float:
        """Прибыль в процентах"""
        return ((self.exit_price - self.entry_price) / self.entry_price) * 100
    
    @property
    def profit_absolute(self) -> float:
        """Прибыль в абсолютном значении"""
        return (self.exit_price - self.entry_price) * self.quantity
    
    @property
    def duration_hours(self) -> float:
        """Длительность сделки в часах"""
        delta = self.exit_time - self.entry_time
        return delta.total_seconds() / 3600
    
    def __repr__(self):
        return (
            f"Trade({self.figi}, {self.entry_time.strftime('%Y-%m-%d %H:%M')} → "
            f"{self.exit_time.strftime('%Y-%m-%d %H:%M')}, "
            f"Entry: {self.entry_price}, Exit: {self.exit_price}, "
            f"PnL: {self.profit_percent:.2f}%)"
        )


@dataclass
class BacktestResult:
    """Результаты бэктеста"""
    figi: str
    strategy_name: str
    timeframe: Timeframe
    start_time: datetime
    end_time: datetime
    
    trades: List[Trade] = field(default_factory=list)
    initial_capital: float = 10000.0  # Начальный капитал в рублях
    
    @property
    def total_trades(self) -> int:
        """Количество сделок"""
        return len(self.trades)
    
    @property
    def winning_trades(self) -> int:
        """Количество прибыльных сделок"""
        return sum(1 for t in self.trades if t.profit_percent > 0)
    
    @property
    def losing_trades(self) -> int:
        """Количество убыточных сделок"""
        return sum(1 for t in self.trades if t.profit_percent < 0)
    
    @property
    def win_rate(self) -> float:
        """Процент выигрышных сделок"""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100
    
    @property
    def total_profit_percent(self) -> float:
        """Общая прибыль в процентах"""
        if self.total_trades == 0:
            return 0.0
        total_profit = sum(t.profit_absolute for t in self.trades)
        return (total_profit / self.initial_capital) * 100
    
    @property
    def avg_profit_per_trade(self) -> float:
        """Средняя прибыль на сделку в %"""
        if self.total_trades == 0:
            return 0.0
        return sum(t.profit_percent for t in self.trades) / self.total_trades
    
    @property
    def max_loss(self) -> float:
        """Максимальный убыток в %"""
        if not self.trades:
            return 0.0
        return min((t.profit_percent for t in self.trades), default=0.0)
    
    @property
    def max_gain(self) -> float:
        """Максимальная прибыль в %"""
        if not self.trades:
            return 0.0
        return max((t.profit_percent for t in self.trades), default=0.0)
    
    def print_summary(self):
        """Вывести сводку результатов"""
        print("\n" + "=" * 80)
        print(f"BACKTEST RESULT: {self.strategy_name} ({self.timeframe.value})")
        print("=" * 80)
        print(f"Инструмент: {self.figi}")
        print(f"Период: {self.start_time.strftime('%Y-%m-%d')} → {self.end_time.strftime('%Y-%m-%d')}")
        print(f"Начальный капитал: {self.initial_capital:,.2f} ₽")
        print()
        print(f"Total Trades:      {self.total_trades}")
        print(f"  ✓ Winning:       {self.winning_trades} ({self.win_rate:.1f}%)")
        print(f"  ✗ Losing:        {self.losing_trades} ({100-self.win_rate:.1f}%)")
        print()
        print(f"Profitability:")
        print(f"  Total PnL:       {self.total_profit_percent:+.2f}%")
        print(f"  Avg per trade:   {self.avg_profit_per_trade:+.2f}%")
        print(f"  Max gain:        {self.max_gain:+.2f}%")
        print(f"  Max loss:        {self.max_loss:+.2f}%")
        print("=" * 80)


class Backtester:
    """
    Симулятор торговли на исторических данных.
    
    Логика:
    1. Получает свечи из HistoryManager
    2. Для каждой свечи вызывает analyze() стратегии
    3. На основе сигналов открывает/закрывает сделки
    4. Подсчитывает метрики
    """
    
    def __init__(self, history_manager: HistoryManager):
        self.history = history_manager
        logger.info("✓ Backtester initialized")
    
    def run(self,
            figi: str,
            strategy: BaseStrategy,
            timeframe: Timeframe,
            initial_capital: float = 10000.0) -> BacktestResult:
        """
        Запустить бэктест стратегии на инструменте.
        
        Args:
            figi: Инструмент (BBG004730N88, etc)
            strategy: Экземпляр стратегии
            timeframe: Таймфрейм анализа
            initial_capital: Начальный капитал
        
        Returns:
            BacktestResult: Результаты тестирования
        """
        logger.info(f"Starting backtest: {strategy.name} on {figi} {timeframe.value}")
        
        # Получаем все свечи из БД
        candles = self.history.get_candles(figi, timeframe)
        
        if not candles:
            logger.error(f"No candles found for {figi} {timeframe.value}")
            return None
        
        logger.info(f"Loaded {len(candles)} candles for analysis")
        
        # Инициализируем результаты
        result = BacktestResult(
            figi=figi,
            strategy_name=strategy.name,
            timeframe=timeframe,
            start_time=candles[0].open_time,
            end_time=candles[-1].open_time,
            initial_capital=initial_capital
        )
        
        open_position = None  # Текущая открытая позиция (Order)
        
        # Симулируем торговлю на каждой свече
        for i, candle in enumerate(candles):
            # Получаем свечи для анализа (все до текущей включительно)
            history_window = candles[:i+1]
            
            # Анализируем
            signal = strategy.analyze(history_window)
            
            # Обрабатываем сигнал
            if signal.type == "BUY" and open_position is None:
                # Открываем позицию
                open_position = Order(
                    type=OrderType.BUY,
                    figi=figi,
                    timestamp=candle.open_time,
                    entry_price=candle.close
                )
                logger.debug(f"[BUY] at {candle.open_time}: {candle.close} ₽")
            
            elif signal.type == "SELL" and open_position is not None:
                # Закрываем позицию
                trade = Trade(
                    figi=figi,
                    entry_time=open_position.timestamp,
                    exit_time=candle.open_time,
                    entry_price=open_position.entry_price,
                    exit_price=candle.close
                )
                result.trades.append(trade)
                logger.debug(f"[SELL] at {candle.open_time}: {candle.close} ₽ (PnL: {trade.profit_percent:+.2f}%)")
                open_position = None
        
        # Если остается открытая позиция, закрываем её по последней цене
        if open_position is not None:
            last_candle = candles[-1]
            trade = Trade(
                figi=figi,
                entry_time=open_position.timestamp,
                exit_time=last_candle.open_time,
                entry_price=open_position.entry_price,
                exit_price=last_candle.close
            )
            result.trades.append(trade)
            logger.debug(f"[CLOSE] at {last_candle.open_time}: {last_candle.close} ₽ (PnL: {trade.profit_percent:+.2f}%)")
        
        logger.info(f"Backtest completed: {result.total_trades} trades")
        return result
