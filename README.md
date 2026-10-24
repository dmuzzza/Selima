# p_selima_v8.1 — автоматическая торговая система (T-Invest API)

Система для сбора рыночных данных, бэктестинга торговых стратегий и автоматической
торговли через T-Invest API (Tinkoff/T-Bank) с визуализацией в Dash и запуском по
расписанию через APScheduler.

## Статус

**Блокеры запуска закрыты (T0):** `import scheduler` проходит, `pytest tests` — 14 passed,
реестр стратегий содержит только существующие пакеты (`sma_crossover`,
`dual_ma_crossover_after_testing`).

Что ещё не сделано:

- нет git-репозитория и CI, нет `Dockerfile` / `docker-compose.yml` (T5-2, T5-3, T5-4, T5-6);
- не закрыты P0-риски реальной торговли: единый движок бэктеста (T2-1),
  walk-forward вместо подбора TP/SL на всей истории (T2-8);
- legacy-мусор в структуре: `src/sma_crossover/`, `app_dashboard.py`, одноразовые скрипты
  в корне (T1-2, T1-3).

Подробный разбор — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), план работ —
[docs/TASKS.md](docs/TASKS.md).

## Что умеет

| Возможность | Модуль |
|---|---|
| Загрузка свечей (30m/1h/4h/1d/1w) в PostgreSQL, инкрементально | `src/market_data/` |
| Торговые стратегии с единым интерфейсом | `src/strategies/` |
| Бэктест (TP/SL, комиссии, MFE/MAE, метрики) | `src/strategies/base_strategy.py` |
| Аналитика результата стратегии | `src/analytics/` |
| Live-торговля: расчёт лота, маркет-ордер, TP/SL | `src/brokers/`, `src/trading/` |
| Сверка состояния с биржей, закрытие сделок | `src/trading/reconciler.py` |
| Dash-дашборд: свечи, индикаторы, уровни, сделки | `dashboard/` |
| Планировщик (P0/P1/P2) | `scheduler.py` |
| Уведомления в VK без сделок | `scheduler_notifier.py` |

## Стек

- Python 3.12 (зафиксировано в `pyproject.toml`: `>=3.12,<3.13`)
- T-Invest SDK (`t_tech.invest`, версия 1.51.0 — вендорный wheel в `vendor/`)
- PostgreSQL — единственное хранилище: рыночные данные, сделки, стоп-ордера
- SQLAlchemy, Dash + Plotly, APScheduler
- `cryptography` (Fernet) для шифрования `account_id`
- Управление зависимостями: `uv` (`pyproject.toml` + `uv.lock`)

## Установка

