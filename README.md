# p_selima_v8.1 — автоматическая торговая система (T-Invest API)

Система для сбора рыночных данных, бэктестинга торговых стратегий и автоматической
торговли через T-Invest API (Tinkoff/T-Bank) с визуализацией в Dash и запуском по
расписанию через APScheduler.

## Статус

**Закрыты:** блокеры запуска (T0), безопасность режима торговли (T4-1/2/5),
единый движок бэктеста (T2-1/T3-3), модель исполнения симулятора (T3-5),
идемпотентность live-сигналов и восстановление зависших PENDING (T2-5/T2-6),
CI и защита веток (T2-14/T5-2/T5-6). `pytest` — 52 passed.
Репозиторий: `https://github.com/dmuzzza/Selima.git`, рабочая ветка `dev`.

Что ещё не сделано:

- нет `Dockerfile` / `docker-compose.yml` (T5-3, T5-4, T2-15);
- не закрыт P0-риск переобучения: walk-forward вместо подбора TP/SL на всей
  истории (T2-8);
- legacy-мусор в структуре: `src/sma_crossover/`, `app_dashboard.py`, одноразовые скрипты
  в корне (T1-2, T1-3).

Подробный разбор — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), план работ —
[docs/TASKS.md](docs/TASKS.md).

## Что умеет

| Возможность | Модуль |
|---|---|
| Загрузка свечей (30m/1h/4h/1d/1w) в PostgreSQL, инкрементально | `src/market_data/` |
| Торговые стратегии с единым интерфейсом | `src/strategies/` |
| Бэктест (TP/SL, гэпы, комиссии, MFE/MAE, метрики) | `src/backtest/simulator.py`, `src/strategies/base_strategy.py` |
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
├── config/                  # instruments.json, costs.json, tp_sl.json, комиссии, лимиты
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
| `COMMISSION_MODE` | `ALL` / `LONG_ONLY` / `SHORT_ONLY` (по умолчанию) / `NONE` — комиссия в бэктесте и отчётах. `NONE` делает GROSS-прогон действительно gross | нет |
| `MARGIN_SHORT_UNCOVERED` | `1` (по умолчанию) — шорт тарифицируется целиком, как заём бумаг; `0` — денежное обеспечение считается покрытием (режим чувствительности). Дефолт консервативный, с отчётом брокера не сверен (T2-17) | нет |
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
| Baseline PnL бэктеста (до/после правок движка) | `uv run python scripts/pnl_baseline.py --tag before` |
| Walk-forward (OOS-оценка TP/SL) | `uv run python scripts/walk_forward.py --tag before` |
| Бэктест с издержками | `uv run python scripts/walk_forward.py --costs-file config/costs.json --tag net` |
| Разложение издержек по двум снапшотам | `uv run python scripts/cost_audit.py debug/baseline_pnl_a.json debug/baseline_pnl_b.json` |
| Атрибуция по цепочке gross → +market → +net | `uv run python scripts/cost_audit.py --chain debug/baseline_pnl_gross.json debug/baseline_pnl_market.json debug/baseline_pnl_net.json` |
| Bootstrap-CI для OOS-expectancy | `uv run python scripts/cost_audit.py --oos debug/walk_forward_net.json` |
| Набор сделок до/после рефакторинга (индексы + PnL) | `uv run python scripts/trade_set_snapshot.py --tag before --ticker SBER --timeframe 4h`, затем `--compare before after` |
| Null-тест: случайные входы против издержек | `uv run python scripts/null_test.py --tag synthetic` (код 1 при вердикте `optimism`) |
| Heatmap TP/SL: плато или одна точка (Markdown + CSV в `debug/`) | `uv run python scripts/heatmap.py --ticker SBER --timeframe 4h --tag t2-19` |
| Мультиинструмент: одно правило на 20 бумагах (Markdown + CSV + JSON) | `uv run python scripts/portfolio_run.py --tag t2-20` |
| Чувствительность: издержки ×2 / TP-штраф / уровни от цены исполнения | `--spread-bps 4 --slippage-bps 6`, `--tp-penalty-bps 4`, `--levels-from-fill` |
| Тесты | `uv run pytest` |
| Линтер / типы | `uv run ruff check .` / `uv run mypy src` |

`Dockerfile` и `docker-compose.yml` пока отсутствуют (`docs/TASKS.md`, T5-3, T5-4).

## CI

`.github/workflows/ci.yml` запускается на push в `dev` и на PR в `dev`/`main`:

| Шаг | Команда | Область |
|---|---|---|
| Lint | `ruff check $CLEAN_SET` | чистый набор |
| Типы | `mypy --follow-imports=silent $CLEAN_SET` | чистый набор |
| Тесты | `pytest -q` | все |

`CLEAN_SET` — список файлов/каталогов без legacy-долга (T1-7): сейчас
`src/backtest/costs.py`, `src/backtest/exit_policy.py`, `src/backtest/heatmap.py`,
`src/backtest/null_test.py`, `src/backtest/portfolio.py`, `src/backtest/simulator.py`,
`src/backtest/walk_forward.py`, `src/strategies/_null_entry`, `src/strategies/_template`,
`src/strategies/strategy_registry.py`, `src/trading/pending_recovery.py`, `tests`.
При исправлении legacy-файла путь **добавляется** в набор; удалять пути нельзя.

Правила веток: `main` — только через PR (обход правил запрещён и для администратора),
`dev` — прямые push разрешены, force-push и удаление запрещены.

## Документация

| Файл | Содержание |
|---|---|
| [AGENTS.md](AGENTS.md) | правила проекта и карта кода для AI-агентов: маршрут чтения, инварианты, DoD |
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
- **Зависшие PENDING разбираются автоматически.** Если процесс упал между созданием
  PENDING-записи и открытием позиции, `PendingRecovery` при старте и в P2 сверяет запись
  с биржей: позиция есть → OPEN; позиции и операций нет → CANCELLED с освобождением
  ключа; операция была, позиции нет → запись остаётся PENDING и требует ручной проверки.
- **Бэктест без издержек — не доказательство.** `config/costs.json` задаёт спред и
  проскальзывание по инструментам. Отчёт с нулевыми издержками помечается `GROSS` и
  `tradable=false`, а `scripts/walk_forward.py` такие прогоны отклоняет без
  `--allow-gross`. OOS-результат и кандидат в live обязаны считаться с ненулевыми
  издержками (ADR-016).
- **Маржа считается по тарифу брокера.** Плата берётся только с непокрытой части позиции,
  порог 5 000 ₽, внутридневная сделка не платит. Шорт по умолчанию считается непокрытым
  (`MARGIN_SHORT_UNCOVERED=1`) — это консервативное допущение, не сверенное с отчётом
  брокера (T2-17, ADR-018).
- **TP/SL задаются конфигом, а не кодом.** `config/tp_sl.json` содержит блок на
  стратегию: `by_ticker` (таймфрейм → тикер) или `by_timeframe`. Отсутствие ключа —
  ошибка `MissingExitPolicyError`, а не подстановка `1.5 / 1.0`. Файл версионируется
  (`version` + `sha256`), и отчёты пишут `tp_sl_version`, чтобы прогоны с разными
  уровнями нельзя было перепутать (ADR-019). Сейчас `dual_ma` покрывает 21 инструмент
  из 44 на 4h; остальные пропускаются с `ERROR` (T3-4a).
- `TEST_MODE=true` генерирует фиктивные сигналы вместо рыночных.
- `account_id` в логах маскируется (`***1234`); ключ шифрования не логируется.
