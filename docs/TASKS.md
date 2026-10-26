# Бэклог задач

Формат: `ID | приоритет | оценка | статус`. Статусы: `todo`, `in-progress`, `done`,
`blocked`. Приоритеты: **P0** — блокирует запуск/риск денег, **P1** — качество и
поддерживаемость, **P2** — улучшения.

Оценка — в условных шагах (S ≤ 1 ч, M ≤ полдня, L ≤ 2 дня).

---

## T0. Блокеры запуска (P0)

| ID | Задача | Оценка | Статус |
|---|---|---|---|
| T0-1 | Почистить `src/strategies/strategy_registry.py`: оставить только существующие стратегии (`sma_crossover`, `dual_ma_crossover_after_testing`), остальные — закомментировать с пометкой `# TODO: restore when package added` | S | done |
| T0-2 | Убрать логирование секретов: `src/utils/crypto.py:29-30` (ключ шифрования), `src/trading/account_selector.py:49-53` (полный `account_id`) | S | done |
| T0-3 | Согласовать scheduler и стратегию: `check_last_signal`/`execute_live_trades` добавлены в `BaseStrategy`, scheduler переключён на существующий пакет | M | done |
| T0-4 | Добавить `pyproject.toml` + lock-файл (uv) с зависимостями | M | done |
| T0-5 | Добавить `src/__init__.py`, `src/strategies/__init__.py`, `src/backtest/__init__.py`, `src/storage/__init__.py` | S | done |
| T0-6 | Удалить пустой `config/config.py` | S | done |

**Приёмка T0:** `python -c "import scheduler"` и `python -m dashboard.app` проходят без
ImportError; в логах нет токенов и `account_id`.

Проверено после T0-1/T0-5: `import src.strategies.strategy_registry` проходит,
`get_available_strategies()` возвращает `{'sma_crossover': 'SMA Crossover',
'dual_ma_crossover_after_testing': 'Dual MA Crossover'}`.

Проверено после T0-4: `uv sync --all-extras` ставит 70 пакетов, все runtime-импорты
проходят (`t_tech.invest`, `sqlalchemy`, `dash`, `pandas`, `apscheduler`, `pytz`,
`plotly`, `numpy`, `cryptography`, `certifi`, `dotenv`, `psycopg2`). `import scheduler`
падает на `ModuleNotFoundError: No module named 'src.strategies.dual_ma_crossover'` —
это T0-3.

Состав зависимостей (T0-4): runtime — `t-tech-investments` (вендорный wheel 1.51.0),
`sqlalchemy`, `psycopg2-binary`, `python-dotenv`, `pytz`, `apscheduler`, `dash`, `plotly`,
`pandas`, `numpy`, `cryptography`, `certifi`; extras `optimization` — `tqdm`, `optuna`;
группа `dev` — `pytest`, `ruff`, `mypy`. Не включены: `tinkoff-investments` (старый SDK,
3 файла — T1-3), `pandas-ta` (корневой legacy `candle_patterns.py`).

Проверено после T0-2: `AccountCrypto()` без ключа бросает `ValueError` с командой
генерации и ничего не пишет в лог; `mask_account_id("2000123456") == "***3456"`,
`mask_account_id(None) == "<not set>"`. Маскирование применено в `account_selector`,
`orchestrator`, `position_manager`, `trade_executor`, `account_info`.

Побочно найдено (не исправлено, T1-4): `position_manager.py:271-299` — недостижимый
код после `return`, в нём `F821: remaining_positions`.

Проверено после T0-3: `import scheduler` проходит (`DRY_RUN = False` — см. T4-5).
`tests/test_strategy_contract.py` — 6 тестов зелёные: базовые `check_last_signal`/
`execute_live_trades` наследуются, стратегии их не дублируют, паритет
`check_last_signal` ↔ `detect_signals` на синтетических свечах (пересечение ровно на
последней свече), обработка пустого и короткого ряда.

