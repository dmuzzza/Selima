"""
Callbacks для Dash Dashboard.

Регистрирует все колбэки. Каждый колбэк — тонкий: делегирует
бизнес-логику в compute.py, построение графика в chart_builder.py.
"""

import logging

from dash import Input, Output, html, dcc
import dash
from dashboard.compute import (
    run_strategy_for_all_tickers,
    run_strategy_for_ticker,
    compute_aggregate_stats,
    compute_averaged_metrics,
    compute_monthly_breakdown,
    compute_trades_list,
    filter_unprofitable,
)
from dashboard.data import get_figi_for_timeframe, get_ticker

from dashboard.ui.components import (
    build_summary_cards,
    build_metrics_cards,
    build_summary_table,
    build_trades_table,
)
from dashboard.ui.chart_builder import (
    build_monthly_charts,
    build_candlestick_figure,
    add_indicators_to_figure,
    add_trades_to_figure,
)

logger = logging.getLogger(__name__)


def register_callbacks(app: dash.Dash, broker, history, indicators_builder) -> None:

    # ═══════════════════════════════════════════════════════════
    # CALLBACK 1: Сводная таблица + карточки
    # ═══════════════════════════════════════════════════════════
    @app.callback(
        [Output("strategy-summary-cards", "children"),
         Output("strategy-summary-table", "children")],
        [Input("table-timeframe-selector", "value"),
         Input("table-strategy-selector", "value"),
         Input("table-date-range-selector", "start_date"),
         Input("table-date-range-selector", "end_date"),
         Input("hide-unprofitable-filter", "value")],
    )
    def update_summary_table(selected_timeframe: str, selected_strategy: str,
                             start_date: str = None, end_date: str = None,
                             hide_unprofitable_value: list[str] = None):
        if selected_timeframe is None or selected_strategy is None:
            empty = html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})
            return empty, empty

        logger.info(f"Updating summary table: {selected_strategy} on {selected_timeframe}")

        hide_unprofitable = ("hide" in (hide_unprofitable_value or []))

        results = run_strategy_for_all_tickers(selected_timeframe, selected_strategy, start_date, end_date)
        results = filter_unprofitable(results, hide_unprofitable)

        summary_list = [r["summary"] for r in results]
        total_stats = compute_aggregate_stats(results, selected_strategy, selected_timeframe)
        total_stats["instruments"] = len(results)

        cards = build_summary_cards(total_stats)
        table = build_summary_table(summary_list, total_stats)
        return cards, table

    # ═══════════════════════════════════════════════════════════
    # CALLBACK 3: Карточки метрик стратегии
    # ═══════════════════════════════════════════════════════════
    @app.callback(
        Output("strategy-metrics-cards", "children"),
        [Input("table-timeframe-selector", "value"),
         Input("table-strategy-selector", "value"),
         Input("table-date-range-selector", "start_date"),
         Input("table-date-range-selector", "end_date"),
         Input("hide-unprofitable-filter", "value")],
    )
    def update_metrics_cards(selected_timeframe: str, selected_strategy: str,
                             start_date: str = None, end_date: str = None,
                             hide_unprofitable_value: list[str] = None):
        if selected_timeframe is None or selected_strategy is None:
            return html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})

        logger.info(f"Updating metrics cards: {selected_strategy} on {selected_timeframe}")

        hide_unprofitable = ("hide" in (hide_unprofitable_value or []))

        results = run_strategy_for_all_tickers(selected_timeframe, selected_strategy, start_date, end_date)
        results = filter_unprofitable(results, hide_unprofitable)

        metrics = compute_averaged_metrics(results)
        return build_metrics_cards(metrics)

    # ═══════════════════════════════════════════════════════════
    # CALLBACK 4: Помесячные графики
    # ═══════════════════════════════════════════════════════════
    @app.callback(
        Output("monthly-charts", "children"),
        [Input("table-timeframe-selector", "value"),
         Input("table-strategy-selector", "value"),
         Input("table-date-range-selector", "start_date"),
         Input("table-date-range-selector", "end_date"),
         Input("hide-unprofitable-filter", "value")],
    )
    def update_monthly_charts(selected_timeframe: str, selected_strategy: str,
                              start_date: str = None, end_date: str = None,
                              hide_unprofitable_value: list[str] = None):
        if selected_timeframe is None or selected_strategy is None:
            return html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})

        logger.info(f"Updating monthly charts: {selected_strategy} on {selected_timeframe}")

        hide_unprofitable = ("hide" in (hide_unprofitable_value or []))

        results = run_strategy_for_all_tickers(selected_timeframe, selected_strategy, start_date, end_date)
        results = filter_unprofitable(results, hide_unprofitable)

        monthly = compute_monthly_breakdown(results, selected_strategy, selected_timeframe)
        return build_monthly_charts(monthly)

    # ═══════════════════════════════════════════════════════════
    # CALLBACK 5: Таблица сделок с фильтрами + график
    # ═══════════════════════════════════════════════════════════
    @app.callback(
        [Output("trades-detail-table", "children"),
         Output("trades-ticker-filter", "options"),
         Output("trades-candlestick-chart", "children")],
        [Input("trades-ticker-filter", "value"),
         Input("trades-timeframe-filter", "value"),
         Input("trades-strategy-filter", "value"),
         Input("trades-date-range-filter", "start_date"),
         Input("trades-date-range-filter", "end_date")],
    )
    def update_trades_table(
        selected_tickers: list[str],
        selected_timeframe: str,
        selected_strategy: str,
        start_date: str,
        end_date: str,
    ):
        if selected_timeframe is None or selected_strategy is None:
            empty = html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})
            return empty, [], empty

        logger.info(f"Updating trades table: {selected_strategy} on {selected_timeframe}")

        results = run_strategy_for_all_tickers(selected_timeframe, selected_strategy, start_date, end_date)

        all_trades = compute_trades_list(results, selected_timeframe)

        if selected_tickers:
            all_trades = [t for t in all_trades if t["ticker"] in selected_tickers]

        # Все доступные тикеры на этом ТФ (не только имеющие сделки) — для фильтра и графика
        available_figis = get_figi_for_timeframe(selected_timeframe)
        all_tickers = sorted({get_ticker(f) for f in available_figis})
        ticker_options = [{"label": t, "value": t} for t in all_tickers]

        # График для первого выбранного тикера (или первого из доступных на ТФ)
        chart_ticker = (
            selected_tickers[0] if selected_tickers
            else (all_tickers[0] if all_tickers else None)
        )
        chart_component = _build_trades_chart_component(
            chart_ticker, selected_timeframe, selected_strategy, start_date, end_date
        )

        return build_trades_table(all_trades, selected_tickers or []), ticker_options, chart_component


