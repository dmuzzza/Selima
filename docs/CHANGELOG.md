# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Версионирование: [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- Идемпотентность live-сигналов (T2-5): `build_signal_key()` в
  `src/trading/models.py`, колонка `live_trades.signal_key` + частичный уникальный
  индекс, `PgLiveTradeRepository.create_pending_if_absent()` — атомарная вставка
  PENDING-сделки до отправки ордера (см. ADR-010).
- `migrations/migrate_add_signal_key.py`: идемпотентная миграция PostgreSQL.
- `tests/test_signal_idempotency.py`: 5 тестов (стабильность ключа, микросекунды,
  различие по свече/таймфрейму/направлению, повторный сигнал не создаёт сделку).
- Репозиторий инициализирован (`https://github.com/dmuzzza/Selima.git`), рабочая ветка
  `dev` создана от `origin/main`; первый коммит — состояние проекта p_selima_v8.1.
- `.gitattributes`: LF в репозитории и рабочей копии, бинарные типы для `.whl`/`.xlsx`/изображений.
- `.gitignore` дополнен: `.coverage*`, `htmlcov/`, `.ipynb_checkpoints/`, `*.parquet`,
  `*.orig`, `*.rej`, `*.xlsx` (артефакты тестов и оптимизации).
- `README.md`: назначение, стек, структура, запуск, список env-переменных без значений.
- `docs/ARCHITECTURE.md`: дерево проекта с описанием файлов, Mermaid-схемы компонентов и
  последовательности торгового цикла, схема БД, перечень дефектов с привязкой к строкам.
- `docs/STRATEGY_CONTRACT.md`: стандарт стратегии — модели `Signal`/`Trade`/`LiveTrade`,
  требования к `detect_signals`/`check_last_signal`/`execute_trades`/`execute_live_trades`,
  целевой контракт v2, чек-лист новой стратегии, антипаттерны, план миграции.
- `docs/TASKS.md`: бэклог с приоритетами P0–P2 и критериями приёмки.
- `docs/DECISIONS.md`: ADR-001…ADR-008.
- `docs/CHANGELOG.md`: этот файл.
- `.env.example`: шаблон переменных окружения без значений.
- `.gitignore`, `.dockerignore`.
- `pyproject.toml`: Python 3.12, runtime-зависимости, extras `optimization`, группа `dev`,
  конфигурация ruff/mypy/pytest.
- `uv.lock`: зафиксированные версии (70 пакетов).
- `requirements.txt`: генерируется из lock (`uv export`), для Docker/CI.
- `vendor/t_tech_investments-1.51.0-py3-none-any.whl`: SDK T-Invest (официальный индекс
  `opensource.tbank.ru` временно недоступен).
- `src/utils/masking.py`: `mask_account_id()` — единая маскировка идентификатора счёта
  для логов (`***1234`).
- `tests/test_strategy_contract.py`: 6 тестов на контракт `BaseStrategy` (наследование
  live-методов, паритет `check_last_signal` ↔ `detect_signals`, пустой/короткий ряд).
- `config/trading_mode.py`: `TRADING_MODE` (`dry_run`|`live`) + `LIVE_CONFIRM`,
  `resolve_trading_mode()`; по умолчанию `dry_run`.
- `tests/test_trading_mode.py`: 8 тестов на разбор режима.

### Fixed

- Подключение к PostgreSQL: SQLAlchemy 2.1 по умолчанию использует драйвер `psycopg`
  (v3), которого нет в зависимостях; установлен `psycopg2-binary`. URL теперь явно
  задаёт драйвер: `postgresql+psycopg2://` (`history_manager.py`, `pg_repository.py`,
  `migrations/migrate_add_signal_key.py`). До исправления любой доступ к БД падал с
  `ModuleNotFoundError: No module named 'psycopg'`.
- `PgLiveTradeRepository.__init__` больше не выполняет DDL при каждом старте: наличие
  `signal_key` и индекса проверяется через системные каталоги, `ALTER TABLE` вызывается
  только если объект отсутствует. На рабочей БД роль приложения (`user_lens`) не владелец
  таблиц, поэтому безусловный `ALTER` ломал запуск (`must be owner of table live_trades`).
- `migrations/migrate_add_signal_key.py`: читает `.env`, проверяет обязательные `PG_*`
  и вырезает пароль из текста ошибки перед логированием.
- Повторный вход по одному и тому же сигналу: если позиция закрывалась по TP/SL,
  планировщик мог открыть её снова на той же закрытой свече. Теперь `signal_key`
  блокирует повторную обработку (T2-5).
- Повторный вход после рестарта процесса: запись PENDING создаётся до отправки ордера,
  поэтому уже обработанный сигнал виден после перезапуска (T2-6, частично).