Проверено после T4-5: `config/trading_mode.py` + `tests/test_trading_mode.py` (8 тестов);
`pytest tests` → 14 passed. `TRADING_MODE=live` без `LIVE_CONFIRM` → `TradingModeError`
на импорте `scheduler`; с `LIVE_CONFIRM=yes` → `mode=live, is_live=True`; без переменных →
`mode=dry_run, is_live=False`. В `dry_run` `_process_instrument` возвращает
`trades_count=0` до создания `TradingOrchestrator`. `scheduler_notifier.py`: `LIVE_TRADING`
удалён, вместо него `TRADING_MODE.is_live`. Логирование префикса токена удалено.

---

## T1. Задача №1 — анализ структуры и проблемных мест

Выполнено: разбор кода и фиксация дефектов в `docs/ARCHITECTURE.md` §9.

| ID | Задача | Оценка | Статус |
|---|---|---|---|
| T1-1 | Провести инвентаризацию дубликатов и решить судьбу каждого: `src/sma_crossover/`, `src/candle_patterns/`, `app_dashboard.py`, `scheduler_notifier.py`, `src/backtest/`, `src/storage/models.py`, `levels_detector*`, `sma_crossover_strategy.py` | S | done (список в ARCHITECTURE §9) |
| T1-2 | Удалить/переместить дубликаты: оставить `src/strategies/sma_crossover/`, удалить `src/sma_crossover/`; `app_dashboard.py` → `archive/`; `src/backtest/backtester.py` → `archive/` (после T3-3) | M | todo |
| T1-3 | Разделить корень: production (`scheduler.py`, `scheduler_notifier.py`, `dashboard/`) и `tools/` (все `*.py`-скрипты из корня, `debug/`, `optimization/`, `scripts/`) | M | todo |
| T1-4 | Исправить дефекты логики: двойной `check_last_signal` (`scheduler.py:463,512`), двойной `allow_short` (`base_strategy.py:208,221`), `total_pnl_percent` (`base_strategy.py:293`), сортировка перед Sharpe/Z-score | M | in-progress (двойной `allow_short` исправлен; остальное — todo) |
| T1-5 | Привести время к единой зоне: всё хранится в UTC (aware), в MSK конвертируется только на выводе. Заменить `datetime.now()` на `datetime.now(timezone.utc)` | M | todo |
| T1-6 | Убрать расхождение документации и кода: `max_positions_per_account` (6 vs 3), cron P2 (`*/3` vs «15 минут»), часы работы планировщика | S | todo |
| T1-7 | Ввести `ruff` + `mypy` + `pytest`, прогнать по проекту, зафиксировать baseline (ошибки — в отдельный список) | M | in-progress (`pyproject.toml` настроен; ruff: 65 legacy-замечаний (UP006/UP035/UP045, `I001`, `F841`, `SIM102`), mypy: 56 ошибок в 7 файлах — в основном implicit Optional и `None`-арифметика в стратегиях. Новые модули (`simulator.py`, `tests/`) — чистые) |

**Приёмка T1:** в `src/` нет файлов-дубликатов; линтер и типы проходят на ядре;
`docs/ARCHITECTURE.md` соответствует дереву.

---

## T2. Задача №2 — идеи по улучшению

