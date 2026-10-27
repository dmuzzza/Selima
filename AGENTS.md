# AGENTS.md

Инструкция для AI-агентов и новых участников. Читается **первой**, до любого кода.
Цель файла — дать правила, карту проекта и границы так, чтобы не читать все 147
Python-файлов проекта целиком.

Проект: автоматизированная торговля на Московской бирже через T-Invest API —
сбор свечей в PostgreSQL, бэктест, live-торговля по расписанию, Dash-дашборд.
Python 3.12, `uv`, PostgreSQL, Dash/Plotly, APScheduler.

---

## 0. Маршрут чтения (экономия контекста)

Порядок обязателен, каждый следующий шаг — только если нужен:

| Шаг | Что читать | Зачем |
|---|---|---|
| 1 | этот файл | правила и карта |
| 2 | `docs/TASKS.md` | что уже сделано, что делать |
| 3 | `docs/ARCHITECTURE.md` §2–§5, §9 | точки входа, дерево с описанием файлов, связи, дефекты |
| 4 | `docs/DECISIONS.md` (ADR по теме) | почему сделано именно так, что уже отвергли |
| 5 | `docs/STRATEGY_CONTRACT.md` | если задача про стратегии |
| 6 | код — **точечно** | `grep` по имени класса/функции, затем `read_file` нужного участка |

Правила:

- **Не читать `src/` целиком.** Дерево в `docs/ARCHITECTURE.md` §3 описывает каждый файл;
  этого достаточно, чтобы найти нужный.
- **Не читать** `docs/PROJECT_DOCUMENTATION.md` — устарел, оставлен как история.
- **Не читать** `archive/`, `debug/`, `.venv/`, `vendor/`.
- Файлы длиннее 800 строк (`scheduler.py`, `scheduler_notifier.py`, `app_dashboard.py`,
  `base_strategy.py`) читать диапазонами вокруг найденного символа, не целиком.
- Если задача не про legacy — legacy-файлы не открывать вообще (см. §8).

---

## 1. Команды

`uv` — канонический способ. Если `uv` нет в PATH (бывает на Windows-машинах проекта),
используйте интерпретатор из `.venv`:

