"""
Base Strategy Class V1

Базовый класс для всех торговых стратегий.
Определяет единый интерфейс и общую логику.

═══════════════════════════════════════════════════════════════════════════
АРХИТЕКТУРА:
═══════════════════════════════════════════════════════════════════════════

Все стратегии наследуются от BaseStrategy и реализуют:
1. detect_signals() - определение сигналов
2. execute_trades() - симуляция торговли
3. name - имя стратегии
4. description - описание

Общие методы (уже реализованы):
- calculate_stats() - расчёт статистики
- run() - запуск полного цикла
- _calculate_pnl() - расчёт PnL

═══════════════════════════════════════════════════════════════════════════
"""

import logging
import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config.commissions import (
    COMMISSION_MODE,
    TRANSACTION_COMMISSION_PERCENT,
    calculate_daily_margin_fee,
)
from src.market_data.models import Candle

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 📊 УНИВЕРСАЛЬНЫЕ DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Signal:
    """Универсальный торговый сигнал"""
    
    timestamp: datetime
    candle_index: int
    signal_type: str  # "BULLISH" | "BEARISH"
    price: float
    metadata: Dict = None  # Дополнительная информация (индикаторы, уровни и т.д.)

    def __repr__(self):
        return f"Signal({self.signal_type} @ idx={self.candle_index}, price={self.price:.2f})"


@dataclass
class Trade:
    """Универсальная торговая сделка"""
    
    trade_id: int
    entry_time: datetime
    entry_index: int
    entry_price: float
    signal_type: str  # "BULLISH" -> LONG, "BEARISH" -> SHORT

    tp_level: float
    sl_level: float
    quantity: float

    # Выход
    exit_time: Optional[datetime] = None
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_type: Optional[str] = None  # "TP" | "SL"

    # Безубыток
    breakeven_triggered: bool = False

    pnl_percent: Optional[float] = None
    pnl_abs: Optional[float] = None

     # ✅ НОВЫЕ ПОЛЯ ДЛЯ MFE/MAE
    mfe_percent: Optional[float] = None  # Maximum Favorable Excursion (макс. потенциальная прибыль)
    mae_percent: Optional[float] = None  # Maximum Adverse Excursion (макс. потенциальный убыток)

    # Движение цены после входа за 25 свечей
    post_entry_up_move: Optional[float] = None
    post_entry_down_move: Optional[float] = None

    # ✅ КОМИССИИ (для отладки и метрик)
    commission_open: float = 0.0      # Комиссия за открытие
    commission_close: float = 0.0     # Комиссия за закрытие
    commission_margin: float = 0.0    # Маржинальная комиссия (за дни)
    commission_total: float = 0.0     # Итого комиссий
    
    # PnL до и после комиссий
    pnl_gross: Optional[float] = None   # PnL до комиссий
    pnl_net: Optional[float] = None     # PnL после комиссий

    # Название паттерна (для candle_patterns и др. стратегий)
    pattern_name: Optional[str] = None

    def is_open(self) -> bool:
        """Открыта ли сделка?"""
        return self.exit_price is None

    def is_long(self) -> bool:
        """Это LONG позиция?"""
        return self.signal_type == "BULLISH"

    def is_short(self) -> bool:
        """Это SHORT позиция?"""
        return self.signal_type == "BEARISH"

    def __repr__(self):
        status = "OPEN" if self.is_open() else f"CLOSED ({self.exit_type})"
        direction = "LONG" if self.is_long() else "SHORT"
        pnl_str = f"{self.pnl_percent:+.2f}%" if self.pnl_percent else "N/A"
        return (
            f"Trade#{self.trade_id} {direction} {status} | "
            f"Entry: {self.entry_price:.2f} | Exit: {self.exit_price or 'N/A'} | "
            f"PnL: {pnl_str}"
        )