| ID | Идея | Приоритет | Оценка | Статус |
|---|---|---|---|---|
| T2-1 | **Один движок бэктеста** (`TradeSimulator`) вместо копии цикла в каждой стратегии | P0 | L | done (T3-3) |
| T2-2 | **Конфигурация через pydantic-settings**: один `Settings` вместо `os.getenv` в 15 файлах, валидация на старте, понятные ошибки | P1 | M | todo |
| T2-3 | **TP/SL в конфиг-файле** (YAML/JSON) с валидацией, вместо словаря в Python; версионирование параметров и привязка к результатам оптимизации | P1 | M | todo |
| T2-4 | **Единое хранилище сделок** (PostgreSQL) + Alembic; SQLite — только для локальной разработки | P1 | L | in-progress (scheduler и notifier переведены на PG; Alembic и перенос старых данных — todo) |
| T2-5 | **Идемпотентность live-торговли**: проверка «сигнал уже обработан» по `(figi, timeframe, candle_open_time, direction)`, а не только по открытой позиции | P0 | M | done |
| T2-6 | **Защита от двойного открытия** при рестарте: блокировка/ключ в БД перед `post_market_order` | P0 | M | done (частично: PENDING-запись с `signal_key` создаётся до отправки ордера) |
| T2-7 | **Модель издержек**: проскальзывание, гэпы, спред, реальные тарифы брокера (сейчас только 0.05% + маржа) | P1 | M | todo |
| T2-8 | **Walk-forward и out-of-sample** вместо подбора TP/SL на всей истории (сейчас `optimization/grid_tp_sl_dual_ma.py` подбирает на тех же данных) | P0 (риск переобучения) | L | todo |
| T2-9 | **Метрики риска**: Sortino, Calmar, VaR, средняя просадка, распределение R-multiple | P2 | M | todo |
| T2-10 | **Наблюдаемость**: структурные JSON-логи, метрики (открытые позиции, ошибки API, лаг данных), healthcheck-endpoint | P1 | M | todo |
| T2-11 | **Уведомления**: единый notifier-интерфейс (VK/Telegram), события: сигнал, сделка, ошибка, ежедневный отчёт | P2 | S | todo |
| T2-12 | **Бэктест на нескольких инструментах одновременно** с общим капиталом и лимитом позиций (сейчас каждый инструмент считается изолированно) | P1 | L | todo |
| T2-13 | **Дашборд**: убрать `debug=True`, вынести host/port в env, добавить equity-curve и таблицу метрик по стратегиям | P1 | M | todo |
| T2-14 | **CI**: GitHub Actions — ruff, mypy, pytest на PR в `dev` | P1 | S | todo |
| T2-15 | **Docker**: multi-stage, non-root, healthcheck, compose с PostgreSQL для локальной разработки | P1 | M | todo |
| T2-16 | **Аудит зависимостей**: `pip-audit`, `bandit`, `trivy` в CI | P2 | S | todo |

**Приёмка T2 (минимум перед реальной торговлей):** T2-1, T2-5, T2-6, T2-8.

---

## T3. Задача №3 — единая структура получения данных из стратегий

Подробно — `docs/STRATEGY_CONTRACT.md`.

| ID | Задача | Оценка | Статус |
|---|---|---|---|
| T3-1 | Зафиксировать контракт v1 (фактические модели и методы) — **сделано** в `STRATEGY_CONTRACT.md` §2–3 | S | done |
| T3-2 | Добавить в `BaseStrategy` дефолтные `check_last_signal()` и `execute_live_trades()`, удалить копии из стратегий | M | done |
| T3-3 | Вынести цикл TP/SL в `src/backtest/simulator.py` (`TradeSimulator`, `ExitRules`, `SizingRules`, `CostModel`); стратегии вызывают движок | L | done (движок + `BaseStrategy.execute_trades`; `CostModel` остался в `BaseStrategy._calculate_commissions`) |
| T3-4 | Ввести `ExitPolicy` (протокол) + реализацию `PercentExitPolicy`; перенести TP/SL по тикерам в конфиг | M | todo |
| T3-5 | Исправить модель исполнения в симуляторе: `position_size_percent`, несколько позиций, порядок TP/SL по фактическому касанию, гэпы, `exit_time` | M | done (T3-5a/b/c: SL-first, гэпы по `open`, `exit_time` = конец бара; ADR-012) |
| T3-5d | Несколько позиций одновременно (сейчас одна на инструмент) | L | todo |
| T3-6 | Ввести `code`/`name`/`description` как атрибуты класса; реестр — через декоратор `@register_strategy` (убрать ручные импорты) | S | todo |
| T3-7 | Унифицировать `Trade` (бэктест) и `LiveTrade` (live): общий `TradeRecord` или явный маппер `to_live_trade()` | M | todo |
| T3-8 | Тест-паритет: `detect_signals(...)[-1]` == `check_last_signal(...)` на N инструментах и таймфреймах | M | todo |
| T3-9 | Шаблон новой стратегии: `src/strategies/_template/` (config.py + strategy.py + test) | S | todo |
| T3-10 | Перевести существующие стратегии на v2 (`sma_crossover`, `dual_ma_crossover_after_testing`), сверить PnL до/после на истории | L | done (dual_ma — бит-в-бит на 40 прогонах; sma_crossover изменился осознанно, см. ADR-011) |

