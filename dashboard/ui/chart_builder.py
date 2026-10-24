"""
Построение plotly Figure для графика свечей.

Вынесено из монолитного callback update_chart (~500 строк).
Каждая функция отвечает за один слой графика.
"""

import logging
from typing import List, Dict, Any, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import html

from src.market_data.models import Candle
from src.strategies.indicators_config import get_indicator_config
from src.strategies.horizontal_levels.extremum_detector import HorizontalExtremum
from dashboard.config import TIMEFRAME_MAP

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНАЯ: SMA по всем свечам
# ═══════════════════════════════════════════════════════════════

def _compute_sma(values: list, period: int) -> list:
    """Вычислить SMA-серию. Возвращает список, где первые period-1 элементов — None."""
    sma = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        sma[i] = sum(window) / period
    return sma


# ═══════════════════════════════════════════════════════════════
# ОСНОВНАЯ: построение свечного графика
# ═══════════════════════════════════════════════════════════════

def build_candlestick_figure(
    candles: List[Candle],
    selected_figi: str,
    selected_timeframe: str,
    show_rsi: bool = False,
    show_macd: bool = False,
    show_mfi_bb: bool = False,
) -> go.Figure:
    """
    Создать базовый свечной график с объёмом.

    Args:
        candles: Список свечей
        selected_figi: FIGI инструмента (для заголовка)
        selected_timeframe: Таймфрейм (для заголовка)
        show_rsi: Добавить нижнюю панель RSI (по умолчанию False)
        show_macd: Добавить нижнюю панель MACD (по умолчанию False)
    """
    df = pd.DataFrame([{
        "time": c.open_time,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
        "is_complete": c.is_complete,
        "candle_source": c.candle_source,
    } for c in candles])
    df = df.reset_index(drop=True)

    if show_rsi or show_macd or show_mfi_bb:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.65, 0.35],
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        )
    else:
        fig = go.Figure()

    # Свечи
    candlestick = go.Candlestick(
        x=df["time"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="OHLC",
        increasing=dict(fillcolor='#27807D', line=dict(color='#1a4d47', width=0.5)),
        decreasing=dict(fillcolor='#B73767', line=dict(color='#c62828', width=0.5)),
    )
    fig.add_trace(candlestick, row=1, col=1) if (show_rsi or show_macd or show_mfi_bb) else fig.add_trace(candlestick)

    # Объём
    volume_bar = go.Bar(
        x=df["time"],
        y=df["volume"],
        name="Volume",
        marker=dict(color='rgba(100, 100, 100, 0.3)'),
        opacity=0.3,
    )
    if show_rsi or show_macd or show_mfi_bb:
        fig.add_trace(volume_bar, row=1, col=1, secondary_y=True)
    else:
        volume_bar.update(yaxis="y2")
        fig.add_trace(volume_bar)

    layout_updates = dict(
        title=f"{selected_figi} - {selected_timeframe}",
        yaxis_title="Price (₽)",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        hovermode="x unified",
        height=600 if not (show_rsi or show_macd) else 850,
        margin=dict(l=50, r=100, t=50, b=50),
    )

    if not (show_rsi or show_macd or show_mfi_bb):
        layout_updates["yaxis2"] = dict(title="", overlaying="y", side="right", showgrid=False, showticklabels=False)

    if show_rsi:
        layout_updates["yaxis_title"] = "Price (₽)"
        layout_updates["yaxis2"] = dict(title="Volume", side="right", showgrid=False, showticklabels=False, rangemode="tozero")
        layout_updates["yaxis3_title"] = "RSI"
        layout_updates["yaxis3"] = dict(range=[0, 100], showgrid=True)

    if show_macd:
        layout_updates["yaxis_title"] = "Price (₽)"
        layout_updates["yaxis2"] = dict(title="Volume", side="right", showgrid=False, showticklabels=False, rangemode="tozero")
        layout_updates["yaxis3_title"] = "MACD"

    if show_mfi_bb:
        layout_updates["yaxis_title"] = "Price (₽)"
        layout_updates["yaxis2"] = dict(title="Volume", side="right", showgrid=False, showticklabels=False, rangemode="tozero")
        layout_updates["yaxis3_title"] = "MFI"
        layout_updates["yaxis3"] = dict(range=[0, 100], showgrid=True)

    fig.update_layout(**layout_updates)

    # X-axis range: last 500 candles
    x_range = [df["time"].iloc[max(0, len(df) - 500)], df["time"].iloc[-1]]
    if show_rsi or show_macd or show_mfi_bb:
        fig.update_xaxes(range=x_range, row=1, col=1)
        fig.update_xaxes(range=x_range, row=2, col=1)
    else:
        fig.update_xaxes(range=x_range)

    # X-axis labels: sparse, every N-th candle (avoids lag from thousands of tickvals)
    step = max(1, len(df) // 20)
    tick_indices = list(range(0, len(df), step))
    tick_dates = [df["time"].iloc[i] for i in tick_indices]
    tick_labels = [d.strftime('%m-%d %H:%M') for d in tick_dates]
    xaxis_update = dict(
        ticktext=tick_labels,
        tickvals=tick_dates,
        type="date",
        showticklabels=True,
        showline=False,
        showgrid=True,
        tickangle=-45,
        gridcolor='rgba(0,0,0,0.05)',
    )
    if show_rsi or show_macd or show_mfi_bb:
        fig.update_xaxes(xaxis_update, row=1, col=1)
        fig.update_xaxes(xaxis_update, row=2, col=1)
    else:
        fig.update_xaxes(xaxis_update)

    return fig, df


# ═══════════════════════════════════════════════════════════════
# ДОБАВЛЕНИЕ ИНДИКАТОРОВ НА ГРАФИК
# ═══════════════════════════════════════════════════════════════

def add_indicators_to_figure(
    fig: go.Figure,
    df: pd.DataFrame,
    candles: List[Candle],
    strategy_code: str,
    strategy=None,
) -> None:
    """
    Добавить линии индикаторов на график в зависимости от выбранной стратегии.

    Args:
        fig: Plotly Figure для модификации
        df: DataFrame со свечами
        candles: Список свечей (нужен для MA Adaptive)
        strategy_code: Код стратегии
        strategy: Инстанс стратегии (опционально, для получения параметров)
    """
    closes = df["close"].tolist()

    if strategy_code == "sma_crossover":
        sma_period = get_indicator_config("SMA").get("period", 50)
        sma_series = _compute_sma(closes, sma_period)
        fig.add_trace(go.Scatter(
            x=df["time"], y=sma_series, mode="lines",
            name=f"SMA({sma_period})",
            line=dict(color="blue", width=1), opacity=0.8, yaxis="y",
        ))

    elif strategy_code in ("dual_ma_crossover", "dual_ma_crossover_after_testing", "dual_ma_crossover_adaptive"):
        # SMA 50
        sma_series = _compute_sma(closes, 50)
        fig.add_trace(go.Scatter(
            x=df["time"], y=sma_series, mode="lines",
            name="SMA(50) Fast",
            line=dict(color="blue", width=2), opacity=0.9, yaxis="y",
        ))
        # MA Adaptive
        _add_ma_adaptive(fig, df, candles)

    elif strategy_code == "dual_ma_ma_crossover":
        _add_dual_ma_ma(fig, df, closes, strategy)

    elif strategy_code == "macd_plus":
        _add_macd_plus_indicators(fig, df, candles, add_macd_panel=True)

    elif strategy_code in ("candle_patterns", "candle_patterns_2"):
        _add_rsi_panel(fig, df, candles, strategy)

    elif strategy_code == "horizontal_levels":
        horizontal_extremums = strategy.get_extremums() if strategy else []
        add_horizontal_extremums_to_figure(fig, df, horizontal_extremums)

    elif strategy_code == "mfi_bb":
        _add_mfi_bb_panel(fig, df, candles, strategy)


def add_horizontal_extremums_to_figure(
    fig: go.Figure,
    df: pd.DataFrame,
    extremums: List[HorizontalExtremum],
) -> None:
    """
    Добавить на график экстремумы стратегии horizontal_levels.

    Цвета:
      - GLOBAL: #9C426E
      - MEDIUM: #3B557C
      - SMALL:  #BCA050
    Обводка: тонкая серая.
    """
    if not extremums:
        return

    strength_colors = {
        "GLOBAL": "#9C426E",
        "MEDIUM": "#3B557C",
        "SMALL": "#BCA050",
    }

    grouped = {}
    for ext in extremums:
        grouped.setdefault(ext.strength, []).append(ext)

    for strength, group in grouped.items():
        x_vals = []
        y_vals = []
        texts = []

        for ext in group:
            candle_idx = None
            for idx, row in df.iterrows():
                if row["time"] == ext.candle_time:
                    candle_idx = idx
                    break
            if candle_idx is None:
                continue
            x_vals.append(df["time"].iloc[candle_idx])  # Используем datetime вместо индекса
            y_vals.append(ext.price)
            texts.append(f"{strength}: {ext.price:.2f}₽")

        if not x_vals:
            continue

        fig.add_trace(go.Scatter(
            x=x_vals,
            y=y_vals,
            mode="markers",
            name=f"HL {strength}",
            marker=dict(
                symbol="circle",
                size=8,
                color=strength_colors.get(strength, "gray"),
                line=dict(color="gray", width=0.5),
            ),
            text=texts,
            hovertemplate="%{text}<extra></extra>",
        ))

    logger.info(f"✅ Drew {len(extremums)} horizontal level extremums")


def _add_ma_adaptive(fig: go.Figure, df: pd.DataFrame, candles: List[Candle]) -> None:
    """Добавить MA Adaptive(14) на график."""
    try:
        from src.strategies.indicators_builder import IndicatorsBuilder
        builder = IndicatorsBuilder(candles)
        ma_adaptive_series = builder.build_ma_adaptive(period=14)

        valid_count = sum(1 for v in ma_adaptive_series if v is not None)
        if valid_count > 0:
            fig.add_trace(go.Scatter(
                x=df["time"], y=ma_adaptive_series, mode="lines",
                name="MA Adaptive(14) Slow",
                line=dict(color="orange", width=2), opacity=0.9, yaxis="y",
            ))
            logger.info(f"✅ Drew MA Adaptive(14) line on chart ({valid_count} valid points)")
        else:
            logger.warning("⚠️ MA Adaptive has no valid values")
    except Exception as e:
        logger.error(f"❌ Failed to draw MA Adaptive: {e}")


def _add_macd_plus_indicators(fig: go.Figure, df: pd.DataFrame, candles: List[Candle], add_macd_panel: bool = False) -> None:
    """Добавить MA200, сигнальные точки MACD и панель MACD для macd_plus."""
    try:
        from src.strategies.macd_plus.strategy import MACDPlusStrategy
        strategy = MACDPlusStrategy("1d")  # только для расчёта индикаторов

        ma200_series = strategy._calculate_sma_series(candles, strategy.trend_ma_period)
        macd_line, signal_line, histogram = strategy._calculate_macd_series(candles)

        # MA200
        fig.add_trace(go.Scatter(
            x=df["time"], y=ma200_series, mode="lines",
            name=f"MA{strategy.trend_ma_period}",
            line=dict(color="blue", width=1.5), opacity=0.8, yaxis="y",
        ))

        # MACD пересечения — отметки на ценовом графике
        entry_x, entry_y, entry_colors, entry_names = [], [], [], []
        cross_long_x, cross_long_y, cross_short_x, cross_short_y = [], [], [], []
        for i in range(1, len(candles)):
            prev_macd = macd_line[i - 1]
            curr_macd = macd_line[i]
            prev_signal = signal_line[i - 1]
            curr_signal = signal_line[i]
            if any(v is None for v in [prev_macd, curr_macd, prev_signal, curr_signal]):
                continue
            ma200 = ma200_series[i]
            close = candles[i].close
            if ma200 is None:
                continue

            if prev_macd <= prev_signal and curr_macd > curr_signal:
                # Точка пересечения на панели MACD
                cross_long_x.append(i)
                cross_long_y.append(curr_macd)
                if curr_macd < 0 and ma200 < close:
                    entry_x.append(i)
                    entry_y.append(close)
                    entry_colors.append("green")
                    entry_names.append("MACD Long")
            elif prev_macd >= prev_signal and curr_macd < curr_signal:
                # Точка пересечения на панели MACD
                cross_short_x.append(i)
                cross_short_y.append(curr_macd)
                if curr_macd > 0 and ma200 > close:
                    entry_x.append(i)
                    entry_y.append(close)
                    entry_colors.append("red")
                    entry_names.append("MACD Short")

        if entry_x:
            fig.add_trace(go.Scatter(
                x=[candles[i].open_time for i in entry_x],
                y=entry_y, mode="markers",
                marker=dict(symbol="diamond", size=8, color=entry_colors,
                            line=dict(color="black", width=0.5)),
                name="MACD Signal",
                text=entry_names,
                hovertemplate="%{text}<extra></extra>",
            ))

        # Панель MACD
        if add_macd_panel:
            x_times = df["time"].tolist()

            # MACD линия
            fig.add_trace(go.Scatter(
                x=x_times, y=macd_line, mode="lines",
                name=f"MACD({strategy.macd_fast_period},{strategy.macd_slow_period},{strategy.macd_signal_period})",
                line=dict(color="#2196F3", width=1.5),
            ), row=2, col=1)

            # Сигнальная линия
            fig.add_trace(go.Scatter(
                x=x_times, y=signal_line, mode="lines",
                name="Signal",
                line=dict(color="#FF9800", width=1.5),
            ), row=2, col=1)

            # Гистограмма
            colors = ["#4CAF50" if (h or 0) >= 0 else "#F44336" for h in histogram]
            fig.add_trace(go.Bar(
                x=x_times, y=histogram,
                name="Histogram",
                marker=dict(color=colors),
                opacity=0.6,
            ), row=2, col=1)

            # Нулевая линия
            fig.add_hline(y=0, line=dict(color="gray", width=1, dash="dash"), row=2, col=1)

            # Точки пересечений на панели MACD
            if cross_long_x:
                fig.add_trace(go.Scatter(
                    x=[candles[i].open_time for i in cross_long_x],
                    y=cross_long_y, mode="markers",
                    marker=dict(symbol="circle", size=5, color="green",
                                line=dict(color="darkgreen", width=0.5)),
                    name="MACD Cross Up",
                    hovertemplate="Cross Up<extra></extra>",
                ), row=2, col=1)

            if cross_short_x:
                fig.add_trace(go.Scatter(
                    x=[candles[i].open_time for i in cross_short_x],
                    y=cross_short_y, mode="markers",
                    marker=dict(symbol="circle", size=5, color="red",
                                line=dict(color="darkred", width=0.5)),
                    name="MACD Cross Down",
                    hovertemplate="Cross Down<extra></extra>",
                ), row=2, col=1)

        logger.info(f"✅ Drew MACD Plus indicators: MA{strategy.trend_ma_period} + {len(entry_x)} signal markers")
    except Exception as e:
        logger.error(f"❌ Failed to draw MACD Plus indicators: {e}")


def _add_dual_ma_ma(fig: go.Figure, df: pd.DataFrame, closes: list, strategy) -> None:
    """Добавить Dual MA + MA индикаторы (из конфига стратегии)."""
    try:
        if strategy is None:
            return
        indicator_params = strategy.get_indicator_params()

        # MA Slow
        ma_slow_period = indicator_params["ma_slow"]["period"]
        ma_slow_series = _compute_sma(closes, ma_slow_period)
        fig.add_trace(go.Scatter(
            x=df["time"], y=ma_slow_series, mode="lines",
            name=indicator_params["ma_slow"]["name"],
            line=dict(color=indicator_params["ma_slow"]["color"], width=2),
            opacity=0.9, yaxis="y",
        ))

        # MA Fast
        ma_fast_period = indicator_params["ma_fast"]["period"]
        ma_fast_series = _compute_sma(closes, ma_fast_period)
        valid_count = sum(1 for v in ma_fast_series if v is not None)
        if valid_count > 0:
            fig.add_trace(go.Scatter(
                x=df["time"], y=ma_fast_series, mode="lines",
                name=indicator_params["ma_fast"]["name"],
                line=dict(color=indicator_params["ma_fast"]["color"], width=2),
                opacity=0.9, yaxis="y",
            ))
            logger.info(f"✅ Drew {indicator_params['ma_fast']['name']} ({valid_count} valid points)")
        else:
            logger.warning(f"⚠️ {indicator_params['ma_fast']['name']} has no valid values")
    except Exception as e:
        logger.error(f"❌ Failed to draw dual_ma_ma_crossover indicators: {e}")


def _add_rsi_panel(fig: go.Figure, df: pd.DataFrame, candles: List[Candle], strategy) -> None:
    """
    Добавить панель RSI(14) с зонами 70/30 под свечным графиком.

    Рисует:
    - Линию RSI(14)
    - Пунктирные линии на 70 (перекупленность) и 30 (перепроданность)
    - Заливку зон: красную выше 70, зелёную ниже 30
    """
    try:
        from src.strategies.indicators_builder import IndicatorsBuilder
        builder = IndicatorsBuilder(candles)
        rsi_series = builder.build_rsi(period=14)

        valid_count = sum(1 for v in rsi_series if v is not None)
        if valid_count == 0:
            logger.warning("RSI series has no valid values")
            return

        x = list(range(len(candles)))

        # Линия RSI
        fig.add_trace(go.Scatter(
            x=x, y=rsi_series, mode="lines",
            name="RSI(14)",
            line=dict(color="#7B2FBE", width=1.5),
            opacity=0.9,
        ), row=2, col=1)

        # ── Зона перекупленности (>=70) ──
        # Пунктир 70
        fig.add_trace(go.Scatter(
            x=x, y=[70] * len(x), mode="lines",
            name="Overbought (70)",
            line=dict(color="rgba(200, 0, 0, 0.5)", width=1, dash="dash"),
            showlegend=False,
        ), row=2, col=1)
        # Верхняя граница заливки
        fig.add_trace(go.Scatter(
            x=x, y=[100] * len(x), mode="lines",
            fill="tonexty",
            fillcolor="rgba(200, 0, 0, 0.06)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ), row=2, col=1)

        # ── Зона перепроданности (<=30) ──
        # Пунктир 30
        fig.add_trace(go.Scatter(
            x=x, y=[30] * len(x), mode="lines",
            name="Oversold (30)",
            line=dict(color="rgba(0, 150, 0, 0.5)", width=1, dash="dash"),
            showlegend=False,
        ), row=2, col=1)
        # Нижняя граница заливки
        fig.add_trace(go.Scatter(
            x=x, y=[0] * len(x), mode="lines",
            fill="tonexty",
            fillcolor="rgba(0, 150, 0, 0.06)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ), row=2, col=1)

        logger.info(f"Drew RSI(14) panel ({valid_count} valid points)")
    except Exception as e:
        logger.error(f"Failed to draw RSI panel: {e}")


def _add_mfi_bb_panel(
    fig: go.Figure,
    df: pd.DataFrame,
    candles: List[Candle],
    strategy,
) -> None:
    """
    Добавить панель MFI с полосами Боллинджера под свечным графиком.

    Рисует на row=2:
    - Линию MFI(14)
    - Полосы Боллинджера (upper/mid/lower) на MFI
    - Пунктирные уровни 80/20
    - Заливку между upper и lower полосами
    """
    try:
        if strategy is None:
            return

        mfi_series = strategy._calculate_mfi_series(candles)
        bb_mid, bb_upper, bb_lower = strategy._calculate_bollinger_bands(mfi_series)

        valid_count = sum(1 for v in mfi_series if v is not None)
        if valid_count == 0:
            logger.warning("MFI series has no valid values")
            return

        # ← ИСПРАВЛЕНИЕ 1: datetime вместо числовых индексов
        x = df["time"].tolist()

        # ── Заливка между верхней и нижней полосой BB ──
        fig.add_trace(go.Scatter(
            x=x, y=bb_upper, mode="lines",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=x, y=bb_lower, mode="lines",
            fill="tonexty",
            fillcolor="rgba(0, 230, 118, 0.08)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ), row=2, col=1)

        # ── BB Upper ──
        fig.add_trace(go.Scatter(
            x=x, y=bb_upper, mode="lines",
            name=f"BB Upper ({strategy.bb_period}, {strategy.bb_multiplier})",
            line=dict(color="rgba(0, 230, 118, 0.5)", width=1),
            opacity=0.8,
        ), row=2, col=1)

        # ── BB Mid (SMA MFI) ──
        fig.add_trace(go.Scatter(
            x=x, y=bb_mid, mode="lines",
            name=f"BB Mid (SMA {strategy.bb_period})",
            line=dict(color="rgba(255, 255, 255, 0.7)", width=1, dash="dot"),
            opacity=0.8,
        ), row=2, col=1)

        # ── BB Lower ──
        fig.add_trace(go.Scatter(
            x=x, y=bb_lower, mode="lines",
            name=f"BB Lower ({strategy.bb_period}, {strategy.bb_multiplier})",
            line=dict(color="rgba(0, 230, 118, 0.5)", width=1),
            opacity=0.8,
        ), row=2, col=1)

        # ── Линия MFI ──
        # ← ИСПРАВЛЕНИЕ 2: белая линия вместо чёрной (#000000 → #FFFFFF)
        fig.add_trace(go.Scatter(
            x=x, y=mfi_series, mode="lines",
            name=f"MFI({strategy.mfi_period})",
            line=dict(color="#000000", width=2),
            opacity=0.9,
        ), row=2, col=1)

        # ── Уровни 80 / 20 / 50 ──
        fig.add_hline(
            y=strategy.ob_level,
            line=dict(color="rgba(255, 23, 68, 0.6)", width=1, dash="dash"),
            row=2, col=1,
            annotation_text="OB 80",
            annotation_position="top right",
        )
        fig.add_hline(
            y=strategy.os_level,
            line=dict(color="rgba(0, 230, 118, 0.6)", width=1, dash="dash"),
            row=2, col=1,
            annotation_text="OS 20",
            annotation_position="bottom right",
        )
        fig.add_hline(
            y=50,
            line=dict(color="rgba(255, 255, 255, 0.6)", width=1, dash="dot"),
            row=2, col=1,
        )

        # ← ИСПРАВЛЕНИЕ 3: явный диапазон оси Y для subplot
        fig.update_yaxes(range=[-10, 110], row=2, col=1)

        logger.info(f"Drew MFI+BB panel ({valid_count} valid MFI points)")
    except Exception as e:
        logger.error(f"Failed to draw MFI BB panel: {e}")


# ═══════════════════════════════════════════════════════════════
# ДОБАВЛЕНИЕ СДЕЛОК НА ГРАФИК
# ═══════════════════════════════════════════════════════════════

def add_trades_to_figure(fig: go.Figure, trades: list) -> None:
    """
    Добавить точки входа/выхода на график.

    Args:
        fig: Plotly Figure
        trades: Список Trade-объектов
    """
    if not trades:
        return

    entry_trades = []
    exit_trades = []

    for trade in trades:
        entry_trades.append({
            "index": trade.entry_index,
            "price": trade.entry_price,
            "time": trade.entry_time,
        })
        if not trade.is_open():
            exit_trades.append({
                "index": trade.exit_index,
                "price": trade.exit_price,
                "time": trade.exit_time,
                "pnl_percent": trade.pnl_percent,
                "is_win": trade.pnl_percent > 0 if trade.pnl_percent else False,
            })

    # ENTRY — серые треугольники вверх
    if entry_trades:
        fig.add_trace(go.Scatter(
            x=[t["time"] for t in entry_trades],
            y=[t["price"] for t in entry_trades],
            mode='markers',
            marker=dict(symbol='triangle-up', size=8, color='gray',
                        line=dict(color='darkgray', width=0.5)),
            name='Entry',
            text=[f"Entry: {t['price']:.2f}₽" for t in entry_trades],
            hovertemplate='%{text}<extra></extra>',
        ))

    # EXIT WIN — зелёные треугольники вниз
    win_trades = [t for t in exit_trades if t["is_win"]]
    if win_trades:
        fig.add_trace(go.Scatter(
            x=[t["time"] for t in win_trades],
            y=[t["price"] for t in win_trades],
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color='green',
                        line=dict(color='darkgreen', width=0.5)),
            name='Exit (Win)',
            text=[f"Exit: {t['price']:.2f}₽ ({t['pnl_percent']:+.2f}%)" for t in win_trades],
            hovertemplate='%{text}<extra></extra>',
        ))

    # EXIT LOSS — красные треугольники вниз
    loss_trades = [t for t in exit_trades if not t["is_win"]]
    if loss_trades:
        fig.add_trace(go.Scatter(
            x=[t["time"] for t in loss_trades],
            y=[t["price"] for t in loss_trades],
            mode='markers',
            marker=dict(symbol='triangle-down', size=8, color='red',
                        line=dict(color='darkred', width=0.5)),
            name='Exit (Loss)',
            text=[f"Exit: {t['price']:.2f}₽ ({t['pnl_percent']:+.2f}%)" for t in loss_trades],
            hovertemplate='%{text}<extra></extra>',
        ))


# ═══════════════════════════════════════════════════════════════
# ДОБАВЛЕНИЕ ЭКСТРЕМУМОВ НА ГРАФИК
# ═══════════════════════════════════════════════════════════════

def add_extremums_to_figure(fig: go.Figure, df: pd.DataFrame, extremums: list) -> None:
    """
    Добавить V-образные экстремумы на график.

    Args:
        fig: Plotly Figure
        df: DataFrame со свечами
        extremums: Список Extremum-объектов
    """
    if not extremums:
        return

    max_x, max_y, max_prices = [], [], []
    min_x, min_y, min_prices = [], [], []

    for ext in extremums:
        candle_idx = None
        for idx, row in df.iterrows():
            if row["time"] == ext.candle_time:
                candle_idx = idx
                break
        if candle_idx is None:
            continue

        if ext.extremum_type == "MAX":
            max_x.append(candle_idx)
            max_y.append(ext.price)
            max_prices.append(f"{ext.price:.2f}")
        else:
            min_x.append(candle_idx)
            min_y.append(ext.price)
            min_prices.append(f"{ext.price:.2f}")

    if max_x:
        fig.add_trace(go.Scatter(
            x=max_x, y=max_y, mode='markers',
            marker=dict(symbol='circle', size=8, color='#82C7B7',
                        line=dict(color='darkgreen', width=0.5)),
            name='Local Maxima (V-shaped)',
            text=max_prices,
            hovertemplate='MAX: %{text}₽',
        ))

    if min_x:
        fig.add_trace(go.Scatter(
            x=min_x, y=min_y, mode='markers',
            marker=dict(symbol='circle', size=8, color='#D5505C',
                        line=dict(color='darkred', width=0.5)),
            name='Local Minima (V-shaped)',
            text=min_prices,
            hovertemplate='MIN: %{text}₽',
        ))


# ═══════════════════════════════════════════════════════════════
# ДОБАВЛЕНИЕ УРОВНЕЙ НА ГРАФИК
# ═══════════════════════════════════════════════════════════════

def add_levels_to_figure(fig: go.Figure, df: pd.DataFrame, levels: list) -> None:
    """
    Добавить уровни поддержки/сопротивления на график.

    Args:
        fig: Plotly Figure
        df: DataFrame со свечами
        levels: Список Level-объектов
    """
    if not levels:
        return

    levels_sorted = sorted(levels, key=lambda x: x.priority, reverse=True)
    for level in levels_sorted:
        x0 = level.start_index if level.start_index is not None else 0
        x1 = level.end_index if level.end_index is not None else len(df) - 1
        fig.add_shape(
            type="line",
            x0=x0, x1=x1,
            y0=level.price, y1=level.price,
            line=dict(
                color=level.color,
                width=level.line_width,
                dash=level.line_dash,
            ),
            opacity=level.opacity,
            layer="below",
        )

    logger.info(f"✅ Drawn {len(levels_sorted)} levels on chart")


# ═══════════════════════════════════════════════════════════════
# ПОСТРОЕНИЕ ТАБЛИЦЫ РЫНОЧНЫХ ДАННЫХ
# ═══════════════════════════════════════════════════════════════

def build_market_data_table(
    candles: List[Candle],
    selected_figi: str,
    selected_timeframe: str,
    broker,
) -> html.Table:
    """Построить HTML-таблицу со всеми свечами."""
    ticker_info = broker.get_instrument_by_figi(selected_figi)
    ticker = ticker_info["ticker"] if ticker_info else selected_figi

    df_table = pd.DataFrame([{
        "figi": selected_figi,
        "ticker": ticker,
        "timeframe": selected_timeframe,
        "open_time": c.open_time.strftime('%Y-%m-%d %H:%M:%S'),
        "open": f"{c.open:.2f}",
        "high": f"{c.high:.2f}",
        "low": f"{c.low:.2f}",
        "close": f"{c.close:.2f}",
        "volume": f"{c.volume:,}",
        "is_complete": f"{c.is_complete:,}",
        "candle_source": f"{c.candle_source:,}",
    } for c in candles])

    return html.Table([
        html.Thead(html.Tr([
            html.Th("FIGI", style=_th()),
            html.Th("Ticker", style=_th()),
            html.Th("Timeframe", style=_th()),
            html.Th("Open Time", style=_th()),
            html.Th("Open (₽)", style=_th_right()),
            html.Th("High (₽)", style=_th_right()),
            html.Th("Low (₽)", style=_th_right()),
            html.Th("Close (₽)", style=_th_right()),
            html.Th("Volume", style=_th_right()),
            html.Th("is_complete", style=_th_right()),
            html.Th("candle_source", style=_th_right()),
        ])),
        html.Tbody([
            html.Tr([
                html.Td(row["figi"], style=_td()),
                html.Td(row["ticker"], style=_td()),
                html.Td(row["timeframe"], style=_td()),
                html.Td(row["open_time"], style={**_td(), "fontFamily": "monospace", "fontSize": "13px"}),
                html.Td(row["open"], style=_td_right()),
                html.Td(row["high"], style=_td_right()),
                html.Td(row["low"], style=_td_right()),
                html.Td(row["close"], style=_td_right()),
                html.Td(row["volume"], style=_td_right()),
                html.Td(row["is_complete"], style=_td_right()),
                html.Td(row["candle_source"], style=_td_right()),
            ])
            for _, row in df_table.iterrows()
        ]),
    ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px", "backgroundColor": "white"})


# ═══════════════════════════════════════════════════════════════
# ПОМЕСЯЧНЫЕ ГРАФИКИ
# ═══════════════════════════════════════════════════════════════

def build_monthly_charts(monthly_data: dict) -> html.Div:
    """
    Строит 4 столбчатых графика (2×2): баланс, PnL, Win/Loss, Avg Hold.

    Args:
        monthly_data: результат compute_monthly_breakdown()

    Returns:
        html.Div с 4 dcc.Graph в сетке 2×2
    """
    from dash import dcc

    months = monthly_data.get("months", [])

    if not months:
        return html.Div("No trade data for monthly charts", style={"padding": "20px", "color": "#999"})

    # Общий стиль для всех маленьких графиков
    chart_style = {"height": 280, "margin": "0"}

    # ── 1. Balance ──
    balance_fig = go.Figure()
    balance_fig.add_trace(go.Bar(
        x=months,
        y=monthly_data["balance"],
        marker=dict(
            color=["#247068" if b >= monthly_data["balance"][0] else "#A13455" for b in monthly_data["balance"]],
        ),
        text=[f"{b:,.0f}" for b in monthly_data["balance"]],
        textposition="outside",
    ))
    balance_fig.update_layout(
        title="Balance (cumulative)",
        template="plotly_white",
        height=280,
        margin=dict(l=40, r=20, t=40, b=40),
        showlegend=False,
        xaxis=dict(tickangle=-45),
    )

    # ── 2. Monthly PnL ──
    pnl_fig = go.Figure()
    pnl_fig.add_trace(go.Bar(
        x=months,
        y=monthly_data["pnl"],
        marker=dict(
            color=["#247068" if p >= 0 else "#A13455" for p in monthly_data["pnl"]],
        ),
        text=[f"{p:+,.0f}" for p in monthly_data["pnl"]],
        textposition="outside",
    ))
    pnl_fig.update_layout(
        title="Monthly PnL",
        template="plotly_white",
        height=280,
        margin=dict(l=40, r=20, t=40, b=40),
        showlegend=False,
        xaxis=dict(tickangle=-45),
    )

    # ── 3. Win / Loss ──
    wl_fig = go.Figure()
    wl_fig.add_trace(go.Bar(
        name="Win",
        x=months,
        y=monthly_data["win_count"],
        marker=dict(color="#247068"),
        text=monthly_data["win_count"],
        textposition="inside",
    ))
    wl_fig.add_trace(go.Bar(
        name="Loss",
        x=months,
        y=monthly_data["loss_count"],
        marker=dict(color="#A13455"),
        text=monthly_data["loss_count"],
        textposition="inside",
    ))
    wl_fig.update_layout(
        title="Win / Loss Trades",
        template="plotly_white",
        height=280,
        margin=dict(l=40, r=20, t=40, b=40),
        barmode="stack",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(tickangle=-45),
    )

    # ── 4. Avg Hold ──
    hold_fig = go.Figure()
    hold_fig.add_trace(go.Bar(
        x=months,
        y=monthly_data["avg_hold"],
        marker=dict(color="#2A5673"),
        text=[f"{h:.1f}d" for h in monthly_data["avg_hold"]],
        textposition="outside",
    ))
    hold_fig.update_layout(
        title="Avg Hold (days)",
        template="plotly_white",
        height=280,
        margin=dict(l=40, r=20, t=40, b=40),
        showlegend=False,
        xaxis=dict(tickangle=-45),
    )

    # ── Сетка 2×2 ──
    return html.Div([
        html.H3("📅 Monthly Breakdown", style={
            "textAlign": "center", "color": "#555", "marginBottom": "16px",
        }),
        html.Div([
            html.Div(dcc.Graph(figure=balance_fig, style=chart_style), style={"flex": "1", "minWidth": "400px"}),
            html.Div(dcc.Graph(figure=pnl_fig, style=chart_style), style={"flex": "1", "minWidth": "400px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "12px", "marginBottom": "12px"}),
        html.Div([
            html.Div(dcc.Graph(figure=wl_fig, style=chart_style), style={"flex": "1", "minWidth": "400px"}),
            html.Div(dcc.Graph(figure=hold_fig, style=chart_style), style={"flex": "1", "minWidth": "400px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "12px"}),
    ], style={
        "backgroundColor": "#f9f4ff", "borderRadius": "8px", "padding": "20px",
        "border": "1px solid #d1c4e9", "marginTop": "24px",
    })


def _th():
    return {"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}


def _th_right():
    return {"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}


def _td():
    return {"padding": "10px", "borderBottom": "1px solid #eee"}


def _td_right():
    return {"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}
