import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import List  # ✅
from datetime import datetime as dt

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go
import pandas as pd

# ✅ ИМПОРТ КЛАССА TRADE
from src.strategies.base_strategy import Trade

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.market_data.timeframe import Timeframe
# from src.strategies.candle_pattern_strategy import CandlePatternStrategy
# from src.strategies.levels_detector import LevelsDetector
# from src.strategies.levels_detector_debug import LevelsDetector
from src.strategies.levels_detector_v5 import LevelsDetector

from src.strategies.levels_builder import LevelsBuilder
from src.strategies.indicators_builder import IndicatorsBuilder
from src.strategies.indicators_config import get_indicator_config

from src.strategies.levels_config import get_clustering_tolerance
from src.strategies.extremums_config import get_extremums_config

from src.strategies.strategy_factory import StrategyFactory
from src.strategies.strategy_registry import get_available_strategies

# ============ НОВОЕ ДЛЯ SMA CROSSOVER STRATEGY ============
# from src.strategies.sma_crossover_strategy import SMACrossoverStrategy
# from src.strategies.sma_crossover_config import get_sma_period
# ========================================================

# Настройка
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashApp")
load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")

# Инициализируем компоненты
logger.info("Initializing components...")
broker = BrokerFactory.create("sdk", token)
history = HistoryManager(broker)
# history = HistoryManager(broker, db_path="marketdata.db")
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
# strategy = CandlePatternStrategy()
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

# ============================================================================
# 📊 ФУНКЦИЯ ДЛЯ ГЕНЕРАЦИИ СВОДНОЙ ТАБЛИЦЫ
# ============================================================================

def _calculate_avg_mfe(trades: List[Trade]) -> float:
    """Средний MFE (потенциальный TP) по всем сделкам."""
    valid_mfe = [t.mfe_percent for t in trades if t.mfe_percent is not None]
    return sum(valid_mfe) / len(valid_mfe) if valid_mfe else 0.0


def _calculate_avg_mae(trades: List[Trade]) -> float:
    """Средний MAE (потенциальный SL) по всем сделкам."""
    valid_mae = [t.mae_percent for t in trades if t.mae_percent is not None]
    return sum(valid_mae) / len(valid_mae) if valid_mae else 0.0


