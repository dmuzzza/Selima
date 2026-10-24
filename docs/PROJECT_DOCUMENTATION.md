> **УСТАРЕЛО (2026-10-22).** Документ описывает проект в состоянии до ревизии и
> содержит неточности (например, лимит позиций, набор стратегий, расположение файлов).
> Оставлен как исторический контекст. Актуальная документация:
> [ARCHITECTURE.md](ARCHITECTURE.md), [STRATEGY_CONTRACT.md](STRATEGY_CONTRACT.md),
> [TASKS.md](TASKS.md), [DECISIONS.md](DECISIONS.md), [CHANGELOG.md](CHANGELOG.md).

# Документация проекта Trading System

## 📋 Оглавление

1. [Обзор проекта](#обзор-проекта)
2. [Архитектура системы](#архитектура-системы)
3. [Структура файлов](#структура-файлов)
4. [Основные компоненты](#основные-компоненты)
5. [Связи между модулями](#связи-между-модулями)
6. [Методики реализации](#методики-реализации)
7. [Конфигурация и запуск](#конфигурация-и-запуск)
8. [База данных](#база-данных)
9. [Торговый процесс](#торговый-процесс)

---

## Обзор проекта

**Trading System** — автоматизированная торговая платформа для работы с биржей Т-Инвестиций (Tinkoff Invest API).

### Основные возможности

- 📊 **Сбор и хранение рыночных данных** — загрузка свечей (30m, 1h, 4h, 1d) в PostgreSQL
- 🤖 **Торговые стратегии** — SMA Crossover, Dual MA Crossover, Dual MA MA Crossover
- 📈 **Бэктестинг** — тестирование стратегий на исторических данных
- 🎯 **Live-торговля** — автоматическое открытие/закрытие позиций
- 📱 **Dash-дашборд** — визуализация данных и результатов стратегий
- ⏰ **Планировщик** — автоматическое обновление данных и торговля по расписанию

### Технологический стек

- **Язык**: Python 3.9+
- **Брокер**: Tinkoff Invest SDK
- **БД**: PostgreSQL (рыночные данные), SQLite (торговые сделки)
- **Веб-интерфейс**: Dash + Plotly
- **Планировщик**: APScheduler
- **ORM**: SQLAlchemy

---

## Архитектура системы

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER INTERFACE                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  main.py        │  │ app_dashboard.py│  │   scheduler.py          │ │
│  │  (тест)         │  │  (Dash UI)      │  │   (автоторговля)        │ │
│  └────────┬────────┘  └────────┬────────┘  └───────────┬─────────────┘ │
└───────────┼─────────────────────┼──────────────────────┼────────────────┘
            │                     │                      │
┌───────────▼─────────────────────▼──────────────────────▼────────────────┐
│                         CORE MODULES (src/)                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ strategies/  │  │ brokers/     │  │ market_data/ │  │ trading/   │  │
│  │ • стратегии  │  │ • брокеры    │  │ • данные     │  │ • торговля │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ analytics/   │  │ backtest/    │  │ storage/     │                  │
│  │ • аналитика  │  │ • тесты      │  │ • модели     │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
            │                     │                      │
┌───────────▼─────────────────────▼──────────────────────▼────────────────┐
│                         DATA LAYER                                       │
│  ┌─────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │  PostgreSQL             │  │  SQLite                             │  │
│  │  marketdata.db          │  │  data/live_trades.db                │  │
│  │  (рыночные данные)      │  │  (торговые сделки)                  │  │
│  └─────────────────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
            │
┌───────────▼─────────────────────────────────────────────────────────────┐
│                         EXTERNAL API                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    Tinkoff Invest API                            │   │
│  │  • Котировки  • Сделки  • Позиции  • Стоп-ордера                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Структура файлов

```
project/
├── main.py                      # Тестовый скрипт подключения к брокеру
├── app_dashboard.py             # Dash-приложение для визуализации
├── scheduler.py                 # Планировщик для автоторговли
├── check_data_db.py             # Проверка БД
├── reload_data.py               # Перезагрузка данных
├── reload_data_v2.py            # Перезагрузка данных v2
├── inspect_client.py            # Инспектор клиента
├── test_check.py                # Тесты
│
├── config/
│   ├── instruments.json         # Конфигурация инструментов (FIGI, тикеры)
│   └── trading_config.py        # Параметры торговли (лимиты позиций)
│
├── src/
│   ├── __init__.py
│   │
│   ├── strategies/              # Торговые стратегии
│   │   ├── base_strategy.py    # Базовый класс Strategy (абстрактный)
│   │   ├── strategy_factory.py # Фабрика стратегий
│   │   ├── strategy_registry.py# Реестр стратегий
│   │   ├── indicators_builder.py # Построитель индикаторов
│   │   ├── indicators_config.py  # Конфигурация индикаторов
│   │   ├── levels_builder.py   # Построитель уровней
│   │   ├── levels_config.py    # Конфигурация уровней
│   │   ├── levels_detector*.py # Детекторы уровней поддержки/сопротивления
│   │   ├── patterns_config.py  # Конфигурация паттернов
│   │   ├── extremums_config.py # Конфигурация экстремумов
│   │   │
│   │   ├── sma_crossover/      # Стратегия SMA Crossover
│   │   │   ├── config.py       # Конфигурация (TP/SL, периоды)
│   │   │   ├── strategy.py     # Реализация стратегии
│   │   │   └── signal_validator.py # Валидатор сигналов
│   │   │
│   │   ├── dual_ma_crossover/  # Стратегия Dual MA Crossover
│   │   │   ├── config.py
│   │   │   └── strategy.py
│   │   │
│   │   └── dual_ma_ma_crossover/ # Стратегия Dual MA MA Crossover
│   │       ├── config.py
│   │       └── strategy.py
│   │
│   ├── brokers/                 # Интеграция с брокером
│   │   ├── base.py             # Абстрактный класс брокера
│   │   ├── broker_factory.py   # Фабрика брокеров
│   │   ├── sdk_broker.py       # Tinkoff SDK брокер
│   │   ├── position_manager.py # Управление размерами позиций
│   │   ├── position_calculator.py # Калькулятор позиций
│   │   ├── trade_executor.py   # Исполнение сделок (открытие/TP/SL)
│   │   └── account_info.py     # Информация о счёте
│   │
│   ├── market_data/            # Рыночные данные
│   │   ├── models.py           # Модель Candle
│   │   ├── timeframe.py        # Enum таймфреймов
│   │   ├── history_manager.py  # Менеджер истории (PostgreSQL)
│   │   ├── data_updater.py     # Обновление данных
│   │   └── candle_loader.py    # Загрузчик свечей
│   │
│   ├── trading/                # Торговый процесс
│   │   ├── models.py           # LiveTrade, TradeStatus
│   │   ├── repository.py       # Репозиторий сделок (SQLite)
│   │   ├── orchestrator.py     # Координатор торговли
│   │   ├── account_selector.py # Выбор счёта по таймфрейму
│   │   ├── order_collector.py  # Сбор стоп-ордеров
│   │   ├── reconciler.py       # Сверка статусов (API ↔ БД)
│   │   ├── stop_order_models.py# Модели стоп-ордеров
│   │   └── stop_order_repository.py # Репозиторий стоп-ордеров
│   │
│   ├── storage/                # Хранение
│   │   └── models.py           # Базовые модели хранения
│   │
│   ├── analytics/              # Аналитика
│   │   └── strategy_analyzer.py# Анализ результатов стратегий
│   │
│   ├── backtest/               # Бэктестинг
│   │   └── backtester.py       # Движок бэктестинга
│   │
│   └── utils/                  # Утилиты
│       ├── crypto.py           # Шифрование данных (account_id)
│       └── __init__.py
│
├── data/                        # Данные (SQLite БД)
│   └── live_trades.db          # База торговых сделок
│
├── migrations/                  # Миграции БД
├── scripts/                     # Скрипты
├── tests/                       # Тесты
├── logs/                        # Логи
├── archive/                     # Архив
├── debug/                       # Отладочные файлы
│
└── docs/                        # Документация
    └── PROJECT_DOCUMENTATION.md # Этот файл
```

---

## Основные компоненты

### 1. Стратегии (`src/strategies/`)

**Базовый класс**: `BaseStrategy`

Все стратегии наследуются от `BaseStrategy` и реализуют:

```python
@abstractmethod
def detect_signals(self, candles: List[Candle]) -> List[Signal]:
    """Определить торговые сигналы"""

@abstractmethod
def execute_trades(self, candles: List[Candle]) -> List[Trade]:
    """Симулировать торговлю"""

@property
@abstractmethod
def name(self) -> str:
    """Имя стратегии"""
```

**Структура сигнала**:
```python
@dataclass
class Signal:
    timestamp: datetime
    candle_index: int
    signal_type: str  # "BULLISH" | "BEARISH"
    price: float
    metadata: Dict
```

**Структура сделки**:
```python
@dataclass
class Trade:
    trade_id: int
    entry_time: datetime
    entry_index: int
    entry_price: float
    signal_type: str
    tp_level: float
    sl_level: float
    quantity: float
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    exit_type: Optional[str]  # "TP" | "SL"
    pnl_percent: Optional[float]
    pnl_abs: Optional[float]
    mfe_percent: Optional[float]  # Maximum Favorable Excursion
    mae_percent: Optional[float]  # Maximum Adverse Excursion
```

**Доступные стратегии**:

| Код | Название | Описание |
|-----|----------|----------|
| `sma_crossover` | SMA Crossover | Пересечение цены через SMA50 |
| `dual_ma_crossover` | Dual MA Crossover | Пересечение SMA50 и MA Adaptive |
| `dual_ma_ma_crossover` | Dual MA MA Crossover | Пересечение двух MA с адаптивными периодами |

### 2. Брокеры (`src/brokers/`)

**Фабрика брокеров**: `BrokerFactory.create(broker_type, token)`

**Компоненты**:

| Класс | Назначение |
|-------|------------|
| `PositionManager` | Расчёт размера позиции с учётом маржи и лимитов |
| `TradeExecutor` | Открытие рыночных заявок, выставление TP/SL |
| `PositionCalculator` | Калькулятор размера позиции на основе свободной маржи |

**Workflow открытия позиции**:

```
1. Strategy → Signal
2. PositionManager.calculate_trade_parameters()
   ├── Проверка лимита позиций (max 3 на счёт)
   ├── Расчёт размера позиции (маржа / max_positions)
   └── Получение торговых параметров (лотность, цена)
3. TradeExecutor.execute_trade()
   ├── Открытие рыночной заявки
   ├── Расчёт цен TP/SL
   ├── Выставление Take Profit (лимитная заявка)
   └── Выставление Stop Loss (стоп-лимит заявка)
4. TradingOrchestrator.process_signals()
   └── Сохранение в БД (статус OPEN)
```

### 3. Рыночные данные (`src/market_data/`)

**HistoryManager** — работа с PostgreSQL:

```python
# Загрузка истории
history.load_initial_history(
    ticker_list=["BBG004730N88", ...],
    timeframes=[Timeframe.H1, Timeframe.H4, Timeframe.D1],
    days_back=365
)

# Получение свечей
candles = history.get_candles(figi, timeframe)

# Инкрементальное обновление
history.update_history(figi, timeframe)
```

**DataUpdater** — умное обновление:

```python
# Полная перезагрузка
updater.full_reload(days_back=14)

# Инкрементальное обновление (только новые свечи)
updater.incremental_update(hours_back=2)
```

**Модель свечи**:
```python
@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_complete: bool
    candle_source: Optional[int]
```

### 4. Торговля (`src/trading/`)

**TradingOrchestrator** — координатор:

```python
orchestrator = TradingOrchestrator(
    token=token,
    account_id=account_id,
    strategy_name="SMA Crossover",
    timeframe="1h",
    db_path="data/live_trades.db"
)

live_trades = orchestrator.process_signals(
    signals=strategy.signals,
    ticker="SBER",
    figi="BBG004730N88",
    tp_percent=1.5,
    sl_percent=1.0
)
```

**LiveTradeRepository** — хранение сделок:

- Шифрование `account_id` перед сохранением
- Хеширование для поиска без расшифровки
- Индексы для быстрого поиска по статусу, тикеру, счёту

**OrderCollector** — сбор стоп-ордеров:

1. Находит сделки с `orders_collected = 0`
2. Ждёт 30 секунд (ордера появляются на бирже не мгновенно)
3. Запрашивает стоп-ордера через API
4. Сохраняет в `StopOrderRepository`
5. Помечает сделку: `orders_collected = 1`

**TradingReconciler** — сверка статусов:

- Периодическая проверка статусов стоп-ордеров (API → БД)
- Автоматическое закрытие сделок при исполнении TP/SL
- Fallback: ручное закрытие при исчезновении позиции

### 5. Дашборд (`app_dashboard.py`)

**Компоненты**:

- **График свечей** с индикаторами (SMA, MA Adaptive)
- **Таблица результатов** по всем тикерам
- **Фильтры**: инструмент, таймфрейм, стратегия

**Callback'и**:
```python
@app.callback(
    Output("candlestick-chart", "figure"),
    [Input("timeframe-selector", "value"),
     Input("instrument-selector", "value"),
     Input("strategy-selector", "value")]
)
def update_chart(timeframe, instrument, strategy):
    # Обновление графика
```

---

## Связи между модулями

### Диаграмма зависимостей

```
┌──────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINTS                                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────────┐    │
│  │  main.py     │  │  app_dashboard.py│  │   scheduler.py      │    │
│  └──────┬───────┘  └────────┬─────────┘  └──────────┬──────────┘    │
└─────────┼───────────────────┼───────────────────────┼────────────────┘
          │                   │                       │
          ▼                   ▼                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         src/                                          │
│                                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │   strategies/   │◄─┤   brokers/      │◄─┤   market_data/      │  │
│  │                 │  │                 │  │                     │  │
│  │ • BaseStrategy  │  │ • BrokerFactory │  │ • HistoryManager    │  │
│  │ • StrategyFactory│ │ • PositionManager│ │ • DataUpdater       │  │
│  │ • SMACrossover  │  │ • TradeExecutor │  │ • Timeframe         │  │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────────┘  │
│           │                    │                                     │
│           │                    ▼                                     │
│           │          ┌─────────────────┐                             │
│           │          │   trading/      │                             │
│           └─────────►│                 │                             │
│                      │ • Orchestrator  │                             │
│                      │ • Repository    │                             │
│                      │ • Reconciler    │                             │
│                      │ • OrderCollector│                             │
│                      └────────┬────────┘                             │
│                               │                                       │
└───────────────────────────────┼───────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         DATA LAYER                                    │
│  ┌─────────────────────────┐  ┌─────────────────────────────────┐   │
│  │  PostgreSQL             │  │  SQLite                         │   │
│  │  (marketdata.db)        │  │  (live_trades.db)               │   │
│  └─────────────────────────┘  └─────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### Ключевые связи

| Модуль | Зависит от | Используется в |
|--------|------------|----------------|
| `BaseStrategy` | — | Все стратегии, StrategyFactory |
| `StrategyFactory` | StrategyRegistry | scheduler.py, app_dashboard.py |
| `BrokerFactory` | AbstractBroker, SDKBroker | HistoryManager, DataUpdater, PositionManager |
| `PositionManager` | PositionCalculator, TradingConfig | TradingOrchestrator |
| `TradeExecutor` | — | TradingOrchestrator |
| `TradingOrchestrator` | PositionManager, TradeExecutor, LiveTradeRepository | Стратегии (execute_live_trades) |
| `HistoryManager` | Broker, SQLAlchemy | DataUpdater, app_dashboard.py |
| `DataUpdater` | HistoryManager, BrokerFactory | scheduler.py |
| `LiveTradeRepository` | SQLite, CryptoUtils | TradingOrchestrator, OrderCollector, Reconciler |
| `OrderCollector` | LiveTradeRepository, StopOrderRepository | scheduler.py |
| `TradingReconciler` | LiveTradeRepository, StopOrderRepository | scheduler.py |

---

## Методики реализации

### 1. Стратегии

**Паттерн**: Strategy Pattern с фабрикой

```python
# Создание стратегии через фабрику
strategy = StrategyFactory.create(
    strategy_code="sma_crossover",
    timeframe="1h",
    config={"tp_percent": 1.5, "sl_percent": 1.0},
    debug=False
)

# Запуск
result = strategy.run(candles)
# result = {"signals": [...], "trades": [...], "stats": StrategyStats}
```

**Валидация сигналов**:
```python
# В конфиге стратегии
"validation": {
    "enabled": True,
    "mode": "default",  # или "strict", "custom"
    "custom_rules": {
        "candle_direction": True,
        "confirmation": True,
        "min_breakout_distance": 0.2
    }
}
```

**Фильтр тренда**:
```python
"trend_filter": {
    "enabled": True,
    "sma_period": 200
}
# BULLISH сигнал принимается только если цена выше SMA200
# BEARISH сигнал принимается только если цена ниже SMA200
```

### 2. Управление позициями

**Лимиты**:
```python
# config/trading_config.py
TRADING_CONFIG = {
    "max_positions_per_account": 3,  # Максимум 3 позиции на счёт
    "min_position_size": 5000.0,      # Минимальный размер позиции
    "margin_reserve_percent": 0.0,    # Резерв маржи
}
```

**Расчёт размера позиции**:
```
max_position_size = (свободная маржа × (1 - reserve%)) / max_positions
lots = int(max_position_size / lot_value)
actual_position_size = lots × lot_value
```

### 3. Приоритеты задач (scheduler.py)

```python
class TaskPriority(IntEnum):
    CRITICAL = 0      # Обновление данных + открытие сделок
    IMPORTANT = 1     # Сбор стоп-ордеров
    BACKGROUND = 2    # Мониторинг позиций
```

**Расписание**:
| Задача | Приоритет | Частота | Время |
|--------|-----------|---------|-------|
| Обновление данных | P0 | Каждые 30 мин + 15с | 05:00 - 00:30 MSK |
| Сбор стоп-ордеров | P1 | Каждые 2 мин | 05:00 - 00:30 MSK |
| Мониторинг позиций | P2 | Каждые 3 мин | 05:00 - 00:30 MSK |

**Логика приоритетов**:
- P0 выполняется всегда, блокирует P1 и P2
- P1 выполняется если не идёт P0
- P2 выполняется если не идёт P0

### 4. Балансировка нагрузки по счетам

**AccountSelector** выбирает счёт по таймфрейму:

| Таймфрейм | Счёт |
|-----------|------|
| 1d | ACCOUNT_ID_DAILY |
| 4h | ACCOUNT_ID_4H или ACCOUNT_ID_4H_2 |
| 1h | ACCOUNT_ID_1H или ACCOUNT_ID_1H_2 |
| 30m | ACCOUNT_ID_1H или ACCOUNT_ID_1H_2 |

**Стратегия выбора**:
```python
account_id = selector.get_account_id(
    timeframe="4h",
    position_manager=pm,
    strategy="least_loaded"  # Выбрать счёт с наименьшей загрузкой
)
```

### 5. Шифрование чувствительных данных

**CryptoUtils** (`src/utils/crypto.py`):

```python
from src.utils.crypto import get_crypto

crypto = get_crypto()

# Шифрование account_id
encrypted = crypto.encrypt(account_id)

# Хеширование для поиска
account_id_hash = crypto.hash_account_id(account_id)

# Расшифровка
decrypted = crypto.decrypt(encrypted)
```

**Применение**:
- `account_id` в БД хранится зашифрованным
- Поиск по `account_id_hash` (без расшифровки)
- При чтении — автоматическая расшифровка

---

## Конфигурация и запуск

### Переменные окружения (.env)

```bash
# Tinkoff API
TINKOFF_API_TOKEN=your_token_here

# Broker type
BROKER_TYPE=sdk

# Accounts (для балансировки)
ACCOUNT_ID_DAILY=...
ACCOUNT_ID_4H=...
ACCOUNT_ID_4H_2=...
ACCOUNT_ID_1H=...
ACCOUNT_ID_1H_2=...

# PostgreSQL
PG_USER=...
PG_PASSWORD=...
PG_HOST=...
PG_DBNAME=...

# Crypto key (для шифрования)
CRYPTO_KEY=your_secret_key
```

### Конфигурация инструментов (config/instruments.json)

```json
{
  "instruments": [
    {
      "figi": "BBG004730N88",
      "ticker": "SBER",
      "enabled": true,
      "timeframes": ["1h", "4h", "1d"]
    },
    {
      "figi": "BBG0047315HK6",
      "ticker": "GAZP",
      "enabled": true,
      "timeframes": ["1h", "4h", "1d"]
    }
  ]
}
```

### Запуск компонентов

**1. Тест подключения**:
```bash
python main.py
```

**2. Загрузка данных**:
```bash
python reload_data.py
```

**3. Запуск дащборда**:
```bash
python app_dashboard.py
# Доступ: http://127.0.0.1:8050
```

**4. Запуск планировщика (автоторговля)**:
```bash
python scheduler.py
```

---

## База данных

### PostgreSQL (рыночные данные)

**Таблица**: `market_data`

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | BigInteger | Primary key |
| figi | String(50) | FIGI инструмента |
| ticker | String(50) | Тикер |
| timeframe | String(10) | Таймфрейм (H1, H4, D1) |
| open_time | DateTime | Время открытия свечи (MSK) |
| open, high, low, close | Numeric(14,6) | Цены OHLC |
| volume | BigInteger | Объём |
| is_complete | Boolean | Завершена ли свеча |
| candle_source | Integer | Источник данных |

**Уникальное ограничение**:
```sql
UNIQUE (figi, timeframe, open_time)
```

### SQLite (торговые сделки)

**Таблица**: `live_trades`

| Колонка | Тип | Описание |
|---------|-----|----------|
| trade_id | TEXT | Уникальный ID сделки |
| position_id | TEXT | ID позиции из API |
| strategy_name | TEXT | Название стратегии |
| timeframe | TEXT | Таймфрейм |
| ticker, figi | TEXT | Инструмент |
| direction | TEXT | LONG/SHORT |
| entry_time, entry_price | DATETIME, REAL | Вход |
| exit_time, exit_price, exit_type | DATETIME, REAL, TEXT | Выход |
| pnl_abs, pnl_percent | REAL | P&L |
| tp_price, sl_price | REAL | Уровни TP/SL |
| status | TEXT | PENDING/OPEN/CLOSED/FAILED |
| account_id | TEXT | Зашифрованный ID счёта |
| account_id_hash | TEXT | Хеш для поиска |
| orders_collected | INTEGER | Флаг сбора ордеров |

**Индексы**:
```sql
idx_live_trades_status
idx_live_trades_position_id
idx_live_trades_ticker_account
```

**Таблица**: `stop_orders`

| Колонка | Тип | Описание |
|---------|-----|----------|
| stop_order_id | TEXT | ID стоп-ордера из API |
| position_id | TEXT | Связь с позицией |
| trade_id | TEXT | Связь со сделкой |
| order_type | TEXT | TP/SL |
| stop_price | REAL | Цена активации |
| status | TEXT | ACTIVE/EXECUTED/CANCELLED/EXPIRED |
| metadata | TEXT | JSON с данными из API |

---

## Торговый процесс

### Полный цикл торговли

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          SCHEDULER (каждые 30 мин)                       │
│                                                                          │
│  1. 🔴 P0: Обновление данных                                            │
│     ├─ incremental_update(hours_back=2)                                 │
│     └─ Загрузка новых свечей в PostgreSQL                               │
│                                                                          │
│  2. 🎯 Запуск стратегий                                                 │
│     ├─ Для каждого таймфрейма (1h, 4h, 1d)                             │
│     ├─ Для каждого инструмента                                          │
│     │  ├─ Загрузить свечи из БД                                        │
│     │  ├─ strategy.check_last_signal(candles)                          │
│     │  └─ Если сигнал → execute_live_trades()                          │
│     └─ TradingOrchestrator.process_signals()                            │
│        ├─ Проверка дублей (нет ли открытой позиции)                    │
│        ├─ PositionManager.calculate_trade_parameters()                 │
│        ├─ TradeExecutor.execute_trade()                                │
│        │  ├─ Рыночная заявка                                           │
│        │  ├─ Take Profit (лимитная)                                    │
│        │  └─ Stop Loss (стоп-лимит)                                    │
│        └─ LiveTradeRepository.save() (статус OPEN)                     │
│                                                                          │
│  3. 🟡 P1: Сбор стоп-ордеров (каждые 2 мин)                             │
│     ├─ OrderCollector.collect_pending_orders()                          │
│     ├─ Ждать 30 секунд после открытия                                   │
│     ├─ Запросить стоп-ордера через API                                  │
│     ├─ StopOrderRepository.save()                                       │
│     └─ Пометить: orders_collected = 1                                   │
│                                                                          │
│  4. 🟢 P2: Мониторинг позиций (каждые 3 мин)                            │
│     ├─ TradingReconciler.reconcile()                                    │
│     ├─ Проверка статусов стоп-ордеров (API → БД)                       │
│     ├─ При EXECUTED → закрыть сделку в БД                               │
│     └─ Fallback: ручное закрытие при исчезновении позиции              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Предотвращение дублей

**На уровне Orchestrator**:
```python
existing_trades = repository.get_open_trades_by_ticker(
    ticker=ticker,
    account_id=account_id,
    timeframe=timeframe
)
if existing_trades:
    return None  # Не открываем дубль
```

**На уровне Scheduler**:
```python
tf_accounts = account_selector.get_all_accounts(timeframe)
if repository.has_open_trade_on_timeframe_accounts(
    ticker=ticker,
    timeframe=timeframe,
    direction=direction,
    account_ids=tf_accounts
):
    return  # Дубль на любом счёте таймфрейма
```

---

## Приложения

### A. Глоссарий

| Термин | Определение |
|--------|-------------|
| **FIGI** | Global Identification Code инструмента |
| **Candle** | Свеча (OHLCV) за период |
| **Signal** | Торговый сигнал (BULLISH/BEARISH) |
| **Trade** | Сделка (открытие + закрытие) |
| **TP** | Take Profit — цель по прибыли |
| **SL** | Stop Loss — ограничение убытка |
| **LONG** | Покупка (ожидание роста) |
| **SHORT** | Продажа (ожидание падения) |
| **PnL** | Profit and Loss — финансовый результат |
| **MFE** | Maximum Favorable Excursion — макс. потенциальная прибыль |
| **MAE** | Maximum Adverse Excursion — макс. потенциальный убыток |

### B. Диаграмма последовательности (открытие позиции)

```
Scheduler → Strategy → Orchestrator → PositionManager → TradeExecutor → Broker API
    │          │           │              │                │               │
    │          │           │              │                │               │
    ├─run_trading_strategies()           │                │               │
    │          │           │              │                │               │
    │─────────▶│           │              │                │               │
    │ check_last_signal()│              │                │               │
    │          │           │              │                │               │
    │◀─────────┤           │              │                │               │
    │ Signal   │           │              │                │               │
    │          │           │              │                │               │
    │          ├─execute_live_trades()   │                │               │
    │          │──────────▶│              │                │               │
    │          │           │              │                │               │
    │          │           ├─process_signals()            │               │
    │          │           │─────────────▶│                │               │
    │          │           │              │                │               │
    │          │           │              │                │               │
    │          │           │              │                │               │
    │          │           │              │                │               │
    │          │           │              │                │               │
```

---

**Версия документации**: 1.0  
**Дата обновления**: 2024  
**Поддержка**: NLP-Core-Team