@dataclass
class StrategyStats:
    """Универсальная статистика стратегии"""
    
    total_trades: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_win_abs: float = 0.0   
    avg_loss_abs: float = 0.0 

    avg_hold_days: float = 0.0   # среднее дней удержания

    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    final_balance: float = 0.0

    # Целесообразность стратегии
    expectancy: float = 0.0
    profit_factor: float = 0.0
    payoff_ratio: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: Optional[float] = None
    z_score: Optional[float] = None

    # Среднее движение цены после входа (за 25 свечей)
    median_long_up_move: float = 0.0
    median_short_down_move: float = 0.0

    def __repr__(self):
        return (
            f"StrategyStats:\n"
            f"  Total trades: {self.total_trades}\n"
            f"  Closed trades: {self.closed_trades}\n"
            f"  Winning: {self.winning_trades} | Losing: {self.losing_trades}\n"
            f"  Win rate: {self.win_rate:.2f}%\n"
            f"  Avg win: {self.avg_win:.2f}% | Avg loss: {self.avg_loss:.2f}%\n"
            f"  Total PnL: {self.total_pnl:.2f}₽ ({self.total_pnl_percent:+.2f}%)\n"
            f"  Final balance: {self.final_balance:.2f}₽"
            f"  Avg hold: {self.avg_hold_days:.1f} days\n"
            f"  Median long up move: {self.median_long_up_move:.2f}%\n"
            f"  Median short down move: {self.median_short_down_move:.2f}%\n"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 🏗️ БАЗОВЫЙ КЛАСС СТРАТЕГИИ
# ═══════════════════════════════════════════════════════════════════════════


class BaseStrategy(ABC):
    """
    Базовый класс для всех торговых стратегий.
    
    Каждая стратегия должна реализовать:
    - detect_signals() - логика определения сигналов
    - execute_trades() - логика симуляции торговли
    - name - имя стратегии для UI
    - description - описание стратегии
    """

    def __init__(self, timeframe: str, config: Dict = None, debug: bool = False):
        """
        Инициализация базовой стратегии.
        
        Args:
            timeframe: Таймфрейм (e.g., "1h", "4h", "1d")
            config: Конфигурация стратегии (опционально)
            debug: Режим отладки
        """
        self.timeframe = timeframe
        self.config = config or {}
        self.debug = debug
        self.logger = logging.getLogger(self.__class__.__name__)

        # Данные стратегии
        self.signals: List[Signal] = []
        self.trades: List[Trade] = []
        self.stats: Optional[StrategyStats] = None

        # Параметры из конфига
        self.initial_balance = self.config.get("initial_balance", 100000.0)
        self.position_size_percent = self.config.get("position_size_percent", 100.0)

        # ✅ Режим торговли: allow_short=False → только LONG
        self.allow_short = self.config.get("allow_short", True)

        # TP/SL для live-торговли: подкласс может переопределить своими значениями
        self.tp_percent = self.config.get("tp_percent", 1.5)
        self.sl_percent = self.config.get("sl_percent", 1.0)

        if self.debug:
            self.logger.debug(f"{self.name} initialized for {timeframe}")

    # ═══════════════════════════════════════════════════════════════════════
    # АБСТРАКТНЫЕ МЕТОДЫ (должны быть реализованы в подклассах)
    # ═══════════════════════════════════════════════════════════════════════

    @abstractmethod
    def detect_signals(self, candles: List[Candle]) -> List[Signal]:
        """
        Определить торговые сигналы на основе свечей.
        
        Args:
            candles: Список свечей
        
        Returns:
            List[Signal]: Список сигналов
        """
        pass

    def entry_filter(self, candle_index: int, candle: Candle, signal: Signal) -> bool:
        """
        Разрешить или запретить вход по сигналу (хук для подклассов).

        Вызывается симулятором на свече входа, то есть на свече после сигнальной:
        здесь доступны цена открытия этой свечи и значения индикаторов с индексом
        `candle_index`.

        Args:
            candle_index: Индекс свечи, на которой открывается позиция
            candle: Сама свеча (цена входа — `candle.open`)
            signal: Сигнал, породивший попытку входа

        Returns:
            bool: True — вход разрешён, False — сигнал пропускается
        """
        return True

    def execute_trades(self, candles: List[Candle]) -> List[Trade]:
        """
        Симулировать торговлю на основе сигналов (общий движок для всех стратегий).

        Единственная реализация цикла TP/SL — `src/backtest/simulator.py`.
        Стратегия не должна переопределять этот метод: различия в критериях входа
        задаются через `detect_signals()` и `entry_filter()`, а TP/SL — через
        `tp_percent`/`sl_percent` в конфиге.

        Args:
            candles: Список свечей

        Returns:
            List[Trade]: Список сделок (закрытые + открытая на конец периода)
        """
        # Импорт внутри метода: simulator импортирует модели из этого модуля.
        from src.backtest.simulator import ExitRules, SizingRules, TradeSimulator

        if not self.signals:
            self.logger.warning("No signals to execute.")
            self.trades = []
            return []

        simulator = TradeSimulator(
            exit_rules=ExitRules(tp_percent=self.tp_percent, sl_percent=self.sl_percent),
            sizing=SizingRules(
                initial_balance=self.initial_balance,
                position_size_percent=self.position_size_percent,
            ),
            on_close=self._calculate_pnl,
            entry_filter=self.entry_filter,
            allow_short=self.allow_short,
            bar_duration=self._bar_duration(),
            logger=self.logger,
            debug=self.debug,
        )

        trades = simulator.run(candles, self.signals)
        self.trades = trades

        closed = len([t for t in trades if not t.is_open()])
        self.logger.info(f"Executed {len(trades)} trades ({closed} closed)")
        return trades

    def _bar_duration(self) -> Optional[timedelta]:
        """
        Длительность одной свечи таймфрейма стратегии.

        Нужна симулятору, чтобы записать `exit_time` как конец бара, в котором
        сработал TP/SL (внутрибарное время из OHLC неизвестно). Для неизвестного
        таймфрейма возвращается None — симулятор оставит время открытия свечи.
        """
        from src.market_data.timeframe import Timeframe

        try:
            return Timeframe(self.timeframe).to_timedelta
        except ValueError:
            self.logger.warning(
                f"Unknown timeframe '{self.timeframe}': exit_time stays at candle open"
            )
            return None

    @property
    @abstractmethod
    def name(self) -> str:
        """Имя стратегии для отображения в UI"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Описание стратегии"""
        pass

    # ═══════════════════════════════════════════════════════════════════════
    # ОБЩИЕ МЕТОДЫ (реализованы для всех стратегий)
    # ═══════════════════════════════════════════════════════════════════════

    def check_last_signal(self, candles: List[Candle]) -> Optional[Signal]:
        """
        Проверить, есть ли сигнал на ПОСЛЕДНЕЙ свече (для live-торговли).

        Дефолтная реализация: прогоняет detect_signals() по всей истории и берёт
        сигнал с индексом последней свечи. Стратегия не должна дублировать этот
        метод — иначе критерий входа в бэктесте и в live расходится.

        Args:
            candles: Список свечей

        Returns:
            Signal если сигнал на последней свече, иначе None

        Note:
            Фильтры входа, которые зависят от цены открытия СЛЕДУЮЩЕЙ свечи
            (например, отклонение от MA в execute_trades), на момент сигнала
            неизвестны — live может открыть сделку, которую бэктест отклонил бы.
        """
        if not candles:
            self.logger.warning("No candles for last signal check")
            return None

        signals = self.detect_signals(candles)
        last_index = len(candles) - 1

        for signal in reversed(signals):
            if signal.candle_index != last_index:
                continue
            # Тот же фильтр, что и в симуляции: при allow_short=False бэктест
            # не открывает SHORT, значит и live не должен.
            if signal.signal_type == "BEARISH" and not self.allow_short:
                self.logger.info("BEARISH signal ignored (allow_short=False)")
                return None
            return signal

        return None

    def execute_live_trades(
        self,
        orchestrator,
        ticker: str,
        figi: str
    ) -> List:
        """
        Выполнить реальную торговлю на основе сигналов из self.signals.

        Дефолтная реализация: передаёт сигналы в TradingOrchestrator с TP/SL
        стратегии. Стратегия не должна дублировать этот метод.

        Args:
            orchestrator: TradingOrchestrator для исполнения сделок
            ticker: Тикер инструмента (e.g., "SBER")
            figi: FIGI код инструмента

        Returns:
            List[LiveTrade]: Список созданных реальных сделок
        """
        if not self.signals:
            self.logger.warning("No signals to execute live.")
            return []

        signals = self.signals
        if not self.allow_short:
            # Тот же фильтр, что и в бэктесте: SHORT не открывается вовсе.
            signals = [s for s in signals if s.signal_type != "BEARISH"]
            if not signals:
                self.logger.info("No executable signals after allow_short filter")
                return []

        self.logger.info(
            f"🚀 Executing LIVE trades: {len(signals)} signals for {ticker}"
        )

        live_trades = orchestrator.process_signals(
            signals=signals,
            ticker=ticker,
            figi=figi,
            tp_percent=self.tp_percent,
            sl_percent=self.sl_percent
        )

        self.logger.info(
            f"✅ Live trading completed: {len(live_trades)} trades executed"
        )

        return live_trades

    def calculate_stats(self) -> StrategyStats:
        """
        Рассчитать статистику стратегии (универсальная реализация).
        
        Returns:
            StrategyStats: Статистика торговли
        """
        if not self.trades:
            self.logger.warning("No trades to calculate stats.")
            return StrategyStats()

        closed_trades = [t for t in self.trades if not t.is_open()]
        hold_days = [
            max(1, (t.exit_time - t.entry_time).days)   # min 1 день, как в _calculate_commissions
            for t in closed_trades
            if t.exit_time and t.entry_time
        ]
        avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0
        winning_trades = [t for t in closed_trades if t.pnl_percent and t.pnl_percent > 0]
        losing_trades = [t for t in closed_trades if t.pnl_percent and t.pnl_percent <= 0]

        total_pnl = sum(t.pnl_abs for t in closed_trades if t.pnl_abs)
        total_pnl_percent = (total_pnl / self.initial_balance) * 100

        win_rate = (
            (len(winning_trades) / len(closed_trades) * 100)
            if closed_trades
            else 0.0
        )

        avg_win = (
            sum(t.pnl_percent for t in winning_trades if t.pnl_percent)
            / len(winning_trades)
            if winning_trades
            else 0.0
        )

        avg_loss = (
            sum(t.pnl_percent for t in losing_trades if t.pnl_percent)
            / len(losing_trades)
            if losing_trades
            else 0.0
        )
        #  Avg Win/Avg Loss в рублях
        avg_win_abs = (
            sum(t.pnl_abs for t in winning_trades if t.pnl_abs)
            / len(winning_trades)
            if winning_trades
            else 0.0
        )

        avg_loss_abs = (
            sum(t.pnl_abs for t in losing_trades if t.pnl_abs)
            / len(losing_trades)
            if losing_trades
            else 0.0
        )

        final_balance = self.initial_balance + total_pnl

        # ═══════════════════════════════════════════════════════════════════════
        # 📊 РАСЧЁТ МЕТРИК ЦЕЛЕСООБРАЗНОСТИ СТРАТЕГИИ
        # ═══════════════════════════════════════════════════════════════════════
        
        # Expectancy = (Win_Rate × Avg_Win) - ((1 - Win_Rate) × |Avg_Loss|)
        # Win_Rate в долях (0.0 - 1.0), Avg_Win и Avg_Loss в процентах
        win_rate_decimal = win_rate / 100.0
        expectancy = (win_rate_decimal * avg_win) - ((1 - win_rate_decimal) * abs(avg_loss))
        
        # Profit Factor = Gross Profit / |Gross Loss|
        gross_profit = sum(t.pnl_net for t in winning_trades if t.pnl_net)
        gross_loss = abs(sum(t.pnl_net for t in losing_trades if t.pnl_net))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        # Payoff Ratio = Avg_Win / |Avg_Loss|
        payoff_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0.0
        
        # Max Drawdown (пиковое падение баланса)
        # Рассчитываем кумулятивный PnL по сделкам и находим максимальную просадку
        cumulative_pnl = 0.0
        peak_balance = self.initial_balance
        max_drawdown = 0.0
        
        # Сортируем сделки по времени входа для корректного расчёта просадки
        sorted_trades = sorted(closed_trades, key=lambda t: t.entry_time)
        
        for trade in sorted_trades:
            if trade.pnl_net:
                cumulative_pnl += trade.pnl_net
                current_balance = self.initial_balance + cumulative_pnl
                
                if current_balance > peak_balance:
                    peak_balance = current_balance
                
                drawdown = peak_balance - current_balance
                drawdown_percent = (drawdown / peak_balance) * 100 if peak_balance > 0 else 0.0
                
                if drawdown_percent > max_drawdown:
                    max_drawdown = drawdown_percent
        

        # Sharpe Ratio (annualized)
        # mean(pnl_percent) / stdev(pnl_percent) * sqrt(trades_in_year)
        returns = [t.pnl_percent for t in closed_trades if t.pnl_percent is not None]
        sharpe_ratio = None
        if len(returns) >= 2:
            mean_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            if std_ret > 0:
                # Реальный период в годах: от первой сделки до последней
                first_time = closed_trades[0].entry_time
                last_time = closed_trades[-1].exit_time or closed_trades[-1].entry_time
                years = (last_time - first_time).days / 365.0
                if years > 0:
                    trades_per_year = len(returns) / years
                    sharpe_ratio = (mean_ret / std_ret) * math.sqrt(trades_per_year)

        # Z-Score (серийность сделок / Wald-Wolfowitz runs test)
        # Бинарный список: 1 = win, 0 = loss
        binary = [1 if t.pnl_percent and t.pnl_percent > 0 else 0 for t in closed_trades]
        N = len(binary)
        W = sum(binary)
        L = N - W
        z_score = None
        if N >= 30 and W > 0 and L > 0:
            # Считаем число серий R
            R = 1
            for i in range(1, N):
                if binary[i] != binary[i - 1]:
                    R += 1
            X = 2 * W * L
            numerator = N * (R - 0.5) - X
            denominator = (X * (X - 1) / (N - 1)) ** 0.5
            if denominator > 0:
                z_score = numerator / denominator

        # Медианное движение цены после входа (25 свечей)
        long_up_moves = [t.post_entry_up_move for t in closed_trades
                         if t.is_long() and t.post_entry_up_move is not None]
        short_down_moves = [t.post_entry_down_move for t in closed_trades
                            if t.is_short() and t.post_entry_down_move is not None]
        
        median_long_up = statistics.median(long_up_moves) if long_up_moves else 0.0
        median_short_down = statistics.median(short_down_moves) if short_down_moves else 0.0

        stats = StrategyStats(
            total_trades=len(self.trades),
            closed_trades=len(closed_trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_win_abs=avg_win_abs,
            avg_loss_abs=avg_loss_abs,
            avg_hold_days=avg_hold_days,

            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            final_balance=final_balance,
            # ✅ Метрики целесообразности
            expectancy=expectancy,
            profit_factor=profit_factor,
            payoff_ratio=payoff_ratio,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            z_score=z_score,
            median_long_up_move=median_long_up,
            median_short_down_move=median_short_down,
        )

        self.stats = stats
        self.logger.info(f"\n{stats}")
        return stats

    def _calculate_commissions(self, trade: Trade) -> None:
        """
        Рассчитать все комиссии для сделки.

        Транзакционная комиссия: 0.05% за открытие + 0.05% за закрытие
        Маржинальная комиссия: по дням удержания (календарные)

        Режим применения регулируется через config/commissions.py → COMMISSION_MODE:
            "ALL"        — на все сделки
            "LONG_ONLY"  — только LONG
            "SHORT_ONLY" — только SHORT
            "NONE"       — отключены

        Args:
            trade: Торговая сделка
        """
        # Проверка режима комиссий
        if COMMISSION_MODE == "NONE":
            trade.commission_open = 0.0
            trade.commission_close = 0.0
            trade.commission_margin = 0.0
            trade.commission_total = 0.0
            return

        if COMMISSION_MODE == "LONG_ONLY" and trade.is_short():
            trade.commission_open = 0.0
            trade.commission_close = 0.0
            trade.commission_margin = 0.0
            trade.commission_total = 0.0
            return

        if COMMISSION_MODE == "SHORT_ONLY" and trade.is_long():
            trade.commission_open = 0.0
            trade.commission_close = 0.0
            trade.commission_margin = 0.0
            trade.commission_total = 0.0
            return

        # Транзакционная комиссия (открытие)
        trade.commission_open = trade.quantity * (TRANSACTION_COMMISSION_PERCENT / 100)
        
        # Транзакционная комиссия (закрытие)
        if trade.exit_price:
            exit_value = trade.quantity * (trade.exit_price / trade.entry_price)
            trade.commission_close = exit_value * (TRANSACTION_COMMISSION_PERCENT / 100)
        else:
            trade.commission_close = 0.0
        
        # Маржинальная комиссия (по дням)
        if trade.exit_time:
            days_held = (trade.exit_time - trade.entry_time).days
            # Минимум 1 день, если сделка закрыта
            days_held = max(1, days_held)
            daily_fee = calculate_daily_margin_fee(trade.quantity)
            trade.commission_margin = daily_fee * days_held
        else:
            trade.commission_margin = 0.0
        
        # Итого
        trade.commission_total = (
            trade.commission_open + 
            trade.commission_close + 
            trade.commission_margin
        )


    def _calculate_pnl(self, trade: Trade, current_balance: float) -> None:
        """
        Рассчитать PnL для закрытой позиции (универсальный метод).
        
        Args:
            trade: Торговая сделка
            current_balance: Баланс на момент входа (размер позиции)
        """
        if trade.exit_price is None:
            return
        
        self._calculate_commissions(trade)

        # PnL до комиссий (Gross)
        if trade.is_long():
            quantity = trade.quantity / trade.entry_price
            pnl_gross = quantity * (trade.exit_price - trade.entry_price)
        else:  # SHORT
            quantity = trade.quantity / trade.entry_price
            pnl_gross = quantity * (trade.entry_price - trade.exit_price)

        # PnL после комиссий (Net)
        pnl_net = pnl_gross - trade.commission_total

        # Проценты
        pnl_percent_gross = (pnl_gross / trade.quantity) * 100
        pnl_percent_net = (pnl_net / trade.quantity) * 100

        # Сохраняем
        trade.pnl_gross = pnl_gross
        trade.pnl_net = pnl_net
        trade.pnl_percent = pnl_percent_net  # ✅ Используем net для метрик
        trade.pnl_abs = pnl_net

    
    # ═══════════════════════════════════════════════════════════
    # MFE / MAE (потенциальный TP/SL за N свечей вперёд)
    # ═══════════════════════════════════════════════════════════

    def _apply_breakeven(self, trade: Trade, candle: Candle) -> None:
        """
        Перенести SL в цену входа (безубыток), если цена close прошла
        в сторону TP на trigger_percent.

        Args:
            trade: Открытая сделка
            candle: Текущая свеча
        """
        if not getattr(self, "breakeven_enabled", False):
            return

        if trade.breakeven_triggered:
            return

        trigger = getattr(self, "breakeven_trigger_percent", 1.0)
        lock_profit = getattr(self, "breakeven_lock_profit_percent", 0.2)
        entry_price = trade.entry_price

        if trade.is_long():
            move_percent = (candle.close - entry_price) / entry_price * 100.0
            if move_percent >= trigger:
                new_sl = entry_price * (1 + lock_profit / 100.0)
                trade.sl_level = new_sl
                trade.breakeven_triggered = True
                if self.debug:
                    self.logger.debug(
                        f"BREAKEVEN LONG #{trade.trade_id}: SL moved to {new_sl:.2f} "
                        f"(+{lock_profit}%) at close={candle.close:.2f} (+{move_percent:.2f}%)"
                    )
        elif trade.is_short():
            move_percent = (entry_price - candle.close) / entry_price * 100.0
            if move_percent >= trigger:
                new_sl = entry_price * (1 - lock_profit / 100.0)
                trade.sl_level = new_sl
                trade.breakeven_triggered = True
                if self.debug:
                    self.logger.debug(
                        f"BREAKEVEN SHORT #{trade.trade_id}: SL moved to {new_sl:.2f} "
                        f"(-{lock_profit}%) at close={candle.close:.2f} (-{move_percent:.2f}%)"
                    )

    def _calculate_mfe_mae(self, trade: Trade, candles: List[Candle], lookforward: int = 5) -> None:
        """
        Рассчитать MFE (макс. потенциальная прибыль) и MAE (макс. потенциальный убыток)
        за N свечей вперёд от входа.

        Args:
            trade: Сделка
            candles: Все свечи
            lookforward: Сколько свечей вперёд смотреть (по умолчанию 5)
        """
        entry_idx = trade.entry_index
        entry_price = trade.entry_price
        end_idx = min(entry_idx + lookforward + 1, len(candles))
        window = candles[entry_idx + 1:end_idx]

        if not window:
            trade.mfe_percent = 0.0
            trade.mae_percent = 0.0
            return

        if trade.is_long():
            max_high = max(c.high for c in window)
            min_low = min(c.low for c in window)
            trade.mfe_percent = ((max_high - entry_price) / entry_price) * 100.0
            trade.mae_percent = ((entry_price - min_low) / entry_price) * 100.0
        else:  # SHORT
            min_low = min(c.low for c in window)
            max_high = max(c.high for c in window)
            trade.mfe_percent = ((entry_price - min_low) / entry_price) * 100.0
            trade.mae_percent = ((max_high - entry_price) / entry_price) * 100.0

    def _calculate_post_entry_moves(self, trade: Trade, candles: List[Candle], lookforward: int = 25) -> None:
        """
        Рассчитать движение цены после входа за N свечей.

        Для LONG: максимальное движение вверх по high.
        Для SHORT: максимальное движение вниз по low.

        Результат сохраняется в атрибуты trade:
            - post_entry_up_move (только для long)
            - post_entry_down_move (только для short)
        """
        entry_idx = trade.entry_index
        entry_price = trade.entry_price
        end_idx = min(entry_idx + lookforward + 1, len(candles))
        window = candles[entry_idx + 1:end_idx]

        if not window or entry_price == 0:
            trade.post_entry_up_move = 0.0
            trade.post_entry_down_move = 0.0
            return

        if trade.is_long():
            max_high = max(c.high for c in window)
            trade.post_entry_up_move = ((max_high - entry_price) / entry_price) * 100.0
            trade.post_entry_down_move = None
        else:  # SHORT
            min_low = min(c.low for c in window)
            trade.post_entry_down_move = ((entry_price - min_low) / entry_price) * 100.0
            trade.post_entry_up_move = None

    
    def run(self, candles: List[Candle]) -> Dict:
        """
        Запустить полный цикл стратегии.
        
        Args:
            candles: Список свечей для анализа
        
        Returns:
            Dict: {
                "signals": List[Signal],
                "trades": List[Trade],
                "stats": StrategyStats,
            }
        """
        self.logger.info(f"Running {self.name} on {len(candles)} candles...")

        signals = self.detect_signals(candles)
        trades = self.execute_trades(candles)

        # SHORT уже отфильтрованы движком (allow_short); здесь убираем их из
        # self.signals, чтобы дашборд не рисовал неисполненные сигналы.
        if not self.allow_short:
            self.signals = [s for s in self.signals if s.signal_type != "BEARISH"]
            signals = self.signals

        # ✅ MFE/MAE и движение после входа для всех стратегий
        for t in trades:
            self._calculate_mfe_mae(t, candles)
            self._calculate_post_entry_moves(t, candles)
            
        stats = self.calculate_stats()

        result = {
            "signals": signals,
            "trades": trades,
            "stats": stats,
        }

        return result
