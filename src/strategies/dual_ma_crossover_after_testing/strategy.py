"""
Dual MA Crossover Strategy V1

Стратегия пересечения двух скользящих средних:
- SMA50 (быстрая линия)
- MA Adaptive (медленная линия)

ЛОГИКА:
- BULLISH: SMA50 пересекает MA Adaptive снизу вверх
- BEARISH: SMA50 пересекает MA Adaptive сверху вниз

ФИЛЬТР EMA (опционально):
- LONG сигнал только если цена > EMA(N)
- SHORT сигнал только если цена < EMA(N)

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import List, Optional

from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal, Trade
from src.strategies.indicators_builder import IndicatorsBuilder

from .config import get_config

logger = logging.getLogger(__name__)


class DualMACrossoverStrategy(BaseStrategy):
    """
    Стратегия пересечения SMA50 × MA Adaptive.
    
    ЛОГИКА:
    - Сигнал BULLISH: SMA50 пересекает MA Adaptive снизу вверх
    - Сигнал BEARISH: SMA50 пересекает MA Adaptive сверху вниз
    - Вход: По цене Open следующей свечи
    - Выход: По TP или SL
    """

    def __init__(self, timeframe: str, config: dict = None, debug: bool = False):
        """
        Инициализация Dual MA Crossover стратегии.
        
        Args:
            timeframe: Таймфрейм (e.g., "1h", "4h", "1d")
            config: Кастомная конфигурация (опционально).
                    Можно передать ticker для подбора индивидуальных TP/SL.
            debug: Режим отладки
        """
        # Извлекаем ticker из config (если есть)
        ticker = (config or {}).get("ticker", None)

        # Загружаем конфиг для таймфрейма + ticker
        strategy_config = get_config(timeframe, config, ticker=ticker)
        
        # Инициализируем базовый класс
        super().__init__(timeframe, strategy_config, debug)
        
        # MA параметры
        self.sma_period = self.config.get("sma_period", 50)
        self.adaptive_period = self.config.get("adaptive_period", 14)
        
        # TP/SL параметры
        self.tp_percent = self.config.get("tp_percent", 1.5)
        self.sl_percent = self.config.get("sl_percent", 1.0)

        # EMA фильтр
        self.use_ema_filter = self.config.get("use_ema_filter", False)
        self.ema_period = self.config.get("ema_period", 200)

        # Фильтр отклонения от MA Adaptive
        self.max_adaptive_deviation = self.config.get("max_adaptive_deviation_percent", 1.2)

        if self.debug:
            self.logger.debug(
                f"Dual MA Crossover initialized: SMA={self.sma_period}, "
                f"Adaptive={self.adaptive_period}, TP={self.tp_percent}%, SL={self.sl_percent}%"
            )
            if self.use_ema_filter:
                self.logger.debug(f"EMA filter ENABLED: EMA({self.ema_period})")

    # ═══════════════════════════════════════════════════════════════════════
    # СВОЙСТВА СТРАТЕГИИ
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def name(self) -> str:
        """Имя стратегии для UI"""
        return "Dual MA Crossover"

    @property
    def description(self) -> str:
        """Описание стратегии"""
        return f"SMA{self.sma_period} × MA Adaptive({self.adaptive_period}) crossover strategy"

    # ═══════════════════════════════════════════════════════════════════════
    # РАСЧЁТ ИНДИКАТОРОВ
    # ═══════════════════════════════════════════════════════════════════════

    def calculate_sma_series(self, candles: List[Candle]) -> List[Optional[float]]:
        """
        Рассчитать SMA50 для всех свечей.
        
        Args:
            candles: Список свечей
        
        Returns:
            List[float | None]: Массив SMA
        """
        if len(candles) < self.sma_period:
            return [None] * len(candles)

        sma_series = [None] * len(candles)

        for i in range(self.sma_period - 1, len(candles)):
            window = candles[i - self.sma_period + 1 : i + 1]
            closes = [c.close for c in window]
            sma_series[i] = sum(closes) / len(closes)

        return sma_series

    def calculate_ma_adaptive_series(self, candles: List[Candle]) -> List[Optional[float]]:
        """
        Рассчитать MA Adaptive используя IndicatorsBuilder.
        
        Args:
            candles: Список свечей
        
        Returns:
            List[float | None]: Массив MA Adaptive
        """
        try:
            from src.strategies.indicators_builder import IndicatorsBuilder
            builder = IndicatorsBuilder(candles)
            ma_adaptive = builder.build_ma_adaptive(period=self.adaptive_period)
            return ma_adaptive
        except Exception as e:
            self.logger.error(f"Failed to calculate MA Adaptive: {e}")
            return [None] * len(candles)

    def calculate_ema_series(self, candles: List[Candle]) -> List[Optional[float]]:
        """
        Рассчитать EMA для всех свечей.

        Args:
            candles: Список свечей

        Returns:
            List[float | None]: Массив EMA
        """
        try:
            from src.strategies.indicators_builder import IndicatorsBuilder
            builder = IndicatorsBuilder(candles)
            ema = builder.build_ema(period=self.ema_period)
            return ema
        except Exception as e:
            self.logger.error(f"Failed to calculate EMA({self.ema_period}): {e}")
            return [None] * len(candles)


    # ═══════════════════════════════════════════════════════════════════════
    # ДЕТЕКТИРОВАНИЕ СИГНАЛОВ
    # ═══════════════════════════════════════════════════════════════════════

    def detect_signals(self, candles: List[Candle]) -> List[Signal]:
        """
        Детектировать пересечения SMA50 и MA Adaptive.
        
        BULLISH: SMA50[i-1] <= MA_Adaptive[i-1] AND SMA50[i] > MA_Adaptive[i]
        BEARISH: SMA50[i-1] >= MA_Adaptive[i-1] AND SMA50[i] < MA_Adaptive[i]
        
        EMA-фильтр (опционально):
            - LONG только если close > EMA(N)
            - SHORT только если close < EMA(N)
        
        Args:
            candles: Список свечей
        
        Returns:
            List[Signal]: Найденные сигналы
        """
        min_candles = max(self.sma_period, self.adaptive_period) + 1

        if self.use_ema_filter:
            min_candles = max(min_candles, self.ema_period + 1)
        
        if len(candles) < min_candles:
            self.logger.warning(
                f"Not enough candles: {len(candles)} < {min_candles}"
            )
            return []

        sma_series = self.calculate_sma_series(candles)
        ma_adaptive_series = self.calculate_ma_adaptive_series(candles)
        
        # EMA фильтр
        ema_series = None
        if self.use_ema_filter:
            ema_series = self.calculate_ema_series(candles)

        # Сохраняем для использования в execute_trades (фильтры на входе)
        self._sma_series = sma_series
        self._ma_adaptive_series = ma_adaptive_series
        self._ema_series = ema_series
        
        signals = []

        # Начинаем с индекса, где все индикаторы готовы
        start_idx = max(self.sma_period, self.adaptive_period)
        if self.use_ema_filter:
            start_idx = max(start_idx, self.ema_period)

        for i in range(start_idx, len(candles)):
            curr_candle = candles[i]

            curr_sma = sma_series[i]
            prev_sma = sma_series[i - 1]
            curr_adaptive = ma_adaptive_series[i]
            prev_adaptive = ma_adaptive_series[i - 1]

            # Проверяем на None
            if any(val is None for val in [curr_sma, prev_sma, curr_adaptive, prev_adaptive]):
                continue

            # EMA фильтр
            ema_val = None
            if self.use_ema_filter:
                ema_val = ema_series[i]
                if ema_val is None:
                    continue

            # BULLISH: MA Adaptive пересекает SMA50 снизу вверх
            if prev_adaptive <= prev_sma and curr_adaptive > curr_sma:
                # EMA фильтр: LONG только если close > EMA
                if self.use_ema_filter and curr_candle.close <= ema_val:
                    if self.debug:
                        self.logger.debug(
                            f"✗ BULLISH rejected by EMA filter at idx={i}: "
                            f"close={curr_candle.close:.2f} <= EMA({self.ema_period})={ema_val:.2f}"
                        )
                    continue

                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BULLISH",
                    price=curr_candle.close,
                    metadata={
                        "sma50": curr_sma,
                        "ma_adaptive": curr_adaptive,
                        **({"ema_val": ema_val} if ema_val is not None else {}),
                    }
                )
                signals.append(signal)

                if self.debug:
                    self.logger.debug(
                        f"✓ BULLISH signal at idx={i}: "
                        f"MA_Adaptive={curr_adaptive:.2f} crosses SMA50={curr_sma:.2f} from below"
                    )

            # BEARISH: MA Adaptive пересекает SMA50 сверху вниз
            elif prev_adaptive >= prev_sma and curr_adaptive < curr_sma:
                # EMA фильтр: SHORT только если close < EMA
                if self.use_ema_filter and curr_candle.close >= ema_val:
                    if self.debug:
                        self.logger.debug(
                            f"✗ BEARISH rejected by EMA filter at idx={i}: "
                            f"close={curr_candle.close:.2f} >= EMA({self.ema_period})={ema_val:.2f}"
                        )
                    continue

                signal = Signal(
                    timestamp=curr_candle.open_time,
                    candle_index=i,
                    signal_type="BEARISH",
                    price=curr_candle.close,
                    metadata={
                        "sma50": curr_sma,
                        "ma_adaptive": curr_adaptive,
                        **({"ema_val": ema_val} if ema_val is not None else {}),
                    }
                )
                signals.append(signal)

                if self.debug:
                    self.logger.debug(
                        f"✓ BEARISH signal at idx={i}: "
                        f"MA_Adaptive={curr_adaptive:.2f} crosses SMA50={curr_sma:.2f} from above"
                    )

        self.signals = signals
        self.logger.info(f"Detected {len(signals)} signals")
        return signals

    # ═══════════════════════════════════════════════════════════════════════
    # СИМУЛЯЦИЯ ТОРГОВЛИ
    # ═══════════════════════════════════════════════════════════════════════

    def execute_trades(self, candles: List[Candle]) -> List[Trade]:
        """
        Симулировать торговлю на основе сигналов.
        
        Args:
            candles: Список свечей
        
        Returns:
            List[Trade]: Список сделок
        """
        if not self.signals:
            self.logger.warning("No signals to execute.")
            return []

        trades = []
        trade_id = 1
        current_trade: Optional[Trade] = None
        current_balance = self.initial_balance

        for candle_idx, candle in enumerate(candles):
            # ───────────────────────────────────────────────────────────────
            # ПРОВЕРКА TP/SL ДЛЯ ОТКРЫТОЙ ПОЗИЦИИ
            # ───────────────────────────────────────────────────────────────

            if current_trade is not None:
                # LONG: проверяем TP на High, SL на Low
                if current_trade.is_long():
                    if candle.high >= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                    if candle.low <= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                # SHORT: проверяем TP на Low, SL на High
                elif current_trade.is_short():
                    if candle.low <= current_trade.tp_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.tp_level
                        current_trade.exit_type = "TP"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

                    if candle.high >= current_trade.sl_level:
                        current_trade.exit_time = candle.open_time
                        current_trade.exit_index = candle_idx
                        current_trade.exit_price = current_trade.sl_level
                        current_trade.exit_type = "SL"
                        self._calculate_pnl(current_trade, current_balance)
                        current_balance += current_trade.pnl_abs
                        trades.append(current_trade)

                        if self.debug:
                            self.logger.debug(
                                f"→ {current_trade} | Balance: {current_balance:.2f}"
                            )

                        current_trade = None
                        continue

            # ───────────────────────────────────────────────────────────────
            # ПРОВЕРКА НОВЫХ СИГНАЛОВ
            # ───────────────────────────────────────────────────────────────

            if current_trade is None:
                for signal in self.signals:
                    # Вход на следующей свече после сигнала
                    if signal.candle_index == candle_idx - 1:
                        entry_price = candle.open
                        position_size = current_balance

                        # ───────────────────────────────────────────────────
                        # ФИЛЬТР 1: EMA200 — трендовый фильтр на входе
                        # ───────────────────────────────────────────────────
                        if self.use_ema_filter and self._ema_series is not None:
                            ema_val = self._ema_series[candle_idx]
                            if ema_val is not None:
                                if signal.signal_type == "BULLISH" and entry_price < ema_val:
                                    if self.debug:
                                        self.logger.debug(
                                            f"✗ ENTRY REJECTED (EMA200 filter): "
                                            f"LONG entry_price={entry_price:.2f} < EMA200={ema_val:.2f}"
                                        )
                                    continue
                                if signal.signal_type == "BEARISH" and entry_price > ema_val:
                                    if self.debug:
                                        self.logger.debug(
                                            f"✗ ENTRY REJECTED (EMA200 filter): "
                                            f"SHORT entry_price={entry_price:.2f} > EMA200={ema_val:.2f}"
                                        )
                                    continue

                        # ───────────────────────────────────────────────────
                        # ФИЛЬТР 2: Отклонение от MA Adaptive ≤ 1.2%
                        # ───────────────────────────────────────────────────
                        if self._ma_adaptive_series is not None:
                            ma_adaptive_val = self._ma_adaptive_series[candle_idx]
                            if ma_adaptive_val is not None:
                                deviation_pct = abs(entry_price - ma_adaptive_val) / ma_adaptive_val * 100.0
                                if deviation_pct > self.max_adaptive_deviation:
                                    if self.debug:
                                        self.logger.debug(
                                            f"✗ ENTRY REJECTED (adaptive deviation): "
                                            f"|{entry_price:.2f} - MA_Adaptive={ma_adaptive_val:.2f}| "
                                            f"/ {ma_adaptive_val:.2f} = {deviation_pct:.2f}% > {self.max_adaptive_deviation}%"
                                        )
                                    continue

                        if signal.signal_type == "BULLISH":
                            tp_level = entry_price * (1 + self.tp_percent / 100.0)
                            sl_level = entry_price * (1 - self.sl_percent / 100.0)
                        else:
                            tp_level = entry_price * (1 - self.tp_percent / 100.0)
                            sl_level = entry_price * (1 + self.sl_percent / 100.0)

                        current_trade = Trade(
                            trade_id=trade_id,
                            entry_time=candle.open_time,
                            entry_index=candle_idx,
                            entry_price=entry_price,
                            signal_type=signal.signal_type,
                            tp_level=tp_level,
                            sl_level=sl_level,
                            quantity=position_size,
                        )

                        trade_id += 1

                        if self.debug:
                            self.logger.debug(
                                f"ENTRY #{current_trade.trade_id}: {current_trade.signal_type} "
                                f"@ {entry_price:.2f} (SIZE: {position_size:.0f}₽)"
                            )
                        break

        # Добавляем открытую позицию
        if current_trade is not None:
            trades.append(current_trade)

        self.trades = trades
        self.logger.info(
            f"Executed {len(trades)} trades "
            f"({len([t for t in trades if not t.is_open()])} closed)"
        )
        return trades