- `scheduler.py`: `DRY_RUN` больше не «пустышка» — раньше флаг только логировался и
  никогда не блокировал сделки. Теперь в `dry_run` ордера не отправляются вовсе.
- `scheduler_notifier.py`: удалён `LIVE_TRADING = True` (реальные сделки были включены
  жёстко); поведение определяется `TRADING_MODE`.
- `scheduler.py`: из логов убран префикс API-токена.
- `scheduler.py`, `scheduler_notifier.py`: импорт переключён на существующий пакет
  `src.strategies.dual_ma_crossover_after_testing`; `import scheduler` проходит.
- `src/utils/crypto.py`: ключ `ACCOUNT_ENCRYPTION_KEY` больше не генерируется и не
  пишется в лог при отсутствии; вместо этого `ValueError` с командой генерации.
- Полный `account_id` больше не попадает в логи: маскирование применено в
  `src/trading/account_selector.py`, `src/trading/orchestrator.py`,
  `src/brokers/position_manager.py`, `src/brokers/trade_executor.py`,
  `src/brokers/account_info.py`.

### Changed

- `scheduler.py`: сделки и стоп-ордера пишутся в PostgreSQL
  (`PgLiveTradeRepository` / `PgStopOrderRepository`), SQLite из production-пути убран.
  Репозитории создаются один раз за процесс (`_trade_repositories()`).
- `TradingOrchestrator` принимает готовый репозиторий (`repository=`); путь к SQLite
  остаётся дефолтом для legacy-скриптов.
- `scheduler_notifier.py`: удалён дублирующий класс `PostgresTradingOrchestrator`
  (его роль выполняет `TradingOrchestrator(repository=...)`); вместе с ним ушло
  логирование полного `account_id`.
- `TradingReconciler`: `trades_db_path` стал необязательным — репозитории передаются
  явно.
- Документация приведена в соответствие с кодом: `README.md` (раздел «Статус» больше не
  заявляет о незакрытых блокерах T0-3), `docs/ARCHITECTURE.md` §9 (пункт про импорт
  отсутствующего пакета помечен как исправленный, оставшиеся скрипты вынесены в п. 22a),
  `docs/TASKS.md` (разделены слипшиеся строки T1-6 и T1-7).
- `BaseStrategy` расширен дефолтными `check_last_signal()` и `execute_live_trades()`;
  `tp_percent`/`sl_percent` читаются из конфига (дефолты 1.5/1.0). Копии этих методов
  удалены из `src/strategies/sma_crossover/strategy.py` — контракт стал единым.
- Зависимости зафиксированы: проект ставится через `uv sync --all-extras`; ранее версии
  нигде не фиксировались, часть пакетов отсутствовала в окружении.
- `docs/PROJECT_DOCUMENTATION.md` помечен как устаревший (оставлен как исторический
  контекст, актуальная документация — `docs/ARCHITECTURE.md`).
- `src/strategies/strategy_registry.py`: удалены 9 импортов отсутствующих пакетов
  стратегий; в реестре остались `sma_crossover` и `dual_ma_crossover_after_testing`.
  Отсутствующие коды перечислены в TODO-комментарии (см. ADR-008).
- Добавлены отсутствующие `__init__.py`: `src/`, `src/strategies/`, `src/backtest/`,
  `src/storage/` — пакеты больше не полагаются на namespace-импорт.

### Removed

- Из PostgreSQL удалены 28 устаревших записей `live_trades` (17 `OPEN`, 11 `FAILED`),
  зашифрованных утраченным `ACCOUNT_ENCRYPTION_KEY`; сделки на бирже были закрыты.
  Бэкап перед удалением — `live_trades_backup.csv` (локально, в `.gitignore`).
  Таблица `stop_orders` на момент удаления была пуста.

### Known issues

Зафиксированы, не исправлены (см. `docs/TASKS.md`):

- Дублирование кода: `src/sma_crossover/` ≡ `src/strategies/sma_crossover/`;
  `app_dashboard.py` vs пакет `dashboard/`; `scheduler.py` vs `scheduler_notifier.py`.
- Нет CI и Docker; `tests/` покрывает только контракт стратегий.
- Смешаны naive/aware datetime; `TEST_MODE` в `scheduler.py` и тестовые тикеры
  захардкожены (сам флаг уже читается из env).
- Фильтры входа, зависящие от цены открытия следующей свечи, применяются в
  `execute_trades`, но не в `check_last_signal` — возможное расхождение backtest/live.

## [0.8.1] — базовая версия (без истории изменений)

Состояние проекта до введения документации. Версия присвоена по имени каталога
(`p_selima_v8.1`); предыдущая история изменений не велась.