def generate_strategy_summary_table(
    selected_strategy: str, 
    selected_timeframe: str,
    start_date: str = None,
    end_date: str = None
) -> html.Table:
    """
    Генерирует сводную таблицу по всем тикерам для выбранной стратегии.
    
    Args:
        selected_strategy: Код стратегии (e.g., "sma_crossover")
        selected_timeframe: Таймфрейм (e.g., "1h", "4h")
    
    Returns:
        html.Table: HTML таблица с результатами
    """
    logger.info(f"Generating strategy summary for {selected_strategy} on {selected_timeframe}...")
    
    summary_data = []
    
    # Получаем все доступные FIGI для выбранного таймфрейма
    available_figis = []
    for figi, timeframes in available_combinations.items():
        if selected_timeframe in timeframes:
            available_figis.append(figi)
    
    logger.info(f"Found {len(available_figis)} instruments with {selected_timeframe} data")
    
    # Запускаем стратегию для каждого тикера
    for figi in available_figis:
        try:
            # Получаем название тикера
            ticker = figi_to_ticker.get(figi, figi)
            
            # Загружаем свечи
            timeframe_map = {
                "30m": Timeframe.M30,
                "1h": Timeframe.H1,
                "4h": Timeframe.H4,
                "1d": Timeframe.D1,
            }
            timeframe = timeframe_map.get(selected_timeframe, Timeframe.H1)
            candles = history.get_candles(figi, timeframe)
            
            # Фильтруем пустые свечи
            candles = [c for c in candles if c.volume and c.volume > 0]
            # Фильтр по датам
            if start_date:
                sd = dt.strptime(start_date.split('T')[0], '%Y-%m-%d')
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]
            if end_date:
                ed = dt.strptime(end_date.split('T')[0], '%Y-%m-%d')
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed + timedelta(days=1)]
            
            if not candles or len(candles) < 50:
                logger.warning(f"Skipping {ticker}: insufficient data")
                continue
            
            # Создаём и запускаем стратегию
            strategy = StrategyFactory.create(
                strategy_code=selected_strategy,
                timeframe=selected_timeframe,
                debug=False
            )
            
            result = strategy.run(candles)
            stats = result["stats"]
            
            # Собираем данные
            summary_data.append({
                "ticker": ticker,
                "figi": figi,
                "total_trades": stats.total_trades,
                "closed_trades": stats.closed_trades,
                "winning": stats.winning_trades,
                "losing": stats.losing_trades,
                "win_rate": stats.win_rate,
                "avg_win": stats.avg_win_abs,
                "avg_loss": stats.avg_loss_abs,
                "total_pnl": stats.total_pnl,
                "total_pnl_percent": stats.total_pnl_percent,
                "final_balance": stats.final_balance,
                # ✅ НОВЫЕ ПОЛЯ: AVG MFE/MAE
                "avg_mfe": _calculate_avg_mfe(result["trades"]),
                "avg_mae": _calculate_avg_mae(result["trades"]),
                "avg_hold_days": stats.avg_hold_days, 
            })
            
            logger.info(f"✓ {ticker}: {stats.closed_trades} trades, WR={stats.win_rate:.1f}%, PnL={stats.total_pnl:+.2f}₽")
            
        except Exception as e:
            logger.error(f"Error processing {figi}: {e}")
            continue
    
    # Сортируем по PnL (убыванию)
    summary_data.sort(key=lambda x: x["total_pnl"], reverse=True)
    
    # ═══════════════════════════════════════════════════════════════════════
    # ВЫЧИСЛЯЕМ ИТОГОВЫЕ ПОКАЗАТЕЛИ ПО ВСЕМ ТИКЕРАМ
    # ═══════════════════════════════════════════════════════════════════════
    
    if summary_data:
        # ═══════════════════════════════════════════════════════════════════
        # ПОЛУЧАЕМ INITIAL_BALANCE ИЗ КОНФИГА СТРАТЕГИИ
        # ═══════════════════════════════════════════════════════════════════
        
        # Создаём экземпляр стратегии, чтобы получить её конфиг
        try:
            temp_strategy = StrategyFactory.create(
                strategy_code=selected_strategy,
                timeframe=selected_timeframe,
                debug=False
            )
            INITIAL_BALANCE = temp_strategy.config.get("initial_balance", 5000.0)
            logger.info(f"Using initial_balance from strategy config: {INITIAL_BALANCE}₽")
        except Exception as e:
            logger.warning(f"Failed to get initial_balance from strategy, using default 5000₽: {e}")
            INITIAL_BALANCE = 5000.0
        
        # ═══════════════════════════════════════════════════════════════════
        # ПОРТФЕЛЬНАЯ ЛОГИКА: ОДИН СТАРТОВЫЙ БАЛАНС + СУММАРНЫЙ PnL
        # ═══════════════════════════════════════════════════════════════════
        
        total_stats = {
            "total_trades": sum(row["total_trades"] for row in summary_data),
            "closed_trades": sum(row["closed_trades"] for row in summary_data),
            "winning": sum(row["winning"] for row in summary_data),
            "losing": sum(row["losing"] for row in summary_data),
            "total_pnl": sum(row["total_pnl"] for row in summary_data),  # Суммарный PnL
            "initial_balance": INITIAL_BALANCE,                          # Из конфига стратегии
        }
        
        # Final Balance = Стартовый баланс + Суммарный PnL
        total_stats["final_balance"] = total_stats["initial_balance"] + total_stats["total_pnl"]
        
        # Средний Win Rate по всем тикерам
        total_stats["win_rate"] = (
            (total_stats["winning"] / total_stats["closed_trades"] * 100) 
            if total_stats["closed_trades"] > 0 else 0.0
        )
        
        # Средний Avg Win (взвешенный по количеству выигрышных сделок)
        total_avg_win = sum(row["avg_win"] * row["winning"] for row in summary_data)
        total_stats["avg_win"] = (
            total_avg_win / total_stats["winning"] 
            if total_stats["winning"] > 0 else 0.0
        )
        
        # Средний Avg Loss (взвешенный по количеству проигрышных сделок)
        total_avg_loss = sum(row["avg_loss"] * row["losing"] for row in summary_data)
        total_stats["avg_loss"] = (
            total_avg_loss / total_stats["losing"] 
            if total_stats["losing"] > 0 else 0.0
        )
        
        # Total PnL в процентах относительно стартового баланса
        total_stats["total_pnl_percent"] = (
            (total_stats["total_pnl"] / total_stats["initial_balance"] * 100)
            if total_stats["initial_balance"] > 0 else 0.0
        )
        total_stats["avg_hold_days"] = (
            sum(row["avg_hold_days"] for row in summary_data) / len(summary_data)
            if summary_data else 0.0
        )

    
    # ═══════════════════════════════════════════════════════════════════════
    # СТРОИМ HTML ТАБЛИЦУ
    # ═══════════════════════════════════════════════════════════════════════
    
    if not summary_data:
        return html.Div("No data available", style={"padding": "20px", "color": "#999"})
    
    # Заголовок таблицы
    table_header = html.Thead(
        html.Tr([
            html.Th("Ticker", style={"padding": "12px", "textAlign": "left", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Total Trades", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Closed", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Winning", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Losing", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Win Rate (%)", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Avg Win (₽)", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Avg Loss (₽)", style={"padding": "12px", "textAlign": "center", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            # ✅ НОВАЯ КОЛОНКА
            html.Th("Avg TP/SL (%)", style={
                "padding": "12px",
                "textAlign": "center",
                "borderBottom": "2px solid #ddd",
                "fontWeight": "bold"
            }),
            html.Th("Avg Hold (days)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Total PnL (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Total PnL (%)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
            html.Th("Final Balance (₽)", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
        ])
    )
    
    # Строки данных по каждому тикеру
    table_rows = []
    for row in summary_data:
        # Цвет строки в зависимости от PnL
        row_color = "#e8f5e9" if row["total_pnl"] > 0 else "#ffebee" if row["total_pnl"] < 0 else "#fff"
        
        table_rows.append(
            html.Tr([
                html.Td(row["ticker"], style={"padding": "10px", "borderBottom": "1px solid #eee", "fontWeight": "bold"}),
                html.Td(row["total_trades"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center"}),
                html.Td(row["closed_trades"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center"}),
                html.Td(row["winning"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center", "color": "green"}),
                html.Td(row["losing"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center", "color": "red"}),
                html.Td(f"{row['win_rate']:.1f}%", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center", "fontFamily": "monospace"}),
                html.Td(f"{row['avg_win']:.2f}₽", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center", "fontFamily": "monospace", "color": "green"}),
                html.Td(f"{row['avg_loss']:.2f}₽", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "center", "fontFamily": "monospace", "color": "red"}),

                # ✅ НОВАЯ ЯЧЕЙКА: AVG TP/SL
                html.Td(
                    f"{row['avg_mfe']:.1f}% / {row['avg_mae']:.1f}%",
                    style={
                        "padding": "10px",
                        "borderBottom": "1px solid #eee",
                        "textAlign": "center",
                        "fontFamily": "monospace",
                        "fontSize": "13px",
                        "color": "#333"
                    }
                    ),
                    html.Td(
                    f"{row['avg_hold_days']:.1f}d",
                    style={
                        "padding": "10px",
                        "borderBottom": "1px solid #eee",
                        "textAlign": "center",
                        "fontFamily": "monospace",
                    }
                ),

                html.Td(f"{row['total_pnl']:+.2f}₽", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace", "fontWeight": "bold", "color": "green" if row["total_pnl"] > 0 else "red"}),
                html.Td(f"{row['total_pnl_percent']:+.2f}%", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(f"{row['final_balance']:,.0f}₽", style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
            ], style={"backgroundColor": row_color})
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # ДОБАВЛЯЕМ ИТОГОВУЮ СТРОКУ (TOTAL)
    # ═══════════════════════════════════════════════════════════════════════
    
    total_row_color = "#d4edda" if total_stats["total_pnl"] > 0 else "#f8d7da" if total_stats["total_pnl"] < 0 else "#e9ecef"
    # Средний MFE/MAE по всем тикерам
    total_avg_mfe = sum(row["avg_mfe"] for row in summary_data) / len(summary_data) if summary_data else 0.0
    total_avg_mae = sum(row["avg_mae"] for row in summary_data) / len(summary_data) if summary_data else 0.0
    total_avg_hold = sum(row["avg_hold_days"] for row in summary_data) / len(summary_data) if summary_data else 0.0
    
    table_rows.append(
        html.Tr([
            
            html.Td("📊 TOTAL", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "fontWeight": "bold", 
                "fontSize": "15px",
                "backgroundColor": total_row_color
            }),
            html.Td(total_stats["total_trades"], style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontWeight": "bold",
                "backgroundColor": total_row_color
            }),
            html.Td(total_stats["closed_trades"], style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontWeight": "bold",
                "backgroundColor": total_row_color
            }),
            html.Td(total_stats["winning"], style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontWeight": "bold", 
                "color": "green",
                "backgroundColor": total_row_color
            }),
            html.Td(total_stats["losing"], style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontWeight": "bold", 
                "color": "red",
                "backgroundColor": total_row_color
            }),
            html.Td(f"{total_stats['win_rate']:.1f}%", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontFamily": "monospace", 
                "fontWeight": "bold",
                "fontSize": "15px",
                "backgroundColor": total_row_color
            }),
            html.Td(f"{total_stats['avg_win']:.2f}₽", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontFamily": "monospace", 
                "fontWeight": "bold", 
                "color": "green",
                "backgroundColor": total_row_color
            }),
            html.Td(f"{total_stats['avg_loss']:.2f}₽", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "center", 
                "fontFamily": "monospace", 
                "fontWeight": "bold", 
                "color": "red",
                "backgroundColor": total_row_color
            }),

            html.Td(
                f"{total_avg_mfe:.1f}% / {total_avg_mae:.1f}%",
                style={
                    "padding": "14px",
                    "borderTop": "3px solid #333",
                    "textAlign": "center",
                    "fontFamily": "monospace",
                    "fontWeight": "bold",
                    "backgroundColor": total_row_color
                }
            ),
            html.Td(
                f"{total_avg_hold:.1f}d",
                style={
                    "padding": "14px",
                    "borderTop": "3px solid #333",
                    "textAlign": "center",
                    "fontFamily": "monospace",
                    "fontWeight": "bold",
                    "backgroundColor": total_row_color
                }
            ),
            html.Td(f"{total_stats['total_pnl']:+.2f}₽", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "right", 
                "fontFamily": "monospace", 
                "fontWeight": "bold", 
                "fontSize": "16px",
                "color": "green" if total_stats["total_pnl"] > 0 else "red",
                "backgroundColor": total_row_color
            }),
            html.Td(f"{total_stats['total_pnl_percent']:+.2f}%", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "right", 
                "fontFamily": "monospace", 
                "fontWeight": "bold",
                "backgroundColor": total_row_color
            }),
            html.Td(f"{total_stats['final_balance']:,.0f}₽", style={
                "padding": "14px", 
                "borderTop": "3px solid #333", 
                "textAlign": "right", 
                "fontFamily": "monospace", 
                "fontWeight": "bold",
                "backgroundColor": total_row_color
            }),
        ])
    )
    
    # Создаём тело таблицы
    table_body = html.Tbody(table_rows)
    
    # Финальная таблица
    table = html.Table(
        [table_header, table_body],
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"}
    )
    
    return table


def generate_strategy_summary_cards(
    selected_strategy: str,
    selected_timeframe: str,
    start_date: str = None,
    end_date: str = None
) -> html.Div:
    """
    Карточки с ОБЩИМИ метриками по всем тикерам (агрегат).
    Использует ту же логику, что и TOTAL-строка таблицы.
    """
    # Переиспользуем вычисления из generate_strategy_summary_table
    # Запускаем стратегию для всех тикеров и собираем total_stats
    summary_data = []
    available_figis = [
        figi for figi, tfs in available_combinations.items()
        if selected_timeframe in tfs
    ]
    timeframe_map = {
        "30m": Timeframe.M30, "1h": Timeframe.H1,
        "4h": Timeframe.H4, "1d": Timeframe.D1,
    }
    timeframe = timeframe_map.get(selected_timeframe, Timeframe.H1)

    for figi in available_figis:
        try:
            ticker = figi_to_ticker.get(figi, figi)
            candles = history.get_candles(figi, timeframe)
            candles = [c for c in candles if c.volume and c.volume > 0]

            # фильтр по датам
            if start_date:
                sd = dt.strptime(start_date.split('T')[0], '%Y-%m-%d')
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= sd]
            if end_date:
                ed = dt.strptime(end_date.split('T')[0], '%Y-%m-%d')
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < ed + timedelta(days=1)]

            if not candles or len(candles) < 50:
                continue

            strategy = StrategyFactory.create(selected_strategy, selected_timeframe, debug=False)
            result = strategy.run(candles)
            stats = result["stats"]

            summary_data.append({
                "total_trades": stats.total_trades,
                "closed_trades": stats.closed_trades,
                "winning": stats.winning_trades,
                "losing": stats.losing_trades,
                "win_rate": stats.win_rate,
                "avg_win_abs": stats.avg_win_abs,
                "avg_loss_abs": stats.avg_loss_abs,
                "total_pnl": stats.total_pnl,
                "total_pnl_percent": stats.total_pnl_percent,
                "final_balance": stats.final_balance,
                "avg_hold_days": stats.avg_hold_days,
            })
        except Exception as e:
            logger.error(f"Error in cards: {figi}: {e}")
            continue

    if not summary_data:
        return html.Div("No data", style={"padding": "20px", "color": "#999"})

    # ── АГРЕГАТЫ ──
    total_trades   = sum(r["total_trades"] for r in summary_data)
    closed_trades  = sum(r["closed_trades"] for r in summary_data)
    winning        = sum(r["winning"] for r in summary_data)
    losing         = sum(r["losing"] for r in summary_data)
    win_rate       = (winning / closed_trades * 100) if closed_trades else 0.0
    total_pnl      = sum(r["total_pnl"] for r in summary_data)
    try:
        temp = StrategyFactory.create(selected_strategy, selected_timeframe, debug=False)
        INITIAL_BALANCE = temp.config.get("initial_balance", 5000.0)
    except:
        INITIAL_BALANCE = 5000.0

    total_pnl_pct  = total_pnl / INITIAL_BALANCE * 100
    final_balance  = INITIAL_BALANCE + total_pnl

    avg_win  = sum(r["avg_win_abs"] * r["winning"] for r in summary_data) / winning if winning else 0.0
    avg_loss = sum(r["avg_loss_abs"] * r["losing"] for r in summary_data) / losing if losing else 0.0
    avg_hold = sum(r["avg_hold_days"] for r in summary_data) / len(summary_data) if summary_data else 0.0

    # ── create_metric_card (локальная) ──
    def card(title, value, suffix="", color="#1976d2"):
        return html.Div([
            html.Div(title, style={"fontSize": "13px", "color": "#666", "marginBottom": "6px"}),
            html.Div(f"{value}{suffix}", style={
                "fontSize": "22px", "fontWeight": "bold", "color": color,
                "fontFamily": "monospace"
            }),
        ], style={
            "padding": "16px", "backgroundColor": "#fff",
            "borderRadius": "8px", "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            "textAlign": "center", "minWidth": "150px", "flex": "1",
        })

    # ── две строки карточек ──
    row1 = html.Div([
        card("Total Trades",    total_trades),
        card("Closed Trades",   closed_trades),
        card("Winning",         winning,    "", "#2e7d32"),
        card("Losing",          losing,     "", "#c62828"),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "14px"})

    row2 = html.Div([
        card("Win Rate",        f"{win_rate:.1f}", "%"),
        card("Avg Win",         f"{avg_win:+.0f}", "₽", "#2e7d32"),
        card("Avg Loss",        f"{avg_loss:+.0f}", "₽", "#c62828"),
        card("Avg Hold",        f"{avg_hold:.1f}", "d"),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "14px"})

    row3 = html.Div([
        card("Total PnL",       f"{total_pnl:+,.0f}", "₽",
             "#2e7d32" if total_pnl > 0 else "#c62828"),
        card("Total PnL %",     f"{total_pnl_pct:+.2f}", "%",
             "#2e7d32" if total_pnl_pct > 0 else "#c62828"),
        card("Final Balance",   f"{final_balance:,.0f}", "₽"),
        card("Instruments",     len(summary_data)),
    ], style={"display": "flex", "gap": "14px"})

    return html.Div([
        html.H4("📋 Aggregate Metrics (All Instruments)", style={
            "textAlign": "center", "color": "#555", "marginBottom": "16px"
        }),
        row1, row2, row3,
    ], style={
        "backgroundColor": "#f9f4ff",
        "borderRadius": "8px",
        "padding": "20px",
        "border": "1px solid #d1c4e9",
        "marginBottom": "24px",
    })



def generate_strategy_metrics_cards(
    selected_strategy: str, 
    selected_timeframe: str,
    start_date: str = None,
    end_date: str = None
) -> html.Div:
    """
    Генерирует карточки с ключевыми метриками стратегии.
    
    Args:
        selected_strategy: Код стратегии (e.g., "sma_crossover")
        selected_timeframe: Таймфрейм (e.g., "1h", "4h")
        start_date: Начальная дата (YYYY-MM-DD)
        end_date: Конечная дата (YYYY-MM-DD)
    
    Returns:
        html.Div: Блок с карточками метрик
    """
    logger.info(f"Generating strategy metrics for {selected_strategy} on {selected_timeframe}...")
    
    # ═══════════════════════════════════════════════════════════════
    # 📅 ОБРАБАТЫВАЕМ ДАТЫ
    # ═══════════════════════════════════════════════════════════════
    
    if start_date:
        # Обрезаем время, если есть (берём только дату)
        start_date_str = start_date.split('T')[0]
        start_date_dt = dt.strptime(start_date_str, '%Y-%m-%d')
        logger.info(f"  Start date: {start_date_dt.strftime('%Y-%m-%d')}")
    else:
        start_date_dt = None

    if end_date:
        # Обрезаем время, если есть (берём только дату)
        end_date_str = end_date.split('T')[0]
        end_date_dt = dt.strptime(end_date_str, '%Y-%m-%d')
        logger.info(f"  End date: {end_date_dt.strftime('%Y-%m-%d')}")
    else:
        end_date_dt = None
    
    # ═══════════════════════════════════════════════════════════════
    # СОБИРАЕМ МЕТРИКИ ПО КАЖДОМУ ТИКЕРУ ОТДЕЛЬНО
    # ═══════════════════════════════════════════════════════════════
    
    ticker_metrics = []
    
    # Получаем все доступные FIGI для выбранного таймфрейма
    available_figis = []
    for figi, timeframes in available_combinations.items():
        if selected_timeframe in timeframes:
            available_figis.append(figi)
    
    # Запускаем стратегию для каждого тикера
    for figi in available_figis:
        try:
            ticker = figi_to_ticker.get(figi, figi)
            
            # Загружаем свечи
            timeframe_map = {
                "30m": Timeframe.M30,
                "1h": Timeframe.H1,
                "4h": Timeframe.H4,
                "1d": Timeframe.D1,
            }
            timeframe = timeframe_map.get(selected_timeframe, Timeframe.H1)
            candles = history.get_candles(figi, timeframe)
            
            # Фильтруем пустые свечи
            candles = [c for c in candles if c.volume and c.volume > 0]
            
            # ═══════════════════════════════════════════════════════
            # 📅 ФИЛЬТРУЕМ ПО ДАТАМ
            # ═══════════════════════════════════════════════════════
            
            if start_date_dt:
                candles_before = len(candles)
                # Убираем timezone из open_time для сравнения
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) >= start_date_dt]
                logger.debug(f"  {ticker}: {candles_before} → {len(candles)} candles (start filter)")

            if end_date_dt:
                candles_before = len(candles)
                # Убираем timezone из open_time для сравнения
                end_date_inclusive = end_date_dt + timedelta(days=1)
                candles = [c for c in candles if c.open_time.replace(tzinfo=None) < end_date_inclusive]
                logger.debug(f"  {ticker}: {candles_before} → {len(candles)} candles (end filter)")
            
            if not candles or len(candles) < 50:
                logger.warning(f"Skipping {ticker}: insufficient data after date filter")
                continue
            
            # Создаём и запускаем стратегию
            strategy = StrategyFactory.create(
                strategy_code=selected_strategy,
                timeframe=selected_timeframe,
                debug=False
            )
            
            result = strategy.run(candles)
            stats = result["stats"]
            
            # Собираем метрики из stats (там уже правильный расчёт!)
            ticker_metrics.append({
                "ticker": ticker,
                "expectancy": stats.expectancy,
                "profit_factor": stats.profit_factor,
                "payoff_ratio": stats.payoff_ratio,
                "max_drawdown": stats.max_drawdown,
            })
            
            logger.info(f"  ✓ {ticker}: {stats.closed_trades} trades, MaxDD={stats.max_drawdown:.2f}%")
            
        except Exception as e:
            logger.error(f"Error processing {figi}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # ═══════════════════════════════════════════════════════════════
    # УСРЕДНЯЕМ МЕТРИКИ ПО ВСЕМ ТИКЕРАМ
    # ═══════════════════════════════════════════════════════════════
    
    if ticker_metrics:
        metrics = {
            "expectancy": sum(m["expectancy"] for m in ticker_metrics) / len(ticker_metrics),
            "profit_factor": sum(m["profit_factor"] for m in ticker_metrics) / len(ticker_metrics),
            "payoff_ratio": sum(m["payoff_ratio"] for m in ticker_metrics) / len(ticker_metrics),
            "max_drawdown": sum(m["max_drawdown"] for m in ticker_metrics) / len(ticker_metrics),
        }
        logger.info(f"  Averaged metrics across {len(ticker_metrics)} tickers")
    else:
        metrics = {
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "payoff_ratio": 0.0,
            "max_drawdown": 0.0,
        }
        logger.warning("  No metrics calculated (no data)")
    
    # ═══════════════════════════════════════════════════════════════
    # СОЗДАЁМ КАРТОЧКИ
    # ═══════════════════════════════════════════════════════════════
    
    def create_metric_card(title: str, value: float, suffix: str = "", 
                          good_threshold: float = None, higher_is_better: bool = True) -> html.Div:
        """Создаёт карточку метрики с цветовой индикацией"""
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
                "fontSize": "14px",
                "color": "#666",
                "marginBottom": "8px",
                "fontWeight": "500"
            }),
            html.Div(value_str, style={
                "fontSize": "28px",
                "fontWeight": "bold",
                "color": color,
                "fontFamily": "monospace"
            }),
        ], style={
            "padding": "20px",
            "backgroundColor": "#fff",
            "borderRadius": "8px",
            "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            "textAlign": "center",
            "minWidth": "180px",
            "flex": "1",
        })
    
    # Создаём карточки
    cards = html.Div([
        create_metric_card(
            "📈 Expectancy (%)",
            metrics["expectancy"],
            "%",
            good_threshold=0.0,
            higher_is_better=True
        ),
        create_metric_card(
            "💰 Profit Factor",
            metrics["profit_factor"],
            "",
            good_threshold=1.0,
            higher_is_better=True
        ),
        create_metric_card(
            "⚖️ Payoff Ratio",
            metrics["payoff_ratio"],
            "",
            good_threshold=1.0,
            higher_is_better=True
        ),
        create_metric_card(
            "📉 Max Drawdown (%)",
            metrics["max_drawdown"],
            "%",
            good_threshold=10.0,
            higher_is_better=False
        ),
    ], style={
        "display": "flex",
        "flexWrap": "wrap",
        "gap": "20px",
        "justifyContent": "center",
        "padding": "20px",
    })
    
    return html.Div([
        html.H3("📊 Strategy Metrics", style={"textAlign": "center", "marginBottom": "20px", "color": "#333"}),
        cards,
    ], style={
        "backgroundColor": "#e8f5e9",
        "borderRadius": "8px",
        "border": "2px solid #4caf50",
        "marginBottom": "30px",
    })



