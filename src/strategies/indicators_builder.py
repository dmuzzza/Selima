"""
Indicators Builder V2 - WITH MA ADAPTIVE

Поддерживаемые индикаторы:
- SMA (Simple Moving Average)
- EMA (Exponential Moving Average)  
- MA Adaptive (Adaptive Moving Average)
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- ATR (Average True Range)
- Stochastic

═══════════════════════════════════════════════════════════════════════════
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Union
from abc import ABC, abstractmethod

from src.market_data.models import Candle
from src.strategies.indicators_config import get_indicator_config, get_all_indicators, INDICATORS_CONFIG

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 📊 DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class IndicatorResult:
    """Результат расчёта индикатора"""
    indicator_name: str  # e.g., "SMA", "RSI", "MACD"
    timestamp: datetime
    value: Union[float, List[float], Dict]  # Может быть число, список, или словарь

    def __repr__(self):
        if isinstance(self.value, dict):
            value_str = ", ".join([f"{k}={v:.2f}" for k, v in self.value.items()])
        elif isinstance(self.value, list):
            value_str = f"[{len(self.value)} values]"
        else:
            value_str = f"{self.value:.2f}"
        
        return f"{self.indicator_name}({value_str})"


# ═══════════════════════════════════════════════════════════════════════════
# 🎯 BASE INDICATOR CLASS
# ═══════════════════════════════════════════════════════════════════════════

class BaseIndicator(ABC):
    """Базовый класс для всех индикаторов"""

    def __init__(self, name: str, debug: bool = False):
        self.name = name
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self.config = self._handle_indicator_config()

    def _handle_indicator_config(self) -> Dict:
        """Загрузить конфигурацию из indicators_config.py"""
        config = get_indicator_config(self.name)
        if self.debug:
            self.logger.debug(f"Loaded config for {self.name}: {config}")
        return config

    def validate_candles(self, candles: List[Candle], min_required: int) -> bool:
        """Проверка достаточности свечей"""
        if len(candles) < min_required:
            if self.debug:
                self.logger.debug(
                    f"{self.name}: Not enough candles ({len(candles)} < {min_required})"
                )
            return False
        return True

    @abstractmethod
    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать индикатор для последней свечи"""
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 📈 INDICATOR IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════