**Приёмка T3:** новая стратегия = 2 файла + строка регистрации; `pytest` содержит
тест-паритет; бэктест и live используют один и тот же `ExitRules`.

Проверено после T3-5: `scripts/pnl_baseline.py` — 44 инструмента × 1h/4h/1d = 132
прогона, 1940 сделок. Детерминизм подтверждён (два снимка совпали побайтово по хешам).
PnL по шагам: 327 803.20 → 291 905.42 (SL-first) → 292 815.68 (гэпы) → 283 251.31
(`exit_time`). Число сделок не менялось — правки касались только выходов.

---

## T4. Безопасность (P0/P1)

| ID | Задача | Приоритет | Статус |
|---|---|---|---|
| T4-1 | Не логировать сгенерированный `ACCOUNT_ENCRYPTION_KEY`; при отсутствии ключа — падать с понятной ошибкой | P0 | done |
| T4-2 | Маскировать `account_id` в логах (`***1234`) везде, включая DEBUG | P0 | done |
| T4-3 | Пароль PostgreSQL не в URL-строке: `create_engine(url, connect_args=...)` или `URL.create()`, `hide_parameters=True` | P1 | todo |
| T4-4 | `dashboard`: `debug=False`, host/port из env, по умолчанию `127.0.0.1` | P1 | todo |
| T4-5 | Убрать `DRY_RUN = False` из кода: режим только через env (`TRADING_MODE=dry_run\|live`) с явным подтверждением | P0 | done |
| T4-6 | Ротация ключа шифрования и перешифровка `account_id` (процедура + скрипт) | P2 | todo |

---

## T5. Инфраструктура и тесты

| ID | Задача | Приоритет | Статус |
|---|---|---|---|
| T5-1 | `tests/` + pytest: `BaseStrategy.calculate_stats`, `TradeSimulator`, `PositionManager` (моки), стратегии на синтетике | P0 | in-progress (47 тестов: контракт стратегий, `TradeSimulator` включая модель исполнения, режим торговли, `signal_key`, `allow_short`) |
| T5-2 | GitHub Actions: ruff + mypy + pytest | P1 | todo |
| T5-3 | Dockerfile (multi-stage, `python:3.12-slim`, non-root, healthcheck) | P1 | todo |
| T5-4 | docker-compose: app + postgres для локальной разработки | P1 | todo |
| T5-5 | Alembic вместо ручных `migrations/*.py` | P2 | todo |
| T5-6 | Инициализация git-репозитория, ветка `dev`, защита `main` | P0 (организационно) | in-progress (repo + `dev` созданы; branch protection — в настройках GitHub) |

---

## T6. Документация

| ID | Задача | Статус |
|---|---|---|
| T6-1 | `README.md` — обзор, запуск, env | done |
| T6-2 | `docs/ARCHITECTURE.md` — дерево, схемы, дефекты | done |
| T6-3 | `docs/STRATEGY_CONTRACT.md` — стандарт стратегии | done |
| T6-4 | `docs/DECISIONS.md` — ADR | done |
| T6-5 | `docs/CHANGELOG.md` — история | done |
| T6-6 | Пометить `docs/PROJECT_DOCUMENTATION.md` как устаревший | done |
| T6-7 | `docs/OPERATIONS.md` — runbook: запуск/остановка, инциденты, сверка, восстановление после рестарта | todo |
| T6-8 | Обновлять документацию в том же коммите, что и код (правило проекта) | постоянно |

---

## Рекомендуемый порядок работ

1. **T0-1 … T0-6** — проект запускается, секреты не текут.
2. **T4-1, T4-2, T4-5** — безопасность перед реальной торговлей.
3. **T3-1 … T3-3** — единый движок; с этого момента новые стратегии пишутся быстро.
4. **T5-1** — тесты на движок и стратегии.
5. **T2-5, T2-6, T2-8** — защита денег: идемпотентность, блокировки, walk-forward.
6. **T1-2, T1-3, T2-4** — чистка структуры и переход на PostgreSQL.
7. Остальное — по мере необходимости.
