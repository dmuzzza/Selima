"""
Переиспользуемые UI-компоненты для Dashboard.

Каждая функция возвращает Dash HTML-элемент.
Не содержат бизнес-логики — только отрисовка.
"""

from typing import List, Dict, Any

from dash import html


# ═══════════════════════════════════════════════════════════════
# КАРТОЧКА МЕТРИКИ
# ═══════════════════════════════════════════════════════════════

def create_metric_card(
    title: str,
    value: float,
    suffix: str = "",
    good_threshold: float = None,
    higher_is_better: bool = True,
) -> html.Div:
    """
    Карточка с метрикой и цветовой индикацией.

    Args:
        title: Заголовок метрики
        value: Числовое значение
        suffix: Суффикс (%, ₽, d, ...)
        good_threshold: Порог для зелёного/красного
        higher_is_better: True — чем выше, тем лучше
    """
    if good_threshold is not None:
        if higher_is_better:
            color = "#2e7d32" if value >= good_threshold else "#c62828"
        else:
            color = "#2e7d32" if value <= good_threshold else "#c62828"
    else:
        color = "#1976d2"

    # Для Max Drawdown не ставим знак +
    if "Drawdown" in title:
        value_str = f"{value:.2f}{suffix}"
    else:
        value_str = f"{value:+.3f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"

    return html.Div([
        html.Div(title, style={
            "fontSize": "14px", "color": "#666", "marginBottom": "8px", "fontWeight": "500",
        }),
        html.Div(value_str, style={
            "fontSize": "28px", "fontWeight": "bold", "color": color, "fontFamily": "monospace",
        }),
    ], style={
        "padding": "20px", "backgroundColor": "#fff", "borderRadius": "8px",
        "boxShadow": "0 2px 4px rgba(0,0,0,0.1)", "textAlign": "center",
        "minWidth": "180px", "flex": "1",
    })


def create_stat_card(
    title: str,
    value,
    suffix: str = "",
    color: str = "#1976d2",
) -> html.Div:
    """
    Простая карточка для статистики (без порогов).
    Используется в aggregate-карточках.
    """
    return html.Div([
        html.Div(title, style={"fontSize": "13px", "color": "#666", "marginBottom": "6px"}),
        html.Div(f"{value}{suffix}", style={
            "fontSize": "22px", "fontWeight": "bold", "color": color, "fontFamily": "monospace",
        }),
    ], style={
        "padding": "16px", "backgroundColor": "#fff", "borderRadius": "8px",
        "boxShadow": "0 2px 4px rgba(0,0,0,0.1)", "textAlign": "center",
        "minWidth": "150px", "flex": "1",
    })


# ═══════════════════════════════════════════════════════════════
# КАРТОЧКИ АГРЕГИРОВАННЫХ МЕТРИК (Strategy Summary Cards)
# ═══════════════════════════════════════════════════════════════

def build_summary_cards(total_stats: Dict[str, Any]) -> html.Div:
    """Три ряда карточек с агрегированными метриками."""
    row1 = html.Div([
        create_stat_card("Total Trades", total_stats.get("total_trades", 0)),
        create_stat_card("Closed Trades", total_stats.get("closed_trades", 0)),
        create_stat_card("Winning", total_stats.get("winning", 0), "", "#2e7d32"),
        create_stat_card("Losing", total_stats.get("losing", 0), "", "#c62828"),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "14px"})

    row2 = html.Div([
        create_stat_card("Win Rate", f"{total_stats.get('win_rate', 0):.1f}", "%"),
        create_stat_card("Avg Win", f"{total_stats.get('avg_win', 0):+.0f}", "₽", "#2e7d32"),
        create_stat_card("Avg Loss", f"{total_stats.get('avg_loss', 0):+.0f}", "₽", "#c62828"),
        create_stat_card("Avg Hold", f"{total_stats.get('avg_hold_days', 0):.1f}", "d"),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "14px"})

    total_pnl = total_stats.get("total_pnl", 0)
    total_pnl_pct = total_stats.get("total_pnl_percent", 0)
    final_balance = total_stats.get("final_balance", 0)
    instruments = total_stats.get("instruments", 0)

    row3 = html.Div([
        create_stat_card("Total PnL", f"{total_pnl:+,.0f}", "₽",
                         "#2e7d32" if total_pnl > 0 else "#c62828"),
        create_stat_card("Total PnL %", f"{total_pnl_pct:+.2f}", "%",
                         "#2e7d32" if total_pnl_pct > 0 else "#c62828"),
        create_stat_card("Final Balance", f"{final_balance:,.0f}", "₽"),
        create_stat_card("Instruments", instruments),
    ], style={"display": "flex", "gap": "14px"})

    return html.Div([
        html.H4("📋 Aggregate Metrics (All Instruments)", style={
            "textAlign": "center", "color": "#555", "marginBottom": "16px",
        }),
        row1, row2, row3,
    ], style={
        "backgroundColor": "#f9f4ff", "borderRadius": "8px", "padding": "20px",
        "border": "1px solid #d1c4e9", "marginBottom": "24px",
    })