class SMAIndicator(BaseIndicator):
    """Simple Moving Average"""

    def __init__(self, debug: bool = False):
        super().__init__("SMA", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать SMA для последней свечи"""
        period = self.config.get("period", 20)

        if not self.validate_candles(candles, period):
            return None

        closes = [c.close for c in candles[-period:]]
        sma_value = sum(closes) / len(closes)

        if self.debug:
            self.logger.debug(f"SMA({period}) = {sma_value:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value=sma_value
        )


class EMAIndicator(BaseIndicator):
    """Exponential Moving Average"""

    def __init__(self, debug: bool = False):
        super().__init__("EMA", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать EMA для последней свечи"""
        period = self.config.get("period", 12)

        if not self.validate_candles(candles, period):
            return None

        closes = [c.close for c in candles[-period:]]

        # EMA formula: EMA = Price * multiplier + EMA(prev) * (1 - multiplier)
        # multiplier = 2 / (N + 1)
        multiplier = 2 / (period + 1)

        # Первое значение - SMA
        ema = sum(closes) / len(closes)

        # Дальше EMA
        for close in closes[len(closes)//2:]:
            ema = (close - ema) * multiplier + ema

        if self.debug:
            self.logger.debug(f"EMA({period}) = {ema:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value=ema
        )


class MAAdaptiveIndicator(BaseIndicator):
    """
    Adaptive Moving Average (MA Adaptive)
    
    Адаптивная скользящая средняя на основе EMA.
    Используется период из конфигурации для динамической адаптации к рынку.
    """

    def __init__(self, debug: bool = False):
        super().__init__("MA_ADAPTIVE", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать MA Adaptive для последней свечи"""
        period = self.config.get("period", 14)

        if not self.validate_candles(candles, period):
            return None

        closes = [c.close for c in candles[-period:]]

        # MA Adaptive = EMA с адаптивным периодом
        # Для упрощения используем стандартный EMA
        multiplier = 2.0 / (period + 1)

        # Первое значение - SMA
        ema = sum(closes) / len(closes)

        # Остальные значения - EMA
        for close in closes[len(closes)//2:]:
            ema = (close - ema) * multiplier + ema

        if self.debug:
            self.logger.debug(f"MA_ADAPTIVE({period}) = {ema:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value=ema
        )


class RSIIndicator(BaseIndicator):
    """Relative Strength Index"""

    def __init__(self, debug: bool = False):
        super().__init__("RSI", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать RSI для последней свечи"""
        period = self.config.get("period", 14)

        if not self.validate_candles(candles, period + 1):
            return None

        closes = [c.close for c in candles[-(period + 1):]]

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)

        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        if self.debug:
            self.logger.debug(f"RSI({period}) = {rsi:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value=rsi
        )


class MACDIndicator(BaseIndicator):
    """MACD - Moving Average Convergence Divergence"""

    def __init__(self, debug: bool = False):
        super().__init__("MACD", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать MACD для последней свечи"""
        fast_period = self.config.get("fast_period", 12)
        slow_period = self.config.get("slow_period", 26)
        signal_period = self.config.get("signal_period", 9)

        required = max(slow_period, signal_period)

        if not self.validate_candles(candles, required + 1):
            return None

        closes = [c.close for c in candles[-(required + 1):]]

        def calculate_ema(values, period):
            multiplier = 2 / (period + 1)
            ema = sum(values[:period]) / period
            for val in values[period:]:
                ema = (val - ema) * multiplier + ema
            return ema

        ema_fast = calculate_ema(closes, fast_period)
        ema_slow = calculate_ema(closes, slow_period)
        macd_line = ema_fast - ema_slow

        if self.debug:
            self.logger.debug(f"MACD line={macd_line:.4f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value={"macd": macd_line}
        )


class BollingerBandsIndicator(BaseIndicator):
    """Bollinger Bands"""

    def __init__(self, debug: bool = False):
        super().__init__("BOLLINGER_BANDS", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать Bollinger Bands для последней свечи"""
        period = self.config.get("period", 20)
        std_dev_multiplier = self.config.get("std_dev_multiplier", 2.0)

        if not self.validate_candles(candles, period):
            return None

        closes = [c.close for c in candles[-period:]]

        middle_band = sum(closes) / len(closes)

        variance = sum((x - middle_band) ** 2 for x in closes) / len(closes)
        std_dev = variance ** 0.5

        upper_band = middle_band + (std_dev * std_dev_multiplier)
        lower_band = middle_band - (std_dev * std_dev_multiplier)

        if self.debug:
            self.logger.debug(
                f"BB({period}): upper={upper_band:.2f}, mid={middle_band:.2f}, low={lower_band:.2f}"
            )

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value={
                "upper": upper_band,
                "middle": middle_band,
                "lower": lower_band
            }
        )


class ATRIndicator(BaseIndicator):
    """ATR - Average True Range"""

    def __init__(self, debug: bool = False):
        super().__init__("ATR", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать ATR для последней свечи"""
        period = self.config.get("period", 14)

        if not self.validate_candles(candles, period + 1):
            return None

        tr_values = []

        for i in range(1, len(candles)):
            curr = candles[i]
            prev = candles[i - 1]

            tr = max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close)
            )
            tr_values.append(tr)

        atr_value = sum(tr_values[-period:]) / period

        if self.debug:
            self.logger.debug(f"ATR({period}) = {atr_value:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value=atr_value
        )


class StochasticIndicator(BaseIndicator):
    """Stochastic Oscillator"""

    def __init__(self, debug: bool = False):
        super().__init__("STOCHASTIC", debug)

    def calculate(self, candles: List[Candle]) -> Optional[IndicatorResult]:
        """Рассчитать Stochastic для последней свечи"""
        period = self.config.get("period", 14)
        smooth_k = self.config.get("smooth_k", 3)

        if not self.validate_candles(candles, period):
            return None

        window = candles[-period:]

        highs = [c.high for c in window]
        lows = [c.low for c in window]
        close = candles[-1].close

        highest = max(highs)
        lowest = min(lows)

        range_val = highest - lowest

        if range_val == 0:
            k_percent = 50.0
        else:
            k_percent = 100 * ((close - lowest) / range_val)

        if self.debug:
            self.logger.debug(f"Stochastic({period}): %K={k_percent:.2f}")

        return IndicatorResult(
            indicator_name=self.name,
            timestamp=candles[-1].open_time,
            value={"k_percent": k_percent}
        )


# ═══════════════════════════════════════════════════════════════════════════
# 🏗️ INDICATORS BUILDER
# ═══════════════════════════════════════════════════════════════════════════

class IndicatorsBuilder:
    """
    Строитель индикаторов для свечей.
    
    Поддерживает:
    - Расчёт одного индикатора (get_indicator_value)
    - Расчёт всех индикаторов (build_indicators)
    - Расчёт индикатора для всех свечей (build_sma, build_ma_adaptive, ...)
    """

    def __init__(self, candles: List[Candle] = None, debug: bool = False):
        self.logger = logging.getLogger(__name__)
        self.debug = debug
        self.candles = candles  # 🔑 Сохраняем свечи для методов build_*

        # Реестр индикаторов
        self.indicators_registry = {
            "SMA": SMAIndicator(debug),
            "EMA": EMAIndicator(debug),
            "MA_ADAPTIVE": MAAdaptiveIndicator(debug),  # 🔑 НОВОЕ
            "RSI": RSIIndicator(debug),
            "MACD": MACDIndicator(debug),
            "BOLLINGER_BANDS": BollingerBandsIndicator(debug),
            "ATR": ATRIndicator(debug),
            "STOCHASTIC": StochasticIndicator(debug),
        }

        self.logger.info(f"IndicatorsBuilder initialized with {len(self.indicators_registry)} indicators")

    def build_indicators(self, candles: List[Candle]) -> Dict[str, Optional[IndicatorResult]]:
        """
        Рассчитать все включённые индикаторы для последней свечи.

        Args:
            candles: Список свечей

        Returns:
            Dict: {indicator_name: IndicatorResult | None}
        """
        if not candles:
            self.logger.warning("No candles provided")
            return {}

        self.logger.debug(f"Building indicators from {len(candles)} candles")

        results = {}

        for indicator_name, indicator in self.indicators_registry.items():
            config = get_indicator_config(indicator_name)

            if not config.get("enabled", True):
                if self.debug:
                    self.logger.debug(f"Indicator {indicator_name} is disabled")
                results[indicator_name] = None
                continue

            try:
                result = indicator.calculate(candles)
                if result:
                    self.logger.debug(f"✓ {result}")
                    results[indicator_name] = result
                else:
                    results[indicator_name] = None
            except Exception as e:
                self.logger.error(f"❌ Error calculating {indicator_name}: {e}")
                results[indicator_name] = None

        return results

    def get_indicator_value(
        self, candles: List[Candle], indicator_name: str
    ) -> Optional[Union[float, Dict]]:
        """
        Получить значение индикатора для последней свечи.

        Args:
            candles: Список свечей
            indicator_name: Название индикатора (e.g., "RSI", "MACD")

        Returns:
            float | Dict | None
        """
        if indicator_name not in self.indicators_registry:
            self.logger.warning(f"Indicator {indicator_name} not found in registry")
            return None

        indicator = self.indicators_registry[indicator_name]
        result = indicator.calculate(candles)

        return result.value if result else None

    # ═══════════════════════════════════════════════════════════════════════
    # 🔧 МЕТОДЫ ДЛЯ РАСЧЁТА ИНДИКАТОРОВ ДЛЯ ВСЕХ СВЕЧЕЙ
    # ═══════════════════════════════════════════════════════════════════════

    def build_sma(self, period: int = 50) -> List[Optional[float]]:
        """
        Рассчитать SMA для всех свечей.
        
        Args:
            period: Период SMA
        
        Returns:
            List[float | None]: Массив SMA
        """
        if not self.candles or len(self.candles) < period:
            return [None] * (len(self.candles) if self.candles else 0)

        sma_series = [None] * len(self.candles)

        for i in range(period - 1, len(self.candles)):
            window = self.candles[i - period + 1 : i + 1]
            closes = [c.close for c in window]
            sma_series[i] = sum(closes) / len(closes)

        return sma_series

    def build_ema(self, period: int = 200) -> List[Optional[float]]:
        """
        Рассчитать EMA для всех свечей.

        Args:
            period: Период EMA

        Returns:
            List[float | None]: Массив EMA
        """
        if not self.candles or len(self.candles) < period:
            return [None] * (len(self.candles) if self.candles else 0)

        ema_series = [None] * len(self.candles)
        multiplier = 2.0 / (period + 1)

        # Первое значение — SMA
        closes_init = [c.close for c in self.candles[:period]]
        ema = sum(closes_init) / len(closes_init)
        ema_series[period - 1] = ema

        # Остальные значения — EMA
        for i in range(period, len(self.candles)):
            close = self.candles[i].close
            ema = close * multiplier + ema * (1 - multiplier)
            ema_series[i] = ema

        return ema_series

    def build_ma_adaptive(self, period: int = 14) -> List[Optional[float]]:
        """
        Рассчитать MA Adaptive (EMA) для всех свечей.
        
        Args:
            period: Период MA Adaptive
        
        Returns:
            List[float | None]: Массив MA Adaptive
        """
        if not self.candles or len(self.candles) < period:
            return [None] * (len(self.candles) if self.candles else 0)

        ma_adaptive_series = [None] * len(self.candles)
        multiplier = 2.0 / (period + 1)

        # Первое значение - SMA
        closes_init = [c.close for c in self.candles[:period]]
        ema = sum(closes_init) / len(closes_init)
        ma_adaptive_series[period - 1] = ema

        # Остальные значения - EMA
        for i in range(period, len(self.candles)):
            close = self.candles[i].close
            ema = close * multiplier + ema * (1 - multiplier)
            ma_adaptive_series[i] = ema

        return ma_adaptive_series

    def build_adx(self, period: int = 14) -> List[Optional[float]]:
        """
        Рассчитать ADX для всех свечей.

        Args:
            period: Период ADX

        Returns:
            List[float | None]: Массив ADX значений
        """
        if not self.candles or len(self.candles) < period * 2 + 1:
            return [None] * (len(self.candles) if self.candles else 0)

        candles = self.candles

        # True Range
        tr_list = [0.0]
        for i in range(1, len(candles)):
            curr = candles[i]
            prev = candles[i - 1]
            tr = max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close)
            )
            tr_list.append(tr)

        # +DM и -DM
        plus_dm = [0.0]
        minus_dm = [0.0]
        for i in range(1, len(candles)):
            curr = candles[i]
            prev = candles[i - 1]
            up_move = curr.high - prev.high
            down_move = prev.low - curr.low

            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0.0)

            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0.0)

        # Сглаженные TR, +DM, -DM
        atr = sum(tr_list[1:period + 1])
        plus_di_sum = sum(plus_dm[1:period + 1])
        minus_di_sum = sum(minus_dm[1:period + 1])

        adx_series = [None] * len(candles)

        for i in range(period, len(candles)):
            if i == period:
                atr = sum(tr_list[i - period + 1:i + 1])
                plus_di_sum = sum(plus_dm[i - period + 1:i + 1])
                minus_di_sum = sum(minus_dm[i - period + 1:i + 1])
            else:
                atr = (atr * (period - 1) + tr_list[i]) / period
                plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
                minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period

            plus_di = (plus_di_sum / atr) * 100.0 if atr > 0 else 0.0
            minus_di = (minus_di_sum / atr) * 100.0 if atr > 0 else 0.0

            dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100.0 if (plus_di + minus_di) > 0 else 0.0

            if i == period:
                adx_series[i] = dx
            else:
                prev_adx = adx_series[i - 1] if adx_series[i - 1] is not None else dx
                adx_series[i] = (prev_adx * (period - 1) + dx) / period

        return adx_series

    def build_rsi(self, period: int = 14) -> List[Optional[float]]:
        """
        Рассчитать RSI для всех свечей.

        Args:
            period: Период RSI

        Returns:
            List[float | None]: Массив RSI значений
        """
        if not self.candles or len(self.candles) < period + 1:
            return [None] * (len(self.candles) if self.candles else 0)

        closes = [c.close for c in self.candles]
        rsi_series = [None] * len(self.candles)

        gains = []
        losses = []

        # Первый проход: считаем gains/losses для всех свечей начиная с 1
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))

        # Первое среднее (SMA от первых period значений)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Первое значение RSI
        if avg_loss == 0:
            rsi_series[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_series[period] = 100.0 - (100.0 / (1.0 + rs))

        # Wilder's smoothing для остальных
        for i in range(period + 1, len(self.candles)):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period

            if avg_loss == 0:
                rsi_series[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_series[i] = 100.0 - (100.0 / (1.0 + rs))

        return rsi_series

    def register_indicator(self, indicator_name: str, indicator_class: type):
        """
        Зарегистрировать кастомный индикатор.

        Args:
            indicator_name: Название индикатора
            indicator_class: Класс индикатора (наследник BaseIndicator)
        """
        try:
            instance = indicator_class(debug=self.debug)
            self.indicators_registry[indicator_name] = instance
            self.logger.info(f"✓ Registered custom indicator: {indicator_name}")
        except Exception as e:
            self.logger.error(f"Failed to register indicator {indicator_name}: {e}")
