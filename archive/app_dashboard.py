import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go
import pandas as pd

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe
from src.strategies.candle_pattern_strategy import CandlePatternStrategy
# from src.strategies.levels_detector import LevelsDetector
# from src.strategies.levels_detector_debug import LevelsDetector
from src.strategies.levels_detector_v5 import LevelsDetector

from src.strategies.levels_builder import LevelsBuilder
from src.strategies.indicators_builder import IndicatorsBuilder
from src.strategies.indicators_config import get_indicator_config

from src.strategies.levels_config import get_clustering_tolerance
from src.strategies.extremums_config import get_extremums_config

# ============ НОВОЕ ДЛЯ SMA CROSSOVER STRATEGY ============
from src.strategies.sma_crossover_strategy import SMACrossoverStrategy
from src.strategies.sma_crossover_config import get_sma_period
# ========================================================

# Настройка
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashApp")
load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")

# Инициализируем компоненты
logger.info("Initializing components...")
broker = BrokerFactory.create("sdk", token)
history = HistoryManager(broker, db_path="marketdata.db")
# Инициализируем SMA Crossover Strategy
sma_strategy = {}  # Кэш для стратегий по таймфреймам

# Загружаем доступные комбинации из БД
logger.info("Loading available data combinations from DB...")
available_combinations = history.get_available_combinations()
logger.info(f"✓ Available combinations: {available_combinations}")

LEVELS_CONFIG = {
    "30m": {"lookback_candles": 200, "clustering_range": 0.005, "min_touches": 2},
    "1h": {"lookback_candles": 150, "clustering_range": 0.01, "min_touches": 2},
    "4h": {"lookback_candles": 100, "clustering_range": 0.02, "min_touches": 2},
    "1d": {"lookback_candles": 50, "clustering_range": 0.05, "min_touches": 1},
}

# Инициализируем стратегию и детектор экстремумов
strategy = CandlePatternStrategy()
indicators_builder = IndicatorsBuilder(debug=True)
# levels_detector = LevelsDetector(config=get_extremums_config, debug=True)

# Создаем маппинг: ticker → figi
ticker_to_figi = {}
figi_to_ticker = {}

session = history.Session()
try:
    from src.market_data.history_manager import MarketData
    rows = session.query(MarketData.figi, MarketData.ticker).distinct().all()
    for figi, ticker in rows:
        ticker_to_figi[ticker] = figi
        figi_to_ticker[figi] = ticker
finally:
    session.close()

logger.info(f"✓ Created ticker mapping: {ticker_to_figi}")

# Инициализируем Dash app
app = dash.Dash(__name__)
app.title = "Trading Dashboard"

# ============================================================================
# LAYOUT
# ============================================================================

app.layout = html.Div([
    html.Div([
        html.H1("📈 Trading Dashboard", style={"textAlign": "center", "marginBottom": 20}),
        
        # Контролы
        html.Div([
            html.Div([
                html.Label("Select Instrument:", style={"fontWeight": "bold"}),
                dcc.Dropdown(
                    id="instrument-selector",
                    options=[
                        {"label": figi_to_ticker.get(figi, figi), "value": figi}
                        for figi in sorted(available_combinations.keys())
                    ],
                    value=list(available_combinations.keys())[0] if available_combinations else None,
                    style={"width": "250px"}
                ),
            ], style={"display": "inline-block", "marginRight": "30px"}),
            
            html.Div([
                html.Label("Select Timeframe:", style={"fontWeight": "bold"}),
                dcc.Dropdown(
                    id="timeframe-selector",
                    options=[],
                    value=None,
                    style={"width": "150px"}
                ),
            ], style={"display": "inline-block"}),
        ], style={
            "padding": "20px",
            "backgroundColor": "#f0f0f0",
            "borderRadius": "5px",
            "marginBottom": "20px"
        }),
        
        # График
        dcc.Graph(id="candlestick-chart"),
        
        # Информация о свечах
        html.Div(id="candles-info", style={"marginTop": "20px", "padding": "10px", "backgroundColor": "#f9f9f9"}),
        
        # 📊 ТАБЛИЦА МАРКЕТ ДАННЫХ
        html.Div([
            html.H2("📋 Market Data Table", style={"marginTop": "40px", "marginBottom": "20px"}),
            html.Div(
                id="market-data-table-container",
                style={
                    "overflowX": "auto",
                    "marginTop": "20px",
                    "border": "1px solid #ddd",
                    "borderRadius": "5px"
                }
            ),
        ], style={"marginTop": "30px"}),
        
    ], style={"padding": "20px", "maxWidth": "1400px", "margin": "0 auto"})
])

# ============================================================================
# CALLBACKS
# ============================================================================