# Инициализируем Dash app
app = dash.Dash(__name__)
app.title = "Trading Dashboard"

# ============================================================================
# LAYOUT
# ============================================================================

# ============================================================================
# LAYOUT
# ============================================================================

app.layout = html.Div([
    html.Div([
        html.H1("📈 Trading Dashboard", style={"textAlign": "center", "marginBottom": 20}),
        
        # ════════════════════════════════════════════════════════════════════
        # 📊 ЗОНА 1: ФИЛЬТРЫ ДЛЯ ГРАФИКА
        # ════════════════════════════════════════════════════════════════════
        
        html.Div([
            html.H3("📈 Chart Filters", style={"marginBottom": "15px", "color": "#333"}),
            
            html.Div([
                # Instrument selector
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
                
                # Timeframe selector
                html.Div([
                    html.Label("Select Timeframe:", style={"fontWeight": "bold"}),
                    dcc.Dropdown(
                        id="timeframe-selector",
                        options=[],
                        value=None,
                        style={"width": "150px"}
                    ),
                ], style={"display": "inline-block", "marginRight": "30px"}),
                
                # Strategy selector
                html.Div([
                    html.Label("Strategy:", style={"fontWeight": "bold"}),
                    dcc.Dropdown(
                        id="strategy-selector",
                        options=[
                            {"label": name, "value": code}
                            for code, name in get_available_strategies().items()
                        ],
                        value="sma_crossover",
                        clearable=False,
                        style={"width": "250px"}
                    ),
                ], style={"display": "inline-block"}),
                # ═══════════════════════════════════════════════════════════════════
                # 📅 НОВОЕ: Фильтр дат для бэктестинга
                # ═══════════════════════════════════════════════════════════════════
                html.Div([
                    html.Label("📅 Date Range:", style={"fontWeight": "bold", "marginLeft": "20px"}),
                    dcc.DatePickerRange(
                        id="date-range-selector",
                        start_date=datetime.now() - timedelta(days=365),  # По умолчанию год назад
                        end_date=datetime.now(),
                        display_format='YYYY-MM-DD',
                        style={"marginLeft": "10px"}
                    ),
                ], style={"display": "inline-block", "marginLeft": "30px"}),
            ]),
        ], style={
            "padding": "20px",
            "backgroundColor": "#e3f2fd",
            "borderRadius": "5px",
            "marginBottom": "20px",
            "border": "2px solid #2196F3"
        }),
        
        # График
        dcc.Graph(id="candlestick-chart"),
        
        # Информация о свечах
        html.Div(id="candles-info", style={"marginTop": "20px", "padding": "10px", "backgroundColor": "#f9f9f9"}),

        # После графика и candles-info, перед ЗОНОЙ 2
        html.Div(id="strategy-metrics-cards", style={"marginTop": "30px"}),
        
        # ════════════════════════════════════════════════════════════════════
        # 📊 ЗОНА 2: ФИЛЬТРЫ ДЛЯ СВОДНОЙ ТАБЛИЦЫ
        # ════════════════════════════════════════════════════════════════════
        
        html.Div([
            html.H2("📊 Strategy Performance Summary", style={"marginTop": "40px", "marginBottom": "15px"}),
            
            # Фильтры для таблицы
            html.Div([
                # Timeframe selector для таблицы
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
                        value="1h",
                        clearable=False,
                        style={"width": "200px"}
                    ),
                ], style={"display": "inline-block", "marginRight": "30px"}),
                
                # Strategy selector для таблицы
                html.Div([
                    html.Label("Strategy:", style={"fontWeight": "bold"}),
                    dcc.Dropdown(
                        id="table-strategy-selector",
                        options=[
                            {"label": name, "value": code}
                            for code, name in get_available_strategies().items()
                        ],
                        value="sma_crossover",
                        clearable=False,
                        style={"width": "250px"}
                    ),
                ], style={"display": "inline-block"}),
                # ═══════════════════════════════════════════════════════════════════
                # 📅 НОВОЕ: Фильтр дат для таблицы
                # ═══════════════════════════════════════════════════════════════════
                html.Div([
                    html.Label("📅 Date Range:", style={"fontWeight": "bold", "marginLeft": "20px"}),
                    dcc.DatePickerRange(
                        id="table-date-range-selector",
                        start_date=datetime.now() - timedelta(days=365),
                        end_date=datetime.now(),
                        display_format='YYYY-MM-DD',
                        style={"marginLeft": "10px"}
                    ),
                ], style={"display": "inline-block", "marginLeft": "30px"}),
            ], style={"marginBottom": "20px"}),
            
            # Сводные карточки (агрегат по всем инструментам)
            html.Div(id="strategy-summary-cards", style={"marginBottom": "20px"}),

            # Сводная таблица
            html.Div(id="strategy-summary-table", style={
                "overflowX": "auto",
                "marginTop": "10px",
                "border": "1px solid #ddd",
                "borderRadius": "5px",
                "backgroundColor": "#fff"
            }),
        ], style={
            "marginTop": "40px",
            "padding": "20px",
            "backgroundColor": "#f3e5f5",
            "borderRadius": "5px",
            "border": "2px solid #9c27b0"
        }),
        
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

# ============================================================================
# CALLBACK ДЛЯ СВОДНОЙ ТАБЛИЦЫ (НЕЗАВИСИМЫЙ)
# ============================================================================

@app.callback(
    [Output("strategy-summary-cards", "children"),
     Output("strategy-summary-table", "children")],
    [Input("table-timeframe-selector", "value"),
     Input("table-strategy-selector", "value"),
     Input("table-date-range-selector", "start_date"),
     Input("table-date-range-selector", "end_date")]
)
def update_summary_table(selected_timeframe: str, selected_strategy: str,
                         start_date: str = None, end_date: str = None):
    """
    Обновить сводную таблицу по всем тикерам с фильтром по датам.
    """
    if selected_timeframe is None or selected_strategy is None:
        empty = html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})
        return empty, empty
    
    logger.info(f"Updating summary table: {selected_strategy} on {selected_timeframe}")
    
    cards = generate_strategy_summary_cards(selected_strategy, selected_timeframe, start_date, end_date)
    table = generate_strategy_summary_table(selected_strategy, selected_timeframe, start_date, end_date)
    return cards, table

