"""
Layout для Dash Dashboard.

Собирает всю структуру страницы из компонентов.
Вынесено из app_dashboard.py для читаемости.
"""

from datetime import datetime, timedelta

from dash import dcc, html

from src.strategies.strategy_registry import get_available_strategies
from dashboard.data import get_available_combinations, get_ticker


def create_layout() -> html.Div:
    """Создать полный layout dashboard."""

    strategy_options = [
        {"label": name, "value": code}
        for code, name in get_available_strategies().items()
    ]

    return html.Div([
        html.Div([
            html.H1("📈 Trading Dashboard", style={"textAlign": "center", "marginBottom": 20}),

            # ════════════════════════════════════════════════════
            # ЗОНА 1: СВОДНАЯ ТАБЛИЦА
            # ════════════════════════════════════════════════════
            html.Div([
                html.H2("📊 Strategy Performance Summary", style={
                    "marginTop": "40px", "marginBottom": "15px",
                }),

                html.Div([
                    html.Div([
                        html.Label("Timeframe:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="table-timeframe-selector",
                            options=[
                                {"label": "30 minutes", "value": "30m"},
                                {"label": "1 hour", "value": "1h"},
                                {"label": "4 hours", "value": "4h"},
                                {"label": "1 day", "value": "1d"},
                            ],
                            value="4h",
                            clearable=False,
                            style={"width": "200px"},
                        ),
                    ], style={"display": "inline-block", "marginRight": "30px"}),

                    html.Div([
                        html.Label("Strategy:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="table-strategy-selector",
                            options=strategy_options,
                            value="candle_patterns_2",
                            clearable=False,
                            style={"width": "250px"},
                        ),
                    ], style={"display": "inline-block"}),

                    html.Div([
                        html.Label("📅 Date Range:", style={"fontWeight": "bold", "marginLeft": "20px"}),
                        dcc.DatePickerRange(
                            id="table-date-range-selector",
                            start_date=datetime(2022, 1, 1),
                            end_date=datetime.now(),
                            display_format='YYYY-MM-DD',
                            style={"marginLeft": "10px"},
                        ),
                    ], style={"display": "inline-block", "marginLeft": "30px"}),

                    html.Div([
                        dcc.Checklist(
                            id="hide-unprofitable-filter",
                            options=[{"label": " Только прибыльные инструменты (Total PnL ≥ 0)", "value": "hide"}],
                            value=[],
                            style={"marginLeft": "30px", "fontWeight": "500"},
                            inputStyle={"marginRight": "6px"},
                        ),
                    ], style={"display": "inline-block", "marginLeft": "30px", "verticalAlign": "middle"}),
                ], style={"marginBottom": "20px"}),

                # Сводные карточки
                html.Div(id="strategy-summary-cards", style={"marginBottom": "20px"}),

                # Помесячные графики
                html.Div(id="monthly-charts"),

                # Карточки метрик стратегии
                html.Div(id="strategy-metrics-cards", style={"marginBottom": "20px"}),

                # Сводная таблица
                html.Div(id="strategy-summary-table", style={
                    "overflowX": "auto", "marginTop": "10px",
                    "border": "1px solid #ddd", "borderRadius": "5px",
                    "backgroundColor": "#fff",
                }),

                # ════════════════════════════════════════════════════
                # ЗОНА 2: ТАБЛИЦА СДЕЛОК
                # ════════════════════════════════════════════════════
                html.Div([
                    html.H2("📋 Trades Detail", style={
                        "marginTop": "50px", "marginBottom": "15px",
                    }),

                html.Div([
                    html.Div([
                        html.Label("Strategy:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="trades-strategy-filter",
                            options=strategy_options,
                            value="candle_patterns_2",
                            clearable=False,
                            style={"width": "250px"},
                        ),
                    ], style={"display": "inline-block", "marginRight": "30px"}),

                    html.Div([
                        html.Label("Ticker:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="trades-ticker-filter",
                            options=[],
                            multi=True,
                            placeholder="All tickers",
                            style={"width": "250px"},
                        ),
                    ], style={"display": "inline-block", "marginRight": "30px"}),

                    html.Div([
                        html.Label("Timeframe:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="trades-timeframe-filter",
                            options=[
                                {"label": "30 minutes", "value": "30m"},
                                {"label": "1 hour", "value": "1h"},
                                {"label": "4 hours", "value": "4h"},
                                {"label": "1 day", "value": "1d"},
                            ],
                            value="4h",
                            clearable=False,
                            style={"width": "200px"},
                        ),
                    ], style={"display": "inline-block", "marginRight": "30px"}),

                    html.Div([
                        html.Label("📅 Period:", style={"fontWeight": "bold"}),
                        dcc.DatePickerRange(
                            id="trades-date-range-filter",
                            start_date=datetime.now() - timedelta(days=365),
                            end_date=datetime.now(),
                            display_format='YYYY-MM-DD',
                            style={"marginLeft": "10px"},
                        ),
                    ], style={"display": "inline-block", "marginLeft": "30px"}),
                ], style={"marginBottom": "20px"}),

                    # График сделок
                    html.Div(id="trades-candlestick-chart", style={
                        "marginBottom": "30px",
                        "border": "1px solid #ddd", "borderRadius": "5px",
                        "backgroundColor": "#fff", "padding": "10px",
                    }),

                    html.Div(id="trades-detail-table", style={
                        "overflowX": "auto",
                        "border": "1px solid #ddd", "borderRadius": "5px",
                        "backgroundColor": "#fff", "padding": "10px",
                    }),
                ]),
            ], style={
                "marginTop": "40px", "padding": "20px",
                "backgroundColor": "#f3e5f5", "borderRadius": "5px",
                "border": "2px solid #9c27b0",
            }),

        ], style={"padding": "20px", "maxWidth": "1400px", "margin": "0 auto"}),
    ])