@app.callback(
    Output("timeframe-selector", "options"),
    Input("instrument-selector", "value")
)
def update_timeframes(selected_figi):
    """Обновляет доступные таймфреймы для выбранного инструмента."""
    if selected_figi is None:
        return []
    timeframes = available_combinations.get(selected_figi, [])
    options = [{"label": tf, "value": tf} for tf in sorted(timeframes)]
    return options

@app.callback(
    Output("timeframe-selector", "value"),
    Input("timeframe-selector", "options")
)
def set_default_timeframe(available_options):
    """Устанавливает первый доступный таймфрейм по умолчанию"""
    if available_options:
        return available_options[0]["value"]
    return None

@app.callback(
    [Output("candlestick-chart", "figure"),
     Output("candles-info", "children"),
     Output("market-data-table-container", "children")],
    [Input("timeframe-selector", "value"),
     Input("instrument-selector", "value")]
)
def update_chart(selected_timeframe: str, selected_figi: str):
    """Обновляет график, паттерны и экстремумы."""
    
    if selected_figi is None or selected_timeframe is None:
        return go.Figure(), html.P("No selection"), html.Div()
    
    logger.info(f"Updating chart: {selected_figi} {selected_timeframe}")
    
    extremums_config = get_extremums_config(selected_timeframe)
    detector = LevelsDetector(config=extremums_config, debug=True)
    # Конвертируем строку в Timeframe enum
    timeframe_map = {
        "30m": Timeframe.M30,
        "1h": Timeframe.H1,
        "4h": Timeframe.H4,
        "1d": Timeframe.D1,
    }
    timeframe = timeframe_map.get(selected_timeframe, Timeframe.H1)
    
    # Получаем свечи ИЗ БД
    candles = history.get_candles(selected_figi, timeframe)
    extremums = detector.find_v_extremums(candles)
    if not candles:
        empty_fig = go.Figure()
        empty_fig.add_annotation(
            text=f"⚠️ No data for {selected_figi} {selected_timeframe}",
            showarrow=False,
            font=dict(size=16, color="red")
        )
        return empty_fig, html.P("No data"), html.Div()
    
    # ============================================================
    # ФИЛЬТРУЕМ СВЕЧИ: УБИРАЕМ "ПУСТЫЕ" (объем = 0 или NaN)
    # ============================================================
    
    filtered_candles = []
    for candle in candles:
        if candle.volume and candle.volume > 0:
            filtered_candles.append(candle)
    
    if not filtered_candles:
        empty_fig = go.Figure()
        empty_fig.add_annotation(
            text=f"⚠️ No valid trading data for {selected_figi} {selected_timeframe}",
            showarrow=False,
            font=dict(size=16, color="red")
        )
        return empty_fig, html.P("No valid data"), html.Div()
    
    candles = filtered_candles

    # 📊 РАССЧИТЫВАЕМ ИНДИКАТОРЫ
    indicators_results = indicators_builder.build_indicators(candles)

    # Преобразуем индикаторы в удобный вид для DataFrame
    indicators_df_rows = []
    for idx, c in enumerate(candles):
        row = {
            "time": c.open_time,
            "close": c.close,
        }
        # Для простоты: считаем, что все индикаторы считаются на последней свече,
        # но будем отображать только последнее значение в отдельной таблице/боксе
        # (полноценные серии можно будет добавить позже)
        indicators_df_rows.append(row)

    # Текущее (последнее) значение индикаторов
    last_indicators_values = {}
    for name, result in indicators_results.items():
        if result is None:
            continue
        val = result.value
        if isinstance(val, dict):
            # Для составных индикаторов — разворачиваем ключи
            for k, v in val.items():
                last_indicators_values[f"{name}_{k}"] = v
        else:
            last_indicators_values[name] = val
    

    # Конвертируем в DataFrame для графика
    df = pd.DataFrame([{
        "time": c.open_time,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    } for c in candles])
    
    df = df.reset_index(drop=True)
    

    # ==============================
    # 📈 SMA / EMA ПО ВСЕМ СВЕЧАМ
    # ==============================
    closes = df["close"].tolist()

    def compute_sma(values: list[float], period: int) -> list[float]:
        sma: list[float | None] = [None] * len(values)
        for i in range(period - 1, len(values)):
            window = values[i - period + 1: i + 1]
            sma[i] = sum(window) / period
        return sma

    
    sma_period = get_indicator_config("SMA").get("period")
    


    sma_series = compute_sma(closes, sma_period)
    



    # Создаем свечной график
    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="OHLC",
        increasing=dict(
            fillcolor='#27807D',
            line=dict(color='#1a4d47', width=0.5)
        ),
        decreasing=dict(
            fillcolor='#B73767',
            line=dict(color='#c62828', width=0.5)
        ),
    )])
    
    # Добавляем объем
    fig.add_trace(go.Bar(
        x=df.index,
        y=df["volume"],
        name="Volume",
        yaxis="y2",
        marker=dict(color='rgba(100, 100, 100, 0.3)'),
        opacity=0.3,
    ))
    
        # Добавляем линии SMA и EMA
    fig.add_trace(go.Scatter(
        x=df.index,
        y=sma_series,
        mode="lines",
        name=f"SMA({sma_period})",
        line=dict(color="blue", width=1),
        opacity=0.8,
        yaxis="y",
    ))

   



    # ========================================================
    # 🕵️♂️ ОПРЕДЕЛЕНИЕ ПАТТЕРНОВ
    # ========================================================
    
    patterns = strategy.detect_all_patterns(candles)
    if patterns:
        logger.info(f"Found {len(patterns)} patterns")
        bullish_x, bullish_y, bullish_txt = [], [], []
        bearish_x, bearish_y, bearish_txt = [], [], []
        neutral_x, neutral_y, neutral_txt = [], [], []
        
        for p in patterns:
            candle_idx = None
            for idx, row in df.iterrows():
                if row["time"] == p.timestamp:
                    candle_idx = idx
                    break
            
            if candle_idx is None:
                continue
            
            if p.type == "BULLISH":
                bullish_x.append(candle_idx)
                bullish_y.append(p.price * 0.999)
                bullish_txt.append(p.name)
            elif p.type == "BEARISH":
                bearish_x.append(candle_idx)
                bearish_y.append(p.price * 1.001)
                bearish_txt.append(p.name)
            else:
                neutral_x.append(candle_idx)
                neutral_y.append(p.price * 1.001)
                neutral_txt.append(p.name)
        
        # Добавляем маркеры на график
        if bullish_x:
            fig.add_trace(go.Scattergl(
                x=bullish_x, y=bullish_y, mode='markers',
                marker=dict(symbol='triangle-up', size=10, color='green'),
                text=bullish_txt, name='Bullish Pattern',
                hovertemplate='%{text}'
            ))
        
        if bearish_x:
            fig.add_trace(go.Scattergl(
                x=bearish_x, y=bearish_y, mode='markers',
                marker=dict(symbol='triangle-down', size=10, color='red'),
                text=bearish_txt, name='Bearish Pattern',
                hovertemplate='%{text}'
            ))
        
        if neutral_x:
            fig.add_trace(go.Scattergl(
                x=neutral_x, y=neutral_y, mode='markers',
                marker=dict(symbol='circle', size=8, color='gray'),
                text=neutral_txt, name='Pattern',
                hovertemplate='%{text}'
            ))
    
    # ========================================================
    # 🔍 ОПРЕДЕЛЕНИЕ V-ОБРАЗНЫХ ЭКСТРЕМУМОВ
    # ========================================================
    
    extremums = detector.find_v_extremums(candles)
    logger.info(f"Found {len(extremums)} V-shaped extremums")
    # ========================================================