def _build_trades_chart_component(
    selected_chart_ticker: str,
    selected_timeframe: str,
    selected_strategy: str,
    start_date: str,
    end_date: str,
) -> html.Div:
    """Построить свечной график для выбранного тикера."""
    if not selected_chart_ticker or selected_timeframe is None or selected_strategy is None:
        return html.Div("Select ticker to display chart", style={"padding": "20px", "color": "#999"})

    logger.info(f"Updating trades chart: {selected_strategy} on {selected_timeframe} for {selected_chart_ticker}")

    figi = None
    for f in get_figi_for_timeframe(selected_timeframe):
        if get_ticker(f) == selected_chart_ticker:
            figi = f
            break

    if figi is None:
        return html.Div(f"No FIGI found for {selected_chart_ticker} on {selected_timeframe}",
                        style={"padding": "20px", "color": "#c62828"})

    result = run_strategy_for_ticker(figi, selected_timeframe, selected_strategy, start_date, end_date)
    if result is None:
        return html.Div(f"No data for {selected_chart_ticker}", style={"padding": "20px", "color": "#999"})

    candles = result.get("candles", [])
    if not candles:
        return html.Div(f"No candles for {selected_chart_ticker}", style={"padding": "20px", "color": "#999"})

    # Последние 1000 свечей
    candles = candles[-1000:]
    index_offset = len(result.get("candles", candles)) - len(candles)

    # Корректируем индексы сделок под срез
    trades = result.get("trades", [])
    for trade in trades:
        if trade.entry_index is not None:
            trade.entry_index -= index_offset
        if trade.exit_index is not None:
            trade.exit_index -= index_offset

    fig, df = build_candlestick_figure(
        candles, figi, selected_timeframe,
        show_macd=(selected_strategy == "macd_plus"),
        show_mfi_bb=(selected_strategy == "mfi_bb"),
    )
    strategy_instance = result.get("strategy")
    add_indicators_to_figure(fig, df, candles, selected_strategy, strategy=strategy_instance)
    add_trades_to_figure(fig, trades)

    return dcc.Graph(figure=fig, style={"height": "850px"})