# ═══════════════════════════════════════════════════════════════
# КАРТОЧКИ СТРАТЕГИЧЕСКИХ МЕТРИК (Expectancy, Profit Factor, ...)
# ═══════════════════════════════════════════════════════════════

def build_metrics_cards(metrics: Dict[str, float]) -> html.Div:
    """Карточки: Expectancy, Profit Factor, Payoff Ratio, Max Drawdown."""

    z = metrics.get("z_score", 0)
    z_color = "#2e7d32" if abs(z) < 1.96 else "#c62828"
    z_label = " |Z|<1.96 ✓" if abs(z) < 1.96 else " non-random ⚠"
    cards = html.Div([
        create_metric_card("📈 Expectancy (%)", metrics.get("expectancy", 0), "%",
                           good_threshold=0.0, higher_is_better=True),
        create_metric_card("💰 Profit Factor", metrics.get("profit_factor", 0), "",
                           good_threshold=1.0, higher_is_better=True),
        create_metric_card("⚖️ Payoff Ratio", metrics.get("payoff_ratio", 0), "",
                           good_threshold=1.0, higher_is_better=True),
        create_metric_card("📉 Max Drawdown (%)", metrics.get("max_drawdown", 0), "%",
                           good_threshold=10.0, higher_is_better=False),
        create_metric_card("📊 Sharpe Ratio",       # ← новая
                           metrics.get("sharpe_ratio", 0), "",
                           good_threshold=1.0, higher_is_better=True),
        # Z-Score — своя карточка:
        html.Div([
            html.Div("🎲 Z-Score", style={"fontSize": "14px", "color": "#666", "marginBottom": "8px", "fontWeight": "500"}),
            html.Div(f"{z:+.2f}", style={"fontSize": "28px", "fontWeight": "bold", "color": z_color, "fontFamily": "monospace"}),
            html.Div(z_label, style={"fontSize": "11px", "color": "#999", "marginTop": "4px"}),
        ], style={
            "padding": "20px", "backgroundColor": "#fff", "borderRadius": "8px",
            "boxShadow": "0 2px 4px rgba(0,0,0,0.1)", "textAlign": "center",
            "minWidth": "180px", "flex": "1",
        }),
    ], style={
        "display": "flex", "flexWrap": "wrap", "gap": "20px",
        "justifyContent": "center", "padding": "20px",
    })

    return html.Div([
        html.H3("📊 Strategy Metrics", style={
            "textAlign": "center", "marginBottom": "20px", "color": "#333",
        }),
        cards,
    ], style={
        "backgroundColor": "#e8f5e9", "borderRadius": "8px",
        "border": "2px solid #4caf50", "marginBottom": "30px",
    })


# ═══════════════════════════════════════════════════════════════
# СВОДНАЯ ТАБЛИЦА
# ═══════════════════════════════════════════════════════════════