# 📊 ПОСТРОЕНИЕ УРОВНЕЙ ПОДДЕРЖКИ/СОПРОТИВЛЕНИЯ
    # ========================================================
    levels_builder = LevelsBuilder(timeframe=selected_timeframe, debug=True)
    levels = levels_builder.build_levels(extremums, candles)
    logger.info(f"✅ Built {len(levels)} support/resistance levels")

    
    
    if extremums:
        max_x, max_y, max_prices = [], [], []
        min_x, min_y, min_prices = [], [], []
        
        for ext in extremums:
            # Находим индекс свечи по времени
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
            else:  # MIN
                min_x.append(candle_idx)
                min_y.append(ext.price)
                min_prices.append(f"{ext.price:.2f}")
        
        # Добавляем максимумы (🟢 зелёные точки)
        if max_x:
            fig.add_trace(go.Scatter(
                x=max_x, y=max_y,
                mode='markers',
                marker=dict(symbol='circle', size=8, color='#82C7B7', 
                           line=dict(color='darkgreen', width=0.5)),
                name='Local Maxima (V-shaped)',
                text=max_prices,
                hovertemplate='MAX: %{text}₽<extra></extra>'
            ))
        
        # Добавляем минимумы (🔴 красные точки)
        if min_x:
            fig.add_trace(go.Scatter(
                x=min_x, y=min_y,
                mode='markers',
                marker=dict(symbol='circle', size=8, color='#D5505C', 
                           line=dict(color='darkred', width=0.5)),
                name='Local Minima (V-shaped)',
                text=min_prices,
                hovertemplate='MIN: %{text}₽<extra></extra>'
            ))
        
        # ========================================================
        # 🎯 ОТРИСОВКА УРОВНЕЙ (SUPPORT/RESISTANCE)
        # ========================================================

        # Сортируем уровни по приоритету (выше = виднее)
        levels_sorted = sorted(levels, key=lambda x: x.priority, reverse=True)

        for level in levels_sorted:
            # Рисуем отрезок, не бесконечную линию!
            x0 = level.start_index if level.start_index is not None else 0
            x1 = level.end_index if level.end_index is not None else len(df) - 1
            
            fig.add_shape(
                type="line",
                x0=x0, x1=x1,  # ← ОТРЕЗОК!
                y0=level.price, y1=level.price,
                line=dict(
                    color=level.color,
                    width=level.line_width,
                    dash=level.line_dash,
                ),
                opacity=level.opacity,
                layer="below",
            )
            
            # Добавляем аннотацию (label) справа
            # fig.add_annotation(
            #     x=len(df) - 1,
            #     y=level.price,
            #     text=f"<b>{level.category}</b><br>{level.price:.2f}",
            #     showarrow=False,
            #     xanchor="left",
            #     font=dict(size=9, color=level.color),
            #     bgcolor="rgba(255,255,255,0.7)",
            #     bordercolor=level.color,
            #     borderwidth=1,
            #     borderpad=4,
            # )

        logger.info(f"✅ Drawn {len(levels_sorted)} levels on chart")
    
    


    # Обновляем layout
    fig.update_layout(
        title=f"{selected_figi} - {selected_timeframe}",
        yaxis_title="Price (₽)",
        yaxis2=dict(title="", overlaying="y", side="right", showgrid=False, showticklabels=False),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        hovermode="x unified",
        height=600,
        margin=dict(l=50, r=100, t=50, b=50),
        xaxis=dict(
            range=[max(0, len(df) - 500), len(df)],
            ticktext=[c.strftime('%m-%d %H:%M') for c in df["time"]],
            tickvals=list(range(len(df))),
            showticklabels=False,
            showline=False,
            showgrid=False,
        )
    )
    
    # Информация о свечах и паттернах
    patterns_count = len(patterns) if patterns else 0
    extremums_count = len(extremums) if extremums else 0
    
    info_text = f"""
    
    Total candles: {len(candles)} | Patterns found: {patterns_count} | Extremums found: {extremums_count} |
    Period: {candles[0].open_time.strftime('%Y-%m-%d')} → {candles[-1].open_time.strftime('%Y-%m-%d')}
    """
    # Текст по индикаторам
    if last_indicators_values:
        indicators_text_lines = []
        for k, v in last_indicators_values.items():
            indicators_text_lines.append(f"{k}: {v:.2f}")
        indicators_text = " | ".join(indicators_text_lines)
    else:
        indicators_text = "No indicators"

    info_block = html.Div([
        html.P(info_text, style={"color": "#666", "fontSize": "14px"}),
        html.P(f"Indicators: {indicators_text}", style={"color": "#333", "fontSize": "14px", "fontWeight": "bold"}),
    ])
    
    # Получаем тикер для таблицы
    ticker_info = broker.get_instrument_by_figi(selected_figi)
    ticker = ticker_info["ticker"] if ticker_info else selected_figi
    
    # 📊 ТАБЛИЦА
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
    } for c in candles])
    
    table = html.Table([
        html.Thead(
            html.Tr([
                html.Th("FIGI", style={"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Ticker", style={"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Timeframe", style={"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Open Time", style={"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Open (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("High (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Low (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Close (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("Volume", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            ])
        ),
        html.Tbody([
            html.Tr([
                html.Td(row["figi"], style={"padding": "10px", "borderBottom": "1px solid #eee"}),
                html.Td(row["ticker"], style={"padding": "10px", "borderBottom": "1px solid #eee"}),
                html.Td(row["timeframe"], style={"padding": "10px", "borderBottom": "1px solid #eee"}),
                html.Td(row["open_time"], style={"padding": "10px", "borderBottom": "1px solid #eee", "fontFamily": "monospace", "fontSize": "13px"}),
                html.Td(row["open"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(row["high"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(row["low"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(row["close"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(row["volume"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
            ])
            for idx, row in df_table.iterrows()
        ])
    ], style={
        "width": "100%",
        "borderCollapse": "collapse",
        "fontSize": "14px",
        "backgroundColor": "white"
    })
    
    return fig, info_block, table #html.P(info_text, style={"color": "#666", "fontSize": "14px"}), table

# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Dash server at http://127.0.0.1:8050")
    app.run(debug=True)