Требуется [uv](https://docs.astral.sh/uv/). Он сам поставит нужный Python.

```bash
pip install --user uv            # если uv ещё нет
uv python install 3.12
uv sync --all-extras             # создаст .venv и поставит зависимости
copy .env.example .env           # заполнить значения
```

Проверка:

```bash
uv run python -c "import t_tech.invest, sqlalchemy, dash; print('deps ok')"
```

### Про SDK T-Invest

Официальный индекс `opensource.tbank.ru` временно недоступен, SDK публикуется на GitHub.
Wheel `t_tech_investments-1.51.0-py3-none-any.whl` лежит в `vendor/` (316 КБ) и подключён
через `[tool.uv.sources]`. Причина локальной копии: на некоторых машинах uv не может
проверить TLS-цепочку `raw.githubusercontent.com` (Let's Encrypt `YR1`).

После восстановления официального индекса: заменить в `pyproject.toml`
`"t-tech-investments"` на `"t-tech-investments>=1.51"`, удалить `[tool.uv.sources]`,
`vendor/` и выполнить `uv lock`.

`requirements.txt` — сгенерированный артефакт (`uv export`), руками не правится:

```bash
uv export --format requirements-txt --no-hashes --all-extras -o requirements.txt
```

## Структура (кратко)

```
p_selima_v8.1/
├── main.py                  # smoke-тест подключения к брокеру
├── scheduler.py             # автоторговля по расписанию
├── scheduler_notifier.py    # только сигналы + уведомления в VK
├── app_dashboard.py         # legacy-дашборд-монолит (~1800 строк)
├── dashboard/               # актуальный дашборд (пакет)
├── config/                  # instruments.json, комиссии, лимиты
├── src/                     # ядро: brokers, market_data, strategies, trading, ...
├── migrations/              # ручные миграции (SQLite legacy + PostgreSQL signal_key)
├── scripts/                 # одноразовые утилиты и экспорт
├── optimization/            # подбор параметров, ТЗ на ML-оптимизацию
├── archive/, debug/         # архив и отладочные скрипты
└── docs/                    # документация
```

Полное дерево с описанием каждого файла — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните. В репозитории значений нет.

| Переменная | Назначение | Обязательна |
|---|---|---|
| `TINKOFF_API_TOKEN` | токен T-Invest API | да |
| `BROKER_TYPE` | `sdk` (по умолчанию) | нет |
| `TRADING_MODE` | `dry_run` (по умолчанию) или `live` | нет |
| `LIVE_CONFIRM` | `yes` — обязательное подтверждение для `TRADING_MODE=live` | для live |
| `TEST_MODE` | `true` — фиктивные сигналы вместо рыночных | нет |
| `PG_HOST`, `PG_USER`, `PG_PASSWORD`, `PG_DBNAME` | подключение к PostgreSQL с рыночными данными | да |
| `ACCOUNT_ID_DUAL_MA_LONG` | счёт для LONG-сделок | да |
| `ACCOUNT_ID_DUAL_MA_SHORT` | счёт для SHORT-сделок | да |
| `ACCOUNT_ENCRYPTION_KEY` | ключ Fernet для шифрования `account_id` в БД. Должен совпадать с ключом, которым зашифрованы существующие записи | да |
| `VK_TOKEN`, `VK_CHAT_ID` | уведомления (`scheduler_notifier.py`) | нет |

## Запуск локально

| Задача | Команда |
|---|---|
| Проверка подключения к брокеру | `uv run python main.py` |
| Дашборд | `uv run python -m dashboard.app` (http://127.0.0.1:8050) |
| Автоторговля | `uv run python scheduler.py` |
| Только сигналы + VK | `uv run python scheduler_notifier.py` |
| Загрузка истории | `uv run python reload_data_v2.py` |
| Миграция БД (signal_key) | `uv run python migrations/migrate_add_signal_key.py` |
| Тесты | `uv run pytest` |
| Линтер / типы | `uv run ruff check .` / `uv run mypy src` |

`Dockerfile` и `docker-compose.yml` пока отсутствуют (`docs/TASKS.md`, T5-3, T5-4).

## Документация

| Файл | Содержание |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | дерево проекта, компоненты, потоки данных, схема БД, дефекты |
| [docs/STRATEGY_CONTRACT.md](docs/STRATEGY_CONTRACT.md) | стандарт стратегии: входы/выходы, сигналы, сделки, чек-лист новой стратегии |
| [docs/TASKS.md](docs/TASKS.md) | бэклог с приоритетами и критериями приёмки |
| [docs/DECISIONS.md](docs/DECISIONS.md) | принятые и предлагаемые архитектурные решения (ADR) |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | история изменений (Keep a Changelog, SemVer) |
| [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md) | **устарело**, оставлено как исторический контекст |

## Важно

- **Режим торговли задаётся только окружением.** По умолчанию `TRADING_MODE=dry_run`:
  сигналы находятся и логируются, но ордера не отправляются. Для реальных сделок нужны
  одновременно `TRADING_MODE=live` и `LIVE_CONFIRM=yes`; иначе процесс не стартует.
- **Один сигнал — максимум одна сделка.** Ключ `figi|timeframe|candle_open_time|direction`
  (`live_trades.signal_key`) блокирует повторный вход по той же закрытой свече, в том
  числе после рестарта процесса. Для существующей БД примените миграцию:
  `uv run python migrations/migrate_add_signal_key.py`.
- `TEST_MODE=true` генерирует фиктивные сигналы вместо рыночных.
- `account_id` в логах маскируется (`***1234`); ключ шифрования не логируется.