def build_summary_table(
    summary_data: List[Dict[str, Any]],
    total_stats: Dict[str, Any],
) -> html.Table:
    """
    HTML-таблица с результатами по каждому тикеру + TOTAL строка.

    Args:
        summary_data: Список словарей summary (из compute.run_strategy_for_all_tickers)
        total_stats: Агрегированная статистика (из compute.compute_aggregate_stats)
    """
    if not summary_data:
        return html.Div("No data available", style={"padding": "20px", "color": "#999"})

    # Заголовок
    table_header = html.Thead(html.Tr([
        html.Th("Ticker", style=_th_left()),
        html.Th("Total Trades", style=_th_center()),
        html.Th("Closed", style=_th_center()),
        html.Th("Winning", style=_th_center()),
        html.Th("Losing", style=_th_center()),
        html.Th("Win Rate (%)", style=_th_center()),
        html.Th("Avg Win (₽)", style=_th_center()),
        html.Th("Avg Loss (₽)", style=_th_center()),
        html.Th("Avg TP/SL (%)", style=_th_center()),
        html.Th("Avg Hold (days)", style=_th_center()),
        html.Th("Med Long Up (%)", style=_th_center()),
        html.Th("Med Short Down (%)", style=_th_center()),
        html.Th("Total PnL (₽)", style=_th_right()),
        html.Th("Total PnL (%)", style=_th_right()),
        html.Th("Final Balance (₽)", style=_th_right()),
    ]))

    # Строки по тикерам
    table_rows = []
    for row in summary_data:
        row_color = (
            "#e8f5e9" if row["total_pnl"] > 0
            else "#ffebee" if row["total_pnl"] < 0
            else "#fff"
        )
        table_rows.append(html.Tr([
            html.Td(row["ticker"], style=_td_left(bold=True)),
            html.Td(row["total_trades"], style=_td_center()),
            html.Td(row["closed_trades"], style=_td_center()),
            html.Td(row["winning"], style=_td_center(color="green")),
            html.Td(row["losing"], style=_td_center(color="red")),
            html.Td(f"{row['win_rate']:.1f}%", style=_td_center(mono=True)),
            html.Td(f"{row['avg_win']:.2f}₽", style=_td_center(color="green", mono=True)),
            html.Td(f"{row['avg_loss']:.2f}₽", style=_td_center(color="red", mono=True)),
            html.Td(f"{row['avg_mfe']:.1f}% / {row['avg_mae']:.1f}%", style=_td_center(mono=True)),
            html.Td(f"{row['avg_hold_days']:.1f}d", style=_td_center(mono=True)),
            html.Td(f"{row.get('median_long_up_move', 0):.2f}%", style=_td_center(mono=True, color="#2e7d32")),
            html.Td(f"{row.get('median_short_down_move', 0):.2f}%", style=_td_center(mono=True, color="#c62828")),
            html.Td(
                f"{row['total_pnl']:+.2f}₽",
                style=_td_right(mono=True, bold=True, color="green" if row["total_pnl"] > 0 else "red"),
            ),
            html.Td(f"{row['total_pnl_percent']:+.2f}%", style=_td_right(mono=True)),
            html.Td(f"{row['final_balance']:,.0f}₽", style=_td_right(mono=True)),
        ], style={"backgroundColor": row_color}))

    # TOTAL строка
    total_pnl = total_stats.get("total_pnl", 0)
    total_row_color = (
        "#d4edda" if total_pnl > 0 else "#f8d7da" if total_pnl < 0 else "#e9ecef"
    )
    total_bg = {"backgroundColor": total_row_color}

    table_rows.append(html.Tr([
        html.Td("📊 TOTAL", style={**_td_left(bold=True), "fontSize": "15px", "borderTop": "3px solid #333", **_p14(), **total_bg}),
        html.Td(total_stats.get("total_trades", 0), style={**_td_center(), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(total_stats.get("closed_trades", 0), style={**_td_center(), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(total_stats.get("winning", 0), style={**_td_center(color="green"), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(total_stats.get("losing", 0), style={**_td_center(color="red"), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('win_rate', 0):.1f}%", style={**_td_center(mono=True), "fontSize": "15px", **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('avg_win', 0):.2f}₽", style={**_td_center(color="green", mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('avg_loss', 0):.2f}₽", style={**_td_center(color="red", mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('avg_mfe', 0):.1f}% / {total_stats.get('avg_mae', 0):.1f}%", style={**_td_center(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('avg_hold_days', 0):.1f}d", style={**_td_center(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('median_long_up_move', 0):.2f}%", style={**_td_center(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('median_short_down_move', 0):.2f}%", style={**_td_center(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(
            f"{total_pnl:+.2f}₽",
            style={
                **_td_right(mono=True, bold=True), "fontSize": "16px",
                "color": "green" if total_pnl > 0 else "red",
                **_p14(), "borderTop": "3px solid #333", **total_bg,
            },
        ),
        html.Td(f"{total_stats.get('total_pnl_percent', 0):+.2f}%", style={**_td_right(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
        html.Td(f"{total_stats.get('final_balance', 0):,.0f}₽", style={**_td_right(mono=True), **_p14(), "borderTop": "3px solid #333", **total_bg}),
    ]))

    table_body = html.Tbody(table_rows)

    return html.Table(
        [table_header, table_body],
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"},
    )


# ═══════════════════════════════════════════════════════════════
# СТИЛЕВЫЕ ХЕЛПЕРЫ
# ═══════════════════════════════════════════════════════════════

def _p14():
    return {"padding": "14px"}


def _th_left():
    return {"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}


def _th_center():
    return {"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}


def _th_right():
    return {"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}


def _td_left(bold=False, mono=False):
    s = {"padding": "10px", "borderBottom": "1px solid #eee"}
    if bold:
        s["fontWeight"] = "bold"
    if mono:
        s["fontFamily"] = "monospace"
    return s


def _td_center(bold=False, mono=False, color=None):
    s = {"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center"}
    if bold:
        s["fontWeight"] = "bold"
    if mono:
        s["fontFamily"] = "monospace"
    if color:
        s["color"] = color
    return s


def _td_right(bold=False, mono=False, color=None):
    s = {"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right"}
    if bold:
        s["fontWeight"] = "bold"
    if mono:
        s["fontFamily"] = "monospace"
    if color:
        s["color"] = color
    return s


# ═══════════════════════════════════════════════════════════════
# ТАБЛИЦА СДЕЛОК
# ═══════════════════════════════════════════════════════════════

def build_trades_table(
    trades_data: List[Dict[str, Any]],
    selected_tickers: List[str],
) -> html.Div:
    """
    HTML-таблица со списком всех сделок.

    Args:
        trades_data: Список словарей с полями сделки
        selected_tickers: Список выбранных тикеров для отображения в заголовке
    """
    if not trades_data:
        return html.Div("No trades match selected filters", style={"padding": "20px", "color": "#999"})

    header = html.Thead(html.Tr([
        html.Th("Signal Date", style=_th_left()),
        html.Th("Ticker", style=_th_left()),
        html.Th("Timeframe", style=_th_center()),
        html.Th("Direction", style=_th_center()),
        html.Th("Pattern", style=_th_left()),
        html.Th("Entry Price", style=_th_right()),
        html.Th("Exit Price", style=_th_right()),
        html.Th("Days Held", style=_th_center()),
        html.Th("Result", style=_th_right()),
    ]))

    rows = []
    for trade in trades_data:
        pnl = trade.get("pnl_net", 0.0)
        row_color = (
            "#e8f5e9" if pnl > 0
            else "#ffebee" if pnl < 0
            else "#fff"
        )
        rows.append(html.Tr([
            html.Td(trade.get("signal_date", "-"), style=_td_left(mono=True)),
            html.Td(trade.get("ticker", "-"), style=_td_left(bold=True)),
            html.Td(trade.get("timeframe", "-"), style=_td_center(mono=True)),
            html.Td(trade.get("direction", "-"), style=_td_center()),
            html.Td(trade.get("pattern_name", "-"), style=_td_left()),
            html.Td(f"{trade.get('entry_price', 0):.2f}", style=_td_right(mono=True)),
            html.Td(
                f"{trade.get('exit_price', 0):.2f}" if trade.get("exit_price") is not None else "OPEN",
                style=_td_right(mono=True),
            ),
            html.Td(f"{trade.get('days_held', 0)}d", style=_td_center(mono=True)),
            html.Td(
                f"{pnl:+.2f}₽ ({trade.get('pnl_percent', 0):+.2f}%)",
                style=_td_right(
                    mono=True,
                    bold=True,
                    color="green" if pnl > 0 else "red" if pnl < 0 else None,
                ),
            ),
        ], style={"backgroundColor": row_color}))

    title_suffix = ""
    if selected_tickers:
        title_suffix = f" — {', '.join(selected_tickers)}"

    return html.Div([
        html.H4(f"📋 Trades{title_suffix}", style={
            "textAlign": "center", "color": "#555", "marginBottom": "16px",
        }),
        html.Table(
            [header, html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"},
        ),
    ])
