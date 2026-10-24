"""
Точка входа для Trading Dashboard.

Замена монолитного app_dashboard.py (~1800 строк).
Собирает приложение из модулей:
  - config.py       : константы
  - data.py         : доступ к данным
  - compute.py      : бизнес-логика
  - ui/components.py: UI-компоненты
  - ui/chart_builder.py: построение графиков
  - ui/layout.py    : структура страницы
  - callbacks.py    : регистрация колбэков

Запуск:
    python -m dashboard.app
    или
    python dashboard/app.py
"""

import os
import sys
import logging

# Добавляем корень проекта в путь (нужно при запуске python dashboard/app.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

import dash

from src.brokers.broker_factory import BrokerFactory
from src.market_data.history_manager import HistoryManager
from src.strategies.indicators_builder import IndicatorsBuilder

from dashboard import data as dashboard_data
from dashboard.ui.layout import create_layout
from dashboard.callbacks import register_callbacks

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКА
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashApp")
load_dotenv()
token = os.getenv("TINKOFF_API_TOKEN")

# ═══════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ КОМПОНЕНТОВ
# ═══════════════════════════════════════════════════════════════
logger.info("Initializing components...")
broker = BrokerFactory.create("sdk", token)
history = HistoryManager(broker)
indicators_builder = IndicatorsBuilder(debug=True)

# Инициализируем data-слой
dashboard_data.init(history, broker)

# ═══════════════════════════════════════════════════════════════
# DASH APP
# ═══════════════════════════════════════════════════════════════
app = dash.Dash(__name__)
app.title = "Trading Dashboard"

# Layout
app.layout = create_layout()

# Callbacks
register_callbacks(app, broker, history, indicators_builder)

# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("Starting Dash server at http://127.0.0.1:8050")
    app.run(debug=True)