@app.callback(
    [Output("candlestick-chart", "figure"),
     Output("candles-info", "children"),
     Output("market-data-table-container", "children")],  # ✅ БЕЗ strategy-summary-table
    [Input("timeframe-selector", "value"),
     Input("instrument-selector", "value"),
     Input("strategy-selector", "value")]
)
def update_chart(selected_timeframe: str, selected_figi: str, selected_strategy: str):
    """Обновляет график, паттерны, стратегию и сделки."""
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


    # 🔍 DEBUG: Проверяем расчёт индикаторов для Dual MA
    if selected_strategy == "dual_ma_crossover":
        logger.info(f"Testing MA Adaptive calculation for {len(candles)} candles...")
        
        try:
            from src.strategies.indicators_builder import IndicatorsBuilder
            
            test_builder = IndicatorsBuilder(candles)
            
            # Тестируем SMA50
            sma_test = test_builder.build_sma(period=50)
            sma_valid = sum(1 for v in sma_test if v is not None)
            logger.info(f"   SMA50: {sma_valid}/{len(candles)} valid values")
            
            # Тестируем MA Adaptive
            ma_adaptive_test = test_builder.build_ma_adaptive(period=14)
            ma_adaptive_valid = sum(1 for v in ma_adaptive_test if v is not None)
            logger.info(f"   MA Adaptive: {ma_adaptive_valid}/{len(candles)} valid values")
            
            # Показываем последние значения
            if sma_valid > 0:
                last_sma = next((v for v in reversed(sma_test) if v is not None), None)
                logger.info(f"   Last SMA50: {last_sma:.2f}")
            
            if ma_adaptive_valid > 0:
                last_ma_adaptive = next((v for v in reversed(ma_adaptive_test) if v is not None), None)
                logger.info(f"   Last MA Adaptive: {last_ma_adaptive:.2f}")
            
        except Exception as e:
            logger.error(f"❌ MA Adaptive test failed: {e}")
            import traceback
            traceback.print_exc()





    # 📊 РАССЧИТЫВАЕМ ИНДИКАТОРЫ
    indicators_results = indicators_builder.build_indicators(candles)

    # Текущее (последнее) значение индикаторов
    last_indicators_values = {}
    for name, result in indicators_results.items():
        if result is None:
            continue
        val = result.value
        if isinstance(val, dict):
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
        "is_complete": c.is_complete,
        "candle_source": c.candle_source,
    } for c in candles])
    df = df.reset_index(drop=True)

    # ==============================
    # 📈 SMA / EMA ПО ВСЕМ СВЕЧАМ
    # ==============================
    closes = df["close"].tolist()

    def compute_sma(values: list, period: int) -> list:
        sma = [None] * len(values)
        for i in range(period - 1, len(values)):
            window = values[i - period + 1: i + 1]
            sma[i] = sum(window) / period
        return sma

    sma_period = get_indicator_config("SMA").get("period")
    sma_series = compute_sma(closes, sma_period)

    # ════════════════════════════════════════════════════════════
    # 🎯 ЗАПУСКАЕМ ВЫБРАННУЮ СТРАТЕГИЮ
    # ════════════════════════════════════════════════════════════

    strategy_result = None
    strategy_stats_text = ""
    entry_trades = []
    exit_trades = []

    try:
        # 🔑 СОЗДАЁМ СТРАТЕГИЮ ЧЕРЕЗ ФАБРИКУ
        strategy = StrategyFactory.create(
            strategy_code=selected_strategy,
            timeframe=selected_timeframe,
            debug=False
        )
        
        logger.info(f"Running {strategy.name} strategy on {len(candles)} candles...")
        
        # Запускаем стратегию
        strategy_result = strategy.run(candles)
        
        # Получаем сделки и статистику
        trades = strategy_result["trades"]
        stats = strategy_result["stats"]
        
        logger.info(f"{strategy.name}: {len(trades)} trades, {stats.closed_trades} closed")


        # Подготавливаем данные для отрисовки входов и выходов
        for trade in trades:
            # ENTRY
            entry_trades.append({
                "index": trade.entry_index,
                "price": trade.entry_price,
                "time": trade.entry_time,
            })

            # EXIT (если закрыта)
            if not trade.is_open():
                exit_trades.append({
                    "index": trade.exit_index,
                    "price": trade.exit_price,
                    "time": trade.exit_time,
                    "pnl_percent": trade.pnl_percent,
                    "is_win": trade.pnl_percent > 0 if trade.pnl_percent else False,
                })

        # Форматируем статистику
        strategy_stats_text = f"""
        <b>📊 {strategy.name} ({selected_timeframe})</b><br>
        Total trades: <b>{stats.total_trades}</b> | Closed: <b>{stats.closed_trades}</b><br>
        Winning: {stats.winning_trades} | Losing: {stats.losing_trades} | Win Rate: <b>{stats.win_rate:.1f}%</b><br>
        Avg Win: <b>{stats.avg_win:+.2f}%</b> | Avg Loss: <b>{stats.avg_loss:+.2f}%</b><br>
        Total PnL: <b>{stats.total_pnl:+.2f}₽</b> ({stats.total_pnl_percent:+.2f}%) | Final Balance: <b>{stats.final_balance:,.0f}₽</b>
        """

    except Exception as e:
        logger.error(f"Error running strategy: {e}")
        import traceback
        traceback.print_exc()

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

    # ════════════════════════════════════════════════════════════
    # 📈 ОТРИСОВКА ИНДИКАТОРОВ В ЗАВИСИМОСТИ ОТ СТРАТЕГИИ
    # ════════════════════════════════════════════════════════════

    if selected_strategy == "sma_crossover":
        # Для SMA Crossover: рисуем только SMA50
        fig.add_trace(go.Scatter(
            x=df.index,
            y=sma_series,
            mode="lines",
            name=f"SMA({sma_period})",
            line=dict(color="blue", width=1),
            opacity=0.8,
            yaxis="y",
        ))

    elif selected_strategy == "dual_ma_crossover":
        # Для Dual MA: рисуем SMA50 и MA Adaptive
        fig.add_trace(go.Scatter(
            x=df.index,
            y=sma_series,
            mode="lines",
            name=f"SMA(50) Fast",
            line=dict(color="blue", width=2),
            opacity=0.9,
            yaxis="y",
        ))
        
        # MA Adaptive (медленная линия)
        try:
            from src.strategies.indicators_builder import IndicatorsBuilder
            builder = IndicatorsBuilder(candles)
            ma_adaptive_series = builder.build_ma_adaptive(period=14)
            
            valid_count = sum(1 for v in ma_adaptive_series if v is not None)
            
            if valid_count > 0:
                fig.add_trace(go.Scatter(
                    x=df.index,
                    y=ma_adaptive_series,
                    mode="lines",
                    name="MA Adaptive(14) Slow",
                    line=dict(color="orange", width=2),
                    opacity=0.9,
                    yaxis="y",
                ))
                logger.info(f"✅ Drew MA Adaptive(14) line on chart ({valid_count} valid points)")
            else:
                logger.warning(f"⚠️ MA Adaptive has no valid values")
                
        except Exception as e:
            logger.error(f"❌ Failed to draw MA Adaptive: {e}")
            import traceback
            traceback.print_exc()

    elif selected_strategy == "dual_ma_ma_crossover":
        # ✅ ПОЛУЧАЕМ ПАРАМЕТРЫ ИЗ СТРАТЕГИИ (вместо hardcode)
        try:
            indicator_params = strategy.get_indicator_params()
            
            # MA Slow (из конфига стратегии)
            ma_slow_period = indicator_params["ma_slow"]["period"]
            ma_slow_series = compute_sma(closes, ma_slow_period)
            
            fig.add_trace(go.Scatter(
                x=df.index,
                y=ma_slow_series,
                mode="lines",
                name=indicator_params["ma_slow"]["name"],  # "MA(50) Slow"
                line=dict(color=indicator_params["ma_slow"]["color"], width=2),
                opacity=0.9,
                yaxis="y",
            ))
            
            # MA Fast (из конфига стратегии)
            ma_fast_period = indicator_params["ma_fast"]["period"]
            
            # Используем ту же функцию compute_sma для расчёта MA
            def compute_ma(values: list, period: int) -> list:
                ma = [None] * len(values)
                for i in range(period - 1, len(values)):
                    window = values[i - period + 1: i + 1]
                    ma[i] = sum(window) / period
                return ma
            
            ma_fast_series = compute_ma(closes, ma_fast_period)  # ← Теперь из конфига!
            
            valid_count = sum(1 for v in ma_fast_series if v is not None)
            if valid_count > 0:
                fig.add_trace(go.Scatter(
                    x=df.index,
                    y=ma_fast_series,
                    mode="lines",
                    name=indicator_params["ma_fast"]["name"],  # "MA(4) Fast" или MA(8), зависит от конфига
                    line=dict(color=indicator_params["ma_fast"]["color"], width=2),
                    opacity=0.9,
                    yaxis="y",
                ))
                logger.info(f"✅ Drew {indicator_params['ma_fast']['name']} line on chart ({valid_count} valid points)")
            else:
                logger.warning(f"⚠️ {indicator_params['ma_fast']['name']} has no valid values")
                
        except Exception as e:
            logger.error(f"❌ Failed to draw indicators for dual_ma_ma_crossover: {e}")
            import traceback
            traceback.print_exc()


    # ════════════════════════════════════════════════════════════
    # 🎯 ОТРИСОВКА СДЕЛОК НА ГРАФИКЕ
    # ════════════════════════════════════════════════════════════

    # ENTRY POINTS - серые треугольники
    if entry_trades:
        entry_x = [t["index"] for t in entry_trades]
        entry_y = [t["price"] for t in entry_trades]
        entry_text = [f"Entry: {t['price']:.2f}₽" for t in entry_trades]

        fig.add_trace(go.Scatter(
            x=entry_x,
            y=entry_y,
            mode='markers',
            marker=dict(
                symbol='triangle-up',
                size=8,
                color='gray',
                line=dict(color='darkgray', width=0.5)
            ),
            name='Entry',
            text=entry_text,
            hovertemplate='%{text}<extra></extra>',
        ))

    # EXIT POINTS - красные/зелёные треугольники
    if exit_trades:
        win_x = [t["index"] for t in exit_trades if t["is_win"]]
        win_y = [t["price"] for t in exit_trades if t["is_win"]]
        win_text = [
            f"Exit: {t['price']:.2f}₽ ({t['pnl_percent']:+.2f}%)" 
            for t in exit_trades if t["is_win"]
        ]

        if win_x:
            fig.add_trace(go.Scatter(
                x=win_x,
                y=win_y,
                mode='markers',
                marker=dict(
                    symbol='triangle-down',
                    size=8,
                    color='green',
                    line=dict(color='darkgreen', width=0.5)
                ),
                name='Exit (Win)',
                text=win_text,
                hovertemplate='%{text}<extra></extra>',
            ))

        loss_x = [t["index"] for t in exit_trades if not t["is_win"]]
        loss_y = [t["price"] for t in exit_trades if not t["is_win"]]
        loss_text = [
            f"Exit: {t['price']:.2f}₽ ({t['pnl_percent']:+.2f}%)" 
            for t in exit_trades if not t["is_win"]
        ]

        if loss_x:
            fig.add_trace(go.Scatter(
                x=loss_x,
                y=loss_y,
                mode='markers',
                marker=dict(
                    symbol='triangle-down',
                    size=8,
                    color='red',
                    line=dict(color='darkred', width=0.5)
                ),
                name='Exit (Loss)',
                text=loss_text,
                hovertemplate='%{text}<extra></extra>',
            ))

    # ========================================================
    # 🕵️♂️ ОПРЕДЕЛЕНИЕ ПАТТЕРНОВ
    # ========================================================
    # patterns = strategy.detect_all_patterns(candles)
    # if patterns:
    #     logger.info(f"Found {len(patterns)} patterns")
    #     bullish_x, bullish_y, bullish_txt = [], [], []
    #     bearish_x, bearish_y, bearish_txt = [], [], []
    #     neutral_x, neutral_y, neutral_txt = [], [], []

    #     for p in patterns:
    #         candle_idx = None
    #         for idx, row in df.iterrows():
    #             if row["time"] == p.timestamp:
    #                 candle_idx = idx
    #                 break

    #         if candle_idx is None:
    #             continue

    #         if p.type == "BULLISH":
    #             bullish_x.append(candle_idx)
    #             bullish_y.append(p.price * 0.999)
    #             bullish_txt.append(p.name)
    #         elif p.type == "BEARISH":
    #             bearish_x.append(candle_idx)
    #             bearish_y.append(p.price * 1.001)
    #             bearish_txt.append(p.name)
    #         else:
    #             neutral_x.append(candle_idx)
    #             neutral_y.append(p.price * 1.001)
    #             neutral_txt.append(p.name)

    #     # Добавляем маркеры на график
    #     if bullish_x:
    #         fig.add_trace(go.Scattergl(
    #             x=bullish_x, y=bullish_y, mode='markers',
    #             marker=dict(symbol='triangle-up', size=10, color='green'),
    #             text=bullish_txt, name='Bullish Pattern',
    #             hovertemplate='%{text}'
    #         ))

    #     if bearish_x:
    #         fig.add_trace(go.Scattergl(
    #             x=bearish_x, y=bearish_y, mode='markers',
    #             marker=dict(symbol='triangle-down', size=10, color='red'),
    #             text=bearish_txt, name='Bearish Pattern',
    #             hovertemplate='%{text}'
    #         ))

    #     if neutral_x:
    #         fig.add_trace(go.Scattergl(
    #             x=neutral_x, y=neutral_y, mode='markers',
    #             marker=dict(symbol='circle', size=8, color='gray'),
    #             text=neutral_txt, name='Pattern',
    #             hovertemplate='%{text}'
    #         ))

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
                x=max_x, y=max_y,
                mode='markers',
                marker=dict(symbol='circle', size=8, color='#82C7B7',
                           line=dict(color='darkgreen', width=0.5)),
                name='Local Maxima (V-shaped)',
                text=max_prices,
                hovertemplate='MAX: %{text}₽'
            ))

        if min_x:
            fig.add_trace(go.Scatter(
                x=min_x, y=min_y,
                mode='markers',
                marker=dict(symbol='circle', size=8, color='#D5505C',
                           line=dict(color='darkred', width=0.5)),
                name='Local Minima (V-shaped)',
                text=min_prices,
                hovertemplate='MIN: %{text}₽'
            ))

    # ========================================================
    # 🎯 ОТРИСОВКА УРОВНЕЙ (SUPPORT/RESISTANCE)
    # ========================================================
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

    # ════════════════════════════════════════════════════════════
    # 📋 ИНФОРМАЦИОННЫЙ БЛОК
    # ════════════════════════════════════════════════════════════

    # patterns_count = len(patterns) if patterns else 0
    extremums_count = len(extremums) if extremums else 0

    info_text = f"""
    Total candles: {len(candles)} | Patterns found: patterns_count | Extremums found: {extremums_count} |
    Period: {candles[0].open_time.strftime('%Y-%m-%d')} → {candles[-1].open_time.strftime('%Y-%m-%d')}
    """

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
        dcc.Markdown(
            strategy_stats_text.replace("<b>", "**").replace("</b>", "**").replace("<br>", "\n"),
            style={"color": "#333", "fontSize": "13px", "marginTop": "15px", "padding": "10px", "backgroundColor": "#f0f8ff", "borderRadius": "5px", "borderLeft": "4px solid #2196F3"}
        ) if strategy_stats_text else None,
    ])

    # ════════════════════════════════════════════════════════════
    # 📊 ТАБЛИЦА МАРКЕТ ДАННЫХ
    # ════════════════════════════════════════════════════════════

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
                html.Th("is_complete", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
                html.Th("candle_source", style={"padding": "12px", "textAlign": "right", "borderBottom": "2px solid #ddd", "fontWeight": "bold"}),
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
                html.Td(row["is_complete"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
                html.Td(row["candle_source"], style={"padding": "10px", "borderBottom": "1px solid #eee", "textAlign": "right", "fontFamily": "monospace"}),
            ])
            for idx, row in df_table.iterrows()
        ])
    ], style={
        "width": "100%",
        "borderCollapse": "collapse",
        "fontSize": "14px",
        "backgroundColor": "white"
    })


    

    return fig, info_block, table, 



@app.callback(
    Output("strategy-metrics-cards", "children"),
    [
        Input("table-timeframe-selector", "value"),
        Input("table-strategy-selector", "value"),
        Input("table-date-range-selector", "start_date"),
        Input("table-date-range-selector", "end_date"),
    ]
)
def update_metrics_cards(selected_timeframe: str, selected_strategy: str, 
                         start_date: str = None, end_date: str = None):
    """
    Обновить карточки метрик стратегии с фильтром по датам.
    """
    if selected_timeframe is None or selected_strategy is None:
        empty = html.Div("Select timeframe and strategy", style={"padding": "20px", "color": "#999"})
        return empty, empty
    
    logger.info(f"Updating metrics cards: {selected_strategy} on {selected_timeframe}")
    
    return generate_strategy_metrics_cards(
        selected_strategy, 
        selected_timeframe,
        start_date,
        end_date
    ) 
# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Dash server at http://127.0.0.1:8050")
    app.run(debug=True)