| Задача | Команда |
|---|---|
| Установка зависимостей | `uv sync --all-extras` |
| Тесты | `uv run pytest -q` |
| Линтер | `uv run ruff check <пути>` |
| Типы | `uv run mypy --follow-imports=silent <пути>` |
| Дашборд | `uv run python -m dashboard.app` (http://127.0.0.1:8050) |
| Автоторговля | `uv run python scheduler.py` |
| Baseline PnL | `uv run python scripts/pnl_baseline.py --tag before` |
| Walk-forward | `uv run python scripts/walk_forward.py --tag before` |
| Загрузка истории | `uv run python reload_data_v2.py` |

Fallback без `uv`: `.venv\Scripts\python.exe -m pytest -q` (Windows),
`.venv/bin/python -m pytest -q` (Unix).

**Качество перед коммитом — обязательный минимум:**

```bash
ruff check $CLEAN_SET        # CLEAN_SET — см. .github/workflows/ci.yml
mypy --follow-imports=silent $CLEAN_SET
pytest -q                    # все тесты, не только CLEAN_SET
```

Текущее состояние: **143 теста зелёные** (на момент написания файла). Не коммитить
с падающими тестами.

`mypy` в `pyproject.toml` исключает `scripts/` — скрипты не типизируются, но обязаны
проходить `ruff`.

---

## 2. Git

- Репозиторий: `https://github.com/dmuzzza/Selima.git`, рабочая ветка — **`dev`**.
- `main` — только через pull request; прямой push, force-push и merge без команды запрещены.
- **Не выполнять** `git commit`, `push`, `reset`, `rebase`, `tag` без явного «ок»
  пользователя. Перед коммитом показать `git status` + `git diff --stat` и текст сообщения.
- Сообщения — [Conventional Commits](https://www.conventionalcommits.org/):
  `feat|fix|docs|refactor|test|chore|ci(scope): описание (TASK-ID)`.
  Пример: `feat(backtest): add spread, slippage and commission cost model (T2-7)`.
- Один коммит — одна задача. Тело коммита: что было не так, что сделано, какие цифры
  изменились (PnL, число сделок, тесты).
- Теги — только по команде. Версия — SemVer (`pyproject.toml`, сейчас 0.8.1).

---

## 3. Документация обновляется в том же коммите

| Файл | Когда обязателен |
|---|---|
| `README.md` | новая команда, env-переменная, изменился запуск/структура |
| `docs/ARCHITECTURE.md` | новый/удалённый файл (дерево §3), новый компонент (§4), новый дефект (§9) |
| `docs/CHANGELOG.md` | **любое** изменение поведения (`Added`/`Changed`/`Fixed`/`Removed`) |
| `docs/DECISIONS.md` | архитектурный выбор → новый ADR (следующий номер по порядку) |
| `docs/TASKS.md` | статус задачи (`todo` → `done`, с указанием чем закрыта) |
| `docs/STRATEGY_CONTRACT.md` | изменение контракта стратегии или модели исполнения |

Если документация не затронута — явно написать об этом в ответе.

Правила текста: русский язык, без «маркетинга», цифры вместо прилагательных
(«PnL −39%», а не «значительно хуже»). ADR: статус, дата, контекст, решение,
альтернативы, следствия — как в существующих ADR-001…016.

---

## 4. Секреты

**Не открывать, не читать, не выводить, не менять, не коммитить:**
`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `secrets*`, `*.tfvars`,
`kubeconfig`, файлы с токенами.

- В репозитории только `.env.example` без значений.
- Не вставлять секреты в код, логи, `Dockerfile`, `docker-compose`, документацию.
- `account_id` в логах только через `mask_account_id()` (`***1234`).
- `ACCOUNT_ENCRYPTION_KEY` не логировать; отсутствие ключа — падение с понятной ошибкой.
- При подозрении на утечку — сразу сообщить и предложить ротацию.
- **Не запускать** `scheduler.py` с `TRADING_MODE=live`: это реальные ордера.
  По умолчанию `dry_run`; `live` требует ещё и `LIVE_CONFIRM=yes`.

---

## 5. Карта кода

Полное дерево — `docs/ARCHITECTURE.md` §3. Здесь только то, что нужно в 90% задач.

| Модуль | Ответственность | Ключевые имена |
|---|---|---|
| `src/strategies/base_strategy.py` | контракт `Signal`/`Trade`/`StrategyStats`, вся математика PnL и комиссий | `BaseStrategy`, `detect_signals`, `execute_trades`, `_calculate_pnl`, `_calculate_commissions` |
| `src/strategies/strategy_registry.py` | авторегистрация стратегий | `@register_strategy`, `get_strategy_class`, `get_available_strategies` |
| `src/strategies/strategy_factory.py` | создание стратегии по коду | `StrategyFactory.create` |
| `src/strategies/_template/` | шаблон новой стратегии | `TemplateStrategy` |
| `src/strategies/sma_crossover/`, `dual_ma_crossover_after_testing/` | рабочие стратегии | — |
| `src/backtest/simulator.py` | единственный цикл TP/SL | `TradeSimulator`, `ExitRules`, `SizingRules` |
| `src/backtest/costs.py` | спред/проскальзывание/комиссия | `BpsCosts`, `TickCosts`, `load_costs`, `CostModel` |
| `src/backtest/walk_forward.py` | train/test окна, OOS-оценка | `generate_windows`, `run_walk_forward` |
| `src/market_data/` | свечи: модели, таймфреймы, PostgreSQL | `Candle`, `Timeframe`, `HistoryManager` |
| `src/brokers/` | брокер и счета | `AbstractBroker`, `TinkoffSDKBroker`, `BrokerFactory` |
| `src/trading/orchestrator.py` | путь реального ордера | `TradingOrchestrator.process_signals` |
| `src/trading/pending_recovery.py` | разбор зависших PENDING | `PendingRecovery` |
| `src/trading/` (репозитории) | `live_trades`, стоп-ордера, сверка | `PgLiveTradeRepository`, `TradingReconciler` |
| `scheduler.py`, `scheduler_notifier.py` | расписание P0/P1/P2 | `_startup_reconcile`, `monitor_open_positions` |
| `dashboard/` | актуальный дашборд | `app.py`, `ui/layout.py` |
| `config/` | инструменты, издержки, комиссии, лимиты | `instruments.json`, `costs.json`, `commissions.py` |

Поток данных и торговый цикл — `docs/ARCHITECTURE.md` §5–§6 (Mermaid).

---

## 6. Инварианты домена (ломать нельзя)

**Модель исполнения бэктеста** (`src/backtest/simulator.py`, ADR-012):

- вход — по `open` свечи, следующей за сигнальной;
- одна позиция на инструмент одновременно (T3-5d — в работе);
- при касании TP и SL в одной свече побеждает **SL** (внутрибарный порядок неизвестен);
- гэп исполняется по `open`, а не по уровню;
- `exit_time` = `open_time + bar_duration` (конец бара);
- `quantity` — сумма позиции **в рублях**, не число акций.

**Издержки** (`src/backtest/costs.py`, ADR-016, ADR-017):

- `spread_bps` — полный спред, каждая сторона платит половину; круг = один спред;
- проскальзывание — отдельное число на сторону, со спредом не смешивается;
- вход, SL, гэп — маркет (½ спреда + проскальзывание); **TP — лимитка, без издержек**
  (`tp_penalty_bps` — только чувствительность, по умолчанию 0);
- уровни TP/SL строятся от **сырого `candle.open`**, издержки меняют только цену
  исполнения: набор сделок обязан совпадать при любой модели издержек. `levels_from_fill`
  — только для чувствительности (live-конвенция), не для основных прогонов;
- PnL — три слоя: `pnl_price` → `pnl_after_market_costs` → `pnl_net`; смешивать нельзя;
- нулевая модель → `label="GROSS"`, `tradable=false`; GROSS-прогон обязан идти с
  `COMMISSION_MODE=NONE` (иначе это не gross); OOS без издержек отклоняется
  (`--allow-gross` — только осознанно);
- разложение Δ PnL — только через `scripts/cost_audit.py` (обязан сходиться в `residual=0`).

**Маржа** (`config/commissions.py`, ADR-018):

- плата только за **непокрытую** часть позиции: `position_amount - own_funds`; при
  `position_size_percent <= 100` лонг покрыт и не платит;
- непокрытая часть меньше `MARGIN_MIN_UNCOVERED` (5 000 ₽) — платы нет;
- `margin_nights()` — смена календарной даты; внутридневная сделка = 0 ночей;
- `MARGIN_SHORT_UNCOVERED` (env, дефолт `1`): шорт тарифицируется целиком как заём бумаг.
  Дефолт **консервативный и не сверен с отчётом брокера** (T2-17). `0` — только
  чувствительность, не основной прогон. SBER 4h, база — net режима `=0` (20 759.38 ₽):
  маржа 3 760 ₽ = 18.1% базы, полный эффект флага с компаундингом 4 159 ₽ = 20.0% базы;
- режим маржи пишется в каждый снапшот (`describe_commissions()`, рядом с `costs`):
  одно и то же число PnL может быть получено двумя моделями.

**Честность бэктеста** (ADR-015): TP/SL, подобранные на всей истории, — не доказательство.
Любая оценка стратегии — walk-forward с OOS-периодами. `oos_pnl` против `in_sample_pnl`
и `degradation` (oos/is) — обязательные цифры в отчёте. Сейчас у стратегий-образцов
in-sample положительный, OOS отрицательный: **в live они не включаются**.

**Live-торговля**:

- идемпотентность: ключ `figi|timeframe|candle_open_time|direction` в `live_trades.signal_key`
  (частичный уникальный индекс), PENDING-запись создаётся **до** отправки ордера (ADR-010);
- зависшие PENDING разбирает `PendingRecovery` при старте и в P2 (ADR-013): позиция есть →
  OPEN; позиции и операций нет → CANCELLED + ключ освобождается; операция была → остаётся
  PENDING (защита от дубля);
- режим задаётся **только** окружением (`TRADING_MODE`, `LIVE_CONFIRM`), не кодом.

**Стратегии** (`docs/STRATEGY_CONTRACT.md`):

- `code`/`name`/`description` — атрибуты класса (`ClassVar`), не свойства;
- регистрация — `@register_strategy` + пакет внутри `src/strategies`; `__init__.py`
  пакета обязан импортировать `strategy`, иначе декоратор не сработает;
- пакеты с префиксом `_` не регистрируются (`_template`);
- стратегия пишет только `detect_signals()`; `execute_trades`, `check_last_signal`,
  `execute_live_trades` наследуются — переопределение расходит бэктест и live;
- `detect_signals` — чистая: без сети, БД, файлов и `datetime.now()`.

---

## 7. Стиль кода

- Python 3.12, `src`-layout, типы везде (`list[...]`, `X | None`, без `typing.List`).
- `logging`, не `print`. Исключение — CLI-скрипты (`scripts/*`) для отчёта в stdout.
- Явная обработка ошибок; не глотать `except Exception` без лога и обоснования.
- Конфиги — через `config/` и `dict`/pydantic; без литералов в коде (TP/SL по тикерам —
  антипаттерн, T3-4).
- Импорт внутри функции — только для разрыва циклической зависимости, с комментарием.
- Минимальные изменения: не переписывать соседний код «заодно».
- Ruff: `line-length=100`, правила `E,F,I,UP,B,SIM`, `E501` игнорируется.
- Тесты: `tests/test_*.py`, синтетические свечи, без сети и БД; моки вместо клиента.

---

## 8. Legacy: не трогать без задачи

Помечено `[L]` в `docs/ARCHITECTURE.md` §3. Основное:

- `src/sma_crossover/`, `src/candle_patterns/` — копии пакетов из `src/strategies/`;
- `app_dashboard.py` (~1800 строк) — дублирует `dashboard/`;
- `src/strategies/sma_crossover_strategy.py`, `sma_crossover_config.py`,
  `candle_pattern_strategy.py`, `simple_ma_strategy.py`, `levels_detector{,_v4,_v5,_debug}.py`;
- `src/backtest/backtester.py` — свой `Trade`, не связан с `BaseStrategy`;
- `src/storage/models.py`, `optimization/*`, скрипты в корне (`check_data_db.py`,
  `dual_ma_signals*.py`, `test_*.py` и др.).

Правила:

- Не рефакторить legacy «по пути»: это отдельные задачи T1-2, T1-3.
- **`CLEAN_SET` только расширяется.** Список в `.github/workflows/ci.yml` — файлы без
  legacy-долга. Исправили файл — добавили путь; удалять пути нельзя.
- Текущий долг (T1-7, baseline — `docs/TASKS.md`, T1-7): ruff и mypy замечания в legacy-файлах.
  Они не блокируют CI, но и не должны расти. `config/commissions.py` вычищен и добавлен
  в `CLEAN_SET` (ADR-018).

---

## 9. Состояние проекта

Закрыто: T0 (блокеры запуска), T2-5/T2-6/T2-7/T2-7a/T2-8, T3-3/T3-5/T3-6/T3-9/T3-10,
T4-1/T4-2/T4-5, T5-2/T5-6, T6-1…T6-6. Подробности и цифры — `docs/TASKS.md`,
`docs/CHANGELOG.md`.

Следующие по приоритету:

| ID | Задача | Зачем |
|---|---|---|
| T3-4 | `ExitPolicy` + `PercentExitPolicy`, TP/SL по тикерам → конфиг | убрать литералы из стратегий |
| T3-7 | `Trade` / `LiveTrade` → общий `TradeRecord` | две модели сделки расходятся |
| T2-4 | Alembic + перенос старых данных | ручные миграции |
| T2-9 | Sortino/Calmar/VaR/R-multiple | метрики риска |
| T2-10 | JSON-логи, метрики, healthcheck | наблюдаемость |
| T2-17 | сверка маржи с отчётом брокера | `MARGIN_SHORT_UNCOVERED=True` — допущение |
| T5-3/T5-4 | Dockerfile + compose | воспроизводимый запуск |
| T1-2/T1-3 | удалить дубликаты, разнести корень | структура |
| T1-7 | добить ruff/mypy в legacy | расширить `CLEAN_SET` |

---

## 10. Definition of Done

1. `ruff` + `mypy` по `CLEAN_SET` — чисто; `pytest` — все тесты зелёные.
2. Новое поведение покрыто тестом; изменение PnL подтверждено прогоном
   `scripts/pnl_baseline.py` или `scripts/walk_forward.py` **с издержками**.
3. Документация обновлена в том же коммите (§3), ADR — если был выбор.
4. Секреты не тронуты и не залогированы (§4).
5. `CLEAN_SET` расширен, если файл стал чистым.
6. Коммит в `dev` — после явного «ок», сообщение по Conventional Commits.

---

## 11. Антипаттерны (проверены на этом проекте)

| Антипаттерн | Чем плохо |
|---|---|
| Подбор параметров на той же истории, на которой отчёт | завышенный PnL, провал в live (T2-8) |
| Бэктест без издержек | long-only при `COMMISSION_MODE=SHORT_ONLY` платит ноль; SBER 4h: gross-PnL на 64% выше net |
| `print` вместо `logging` в production-коде | нет уровней и структуры |
| TP/SL литералами в стратегии | нельзя отоптимизировать и провалидировать (T3-4) |
| Дублирование `check_last_signal` в стратегии | бэктест и live расходятся |
| `datetime.now()` без таймзоны | naive/aware смешение, сдвиг сделок (T1-5) |
| Импорт внутри функции без причины | скрытые циклы, медленный старт |
| Правка legacy «попутно» | раздувает diff, ломает воспроизводимость |
| `TRADING_MODE=live` в тестах/примерах | реальные ордера |
