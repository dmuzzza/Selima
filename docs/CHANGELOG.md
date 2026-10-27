# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Версионирование: [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- `config/commissions.py`: маржинальная плата по тарифу брокера — только на непокрытую
  часть позиции, порог `MARGIN_MIN_UNCOVERED = 5 000 ₽`, `margin_nights()` (0 для
  внутридневной сделки). Флаг `MARGIN_SHORT_UNCOVERED` (env), по умолчанию `True`:
  шорт тарифицируется целиком, значение консервативное и не сверено с отчётом брокера
  (T2-11). `describe_commissions()` пишет режим и флаг в снапшоты baseline и
  walk-forward рядом с `costs` (ADR-018).
- `scripts/cost_audit.py --chain`: атрибуция Δ PnL по цепочке снапшотов (gross → +market
  → +net), строка `first → last`, `chain drift` и сверка `snapshot total` с суммой
  сделок; бакеты считаются на едином эталонном notional, поэтому порядок шагов не важен
  (ADR-018).
- `tests/test_commissions.py`: 12 тестов модели маржи (внутридневная сделка, ночи,
  покрытие своими средствами, порог 5 000, брекет по непокрытой части, оба режима флага,
  валидация режима комиссий, поля отчёта).
- `tests/test_cost_audit.py`: 3 теста аудита — режим маржи берётся из снапшота (а не из
  env), старый снапшот без блока `commissions` падает на дефолт процесса, инверсия
  bps-сдвига делением не даёт фантомного бакета `price`.
- `tests/test_trade_simulator.py`: инвариант набора сделок для выхода только по гэпам
  (`test_cost_model_does_not_change_gap_only_exits`) — вторая логика выхода, кроме TP/SL.

### Changed

- `config/costs.json` версия 1 → 2: `tick_size` CRU6 0.01 → 0.001 (шаг цены MOEX CNY,
  стоимость шага 1 ₽, лот 1000 CNY). Тиковые издержки CRU6 были завышены в 10 раз:
  слой издержек рынка −1 977.56 → −200.58 ₽, net при `MARGIN_SHORT_UNCOVERED=0`
  1 710.47 → 3 475.33 ₽, 11 сделок в обоих прогонах (ADR-018).
- `CLEAN_SET` расширен: `config/commissions.py` (2 замечания ruff исправлены).

### Fixed

- Маржинальная плата начислялась на всё тело позиции и минимум за один день, из-за чего
  при `position_size_percent <= 100` позиция на собственные средства платила маржу.
  SBER 4h: 3 760 ₽ фиктивной платы при режиме «шорт непокрыт», 0 ₽ — при покрытом.
- `cost_audit.py`: восстановление цены без издержек вычитанием давало фантомный бакет
  `price` (для SBER +4.01 ₽). Теперь деление на `1 + offset`; `attribute()` считает
  ставки на 1 ₽ × единый notional. `residual 0.00`, `drift 0.00` на всех прогонах.
- `cost_audit.py` хардкодил `initial_balance = 50 000`, а у `sma_crossover` он 450 000;
  теперь баланс и `position_size_percent` читаются из снапшота. Добавлено предупреждение,
  если баланс уходит в минус (правил банкротства в движке нет).
- `cost_audit.py` при реплее брал режим маржи из окружения, а не из снапшота, поэтому
  прогон при `MARGIN_SHORT_UNCOVERED=0` разбирался с моделью `=1`. Теперь режим читается
  из `commissions` в файле; печатается `MODEL MISMATCH`, если пересобранный из слоёв net
  не совпадает с суммой сделок (ADR-018).

- `scripts/cost_audit.py`: разложение Δ PnL между двумя снапшотами `--full` на `price`
  (сдвиг уровней/выходов), `size` (компаундинг), `market`, `commission`, `new/lost`;
  сумма сходится в ноль, иначе код возврата 1. Пишет CSV по сделкам (три слоя PnL,
  издержки входа/выхода, комиссия, маржа, `exit_reason`, сдвиг уровня). Режим
  `--oos <walk_forward.json>` считает bootstrap-CI для OOS-expectancy (T2-7a, ADR-017).
- Три слоя PnL в `Trade` и отчётах: `pnl_price` → `pnl_after_market_costs` → `pnl_net`
  (+ `market_cost`, `raw_entry_price`, `raw_exit_price`). `pnl_baseline` пишет их
  отдельными полями (T2-7a, ADR-017).
- `Trade.exit_reason`: `TP` / `SL` / `GAP` / `TIME` рядом с `exit_type` — гэп больше
  не сливается с обычным касанием уровня (T2-7a, ADR-017).
- `BpsCosts.tp_penalty_bps` / `TickCosts.tp_penalty_ticks`: TP исполняется хуже уровня
  на N единиц — модель «касание было, исполнения не было». По умолчанию 0, в
  `tradable` не входит. CLI: `--tp-penalty-bps` (T2-7a).
- `TradeSimulator(levels_from_fill=...)` + `--levels-from-fill`: уровни от цены
  исполнения, как в live. Только для чувствительности; воспроизводит прежнее поведение
  бит-в-бит (T2-7a).
- `walk_forward` пишет `oos_trade_pnl` — per-trade OOS PnL для доверительных интервалов.
- `tests/test_costs.py`: +5 тестов (`tp_penalty`, `COMMISSION_MODE` из env);
  `tests/test_trade_simulator.py`: инвариант набора сделок при издержках, `exit_reason`,
  `levels_from_fill`.

- `AGENTS.md`: правила проекта и карта кода для AI-агентов — маршрут чтения (docs вместо
  сплошного чтения `src/`), команды, git-правила, список секретов, карта модулей,
  инварианты домена (модель исполнения, издержки, идемпотентность live), legacy-границы,
  Definition of Done и антипаттерны (T6-9).
- `src/backtest/costs.py` + `config/costs.json`: модель издержек бэктеста (T2-7, ADR-016).
  Полный спред делится пополам между сторонами, проскальзывание задаётся отдельно на
  сторону, комиссия остаётся третьей строкой. Вход, SL и гэп исполняются как маркет
  (½ спреда + проскальзывание), TP — как лимитка (без издержек). Издержки применяются к
  цене исполнения, а не к уровням TP/SL, поэтому не меняют факт выхода.
  `BpsCosts` для акций, `TickCosts` для фьючерсов и валюты; параметры по инструменту,
  неизвестный тикер наследует `default`.
- Нулевая модель издержек помечается `label="GROSS"` / `tradable=false`; отчёты пишут
  `costs_version` (`version+sha256[:12]`) и параметры модели. `walk_forward.py`
  отказывается запускаться с нулевыми издержками без `--allow-gross`.
- `scripts/pnl_baseline.py`, `scripts/walk_forward.py`: флаги `--spread-bps`,
  `--slippage-bps`, `--costs-file`; сравнение снапшотов предупреждает, если метка
  издержек изменилась (иначе дельта PnL смешивает код и издержки).
- `tests/test_costs.py`: 19 тестов модели издержек; в `tests/test_trade_simulator.py`
  добавлено 6 кейсов (вход, SL, TP-лимитка, гэп, SHORT, нулевые издержки).
- `src/backtest/walk_forward.py`: walk-forward валидация (T2-8, ADR-015). `generate_windows()`
  строит последовательные пары train/test, `run_walk_forward()` отбирает лучший конфиг
  сетки на train и прогоняет **только его** на test; OOS-сделки склеиваются, окна без
  достаточного числа train-сделок пропускаются. Test-период получает `warmup_bars`
  свечей до `test_start` для прогрева индикаторов, сделки из прогрева отбрасываются.
  Runner внедряется, поэтому модуль не зависит от БД и фабрики стратегий.
- `scripts/walk_forward.py`: прогон walk-forward по БД (`config/instruments.json`),
  сетка TP/SL, сводка по инструментам (OOS PnL, in-sample PnL, win rate, `oos/is`)
  и JSON-отчёт в `debug/walk_forward_<tag>.json`.
- `tests/test_walk_forward.py`: 20 тестов — границы и шаг окон, изоляция параметров
  (на test уходит конфиг, выбранный на train), warmup и отбрасывание его сделок,
  пропуск окон, OOS-агрегаты и сериализация отчёта.
- `src/strategies/_template/`: шаблон новой стратегии (`config.py`, `strategy.py`) с
  готовым `detect_signals` и декоратором `@register_strategy` (T3-9). Пакеты с префиксом
  `_` не попадают в реестр, поэтому шаблон не торгуется.
- `tests/test_strategy_registry.py`: 9 тестов — автообнаружение, чтение имени из атрибутов
  класса без создания экземпляра, отказ при пустом/дублирующемся `code`, идемпотентная
  регистрация, изоляция `_template` (проверка в отдельном процессе), контракт шаблона.
- `src/strategies/strategy_registry.py`: авторегистрация стратегий (T3-6, ADR-014).
  `@register_strategy` на классе + ленивое обнаружение пакетов через `pkgutil`;
  ручные импорты стратегий из реестра удалены. `get_available_strategies()` читает
  `name` из атрибутов класса и больше не создаёт экземпляры стратегий.
- `.github/workflows/ci.yml`: CI на push в `dev` и на PR в `dev`/`main` — `ruff`, `mypy`,
  `pytest` (T2-14, T5-2). Проверяется «чистый набор» (`CLEAN_SET`: `simulator.py`,
  `pending_recovery.py`, `tests/`), legacy-файлы добавляются по мере исправления (T1-7).
  `mypy` запускается с `--follow-imports=silent`, так как импортируемые legacy-модули
  пока не аннотированы.
- `src/trading/pending_recovery.py`: `PendingRecovery` — дозакрытие зависших PENDING-сделок
  (хвост T2-6, ADR-013). Сверяет запись с биржей: позиция есть → OPEN с данными из
  портфеля; позиции и операций нет → CANCELLED с освобождением `signal_key`; операция
  была, но позиции нет → остаётся PENDING и требует ручной проверки.
- `PgLiveTradeRepository.get_stale_pending(older_than_minutes=30)`: выборка PENDING-записей
  с занятым `signal_key`, которые не видны ни сборщику ордеров, ни сверке.
- `tests/test_pending_recovery.py`: 5 тестов (усыновление позиции, отмена с освобождением
  ключа, сохранение ключа при наличии операций, отказ API операций, SHORT с отрицательным
  количеством).
- `src/backtest/simulator.py`: единый движок симуляции `TradeSimulator` +
  `ExitRules`/`SizingRules` (T3-3, ADR-011). Один цикл TP/SL для всех стратегий.
- `BaseStrategy.entry_filter()`: хук входа на свече исполнения (доступны `candle.open`
  и значения индикаторов); `BaseStrategy.execute_trades()` стал общей реализацией.
- `tests/test_trade_simulator.py`: 13 тестов движка (вход по open следующей свечи,
  TP/SL для LONG и SHORT, одна позиция, `position_size_percent`, `entry_filter`).
  Добавлены 8 тестов модели исполнения (T3-5): SL-first в одной свече для LONG и SHORT,
  гэпы через SL/TP в обе стороны, `exit_time` = конец бара.
- `tests/test_allow_short_parity.py`: 7 тестов паритета backtest/live по `allow_short`.
- Идемпотентность live-сигналов (T2-5): `build_signal_key()` в
  `src/trading/models.py`, колонка `live_trades.signal_key` + частичный уникальный
  индекс, `PgLiveTradeRepository.create_pending_if_absent()` — атомарная вставка
  PENDING-сделки до отправки ордера (см. ADR-010).
- `migrations/migrate_add_signal_key.py`: идемпотентная миграция PostgreSQL.
- `tests/test_signal_idempotency.py`: 5 тестов (стабильность ключа, микросекунды,
  различие по свече/таймфрейму/направлению, повторный сигнал не создаёт сделку).
- `scripts/pnl_baseline.py`: воспроизводимый baseline PnL — прогон стратегии по всем
  инструментам/таймфреймам из PostgreSQL, JSON-снимок с хешем сделок в `debug/`
  (в `.gitignore`) и режим `--compare before after` для диффа до/после правок движка.
  Свечи читаются только из БД, обращения к брокеру нет.
- Репозиторий инициализирован (`https://github.com/dmuzzza/Selima.git`), рабочая ветка
  `dev` создана от `origin/main`; первый коммит — состояние проекта p_selima_v8.1.
- Защита веток на GitHub (T5-6): `main` — только через pull request, запрет обхода правил
  администратором, force-push и удаления; `dev` — запрет force-push и удаления,
  прямые push разрешены.
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

- **Издержки сдвигали уровни TP/SL (T2-7a, ADR-017).** `TradeSimulator` строил уровни от
  `entry_price`, уже включавшей ½ спреда и проскальзывание. Сдвиг на 4 bps менял, какая
  свеча закрывает сделку, поэтому набор сделок зависел от модели издержек (52 → 53).
  Теперь уровни считаются от сырого `candle.open`; инвариант закреплён тестом.
  SBER 4h: чистые издержки рынка на том же наборе — **−1 652.70 ₽ (−6.9% от gross)**,
  а не −39%; с компаундингом −2 015.50 ₽ (−8.4%).
- **Метка `GROSS` была не gross.** `pnl_abs` всегда вычитал комиссию и маржу, поэтому
  «gross» SBER 4h (18 666.06 ₽) был занижен на 4 613 ₽. `COMMISSION_MODE` читается из
  окружения; `COMMISSION_MODE=NONE` отключает комиссию полностью. Реальный gross —
  23 977.55 ₽.
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

- **Издержки в бэктесте (T2-7, ADR-016, поправка в ADR-017).** `TradeSimulator` принимает
  `costs: CostModel`; вход, SL и гэп исполняются с половиной спреда и проскальзыванием,
  TP — как лимитка. Дефолт (`costs=None`) сохраняет прежние цены, поэтому старые снапшоты
  воспроизводимы, но такой прогон помечается `GROSS`. Итоговый замер SBER 4h после
  исправления уровней и модели маржи (ADR-018): 23 977.55 (gross) → 21 962.05 (издержки
  рынка) → 16 600.73 ₽ (с комиссией и маржой), 52 сделки во всех трёх прогонах.
- `scheduler.py`, `scheduler_notifier.py`: `_startup_reconcile()` и P2
  `monitor_open_positions()` сначала вызывают `PendingRecovery` — зависшие PENDING не
  видны сверке, так как у них нет стоп-ордеров.
- **Модель исполнения `TradeSimulator` (T3-5, ADR-012).** Бэктест больше не завышает
  результат: при касании TP и SL в одной свече исполняется SL; при гэпе (свеча открылась
  за уровнем) выход считается по `open`; `exit_time` — конец бара, а не его открытие.
  Исторические результаты изменились: суммарный PnL по 132 прогонам
  327 803.20 → 283 251.31 ₽ (−13.6%), число сделок не изменилось. Время удержания
  выросло на длительность бара, поэтому маржинальная комиссия считается точнее
  (на `1d` это +1 день к каждой сделке).
- `TradeSimulator.__init__` принимает `bar_duration: timedelta | None`;
  `BaseStrategy.execute_trades()` передаёт длительность бара из `Timeframe`.
- `sma_crossover`, `dual_ma_crossover_after_testing`: удалены собственные
  `execute_trades()` (~130 строк каждая). Логика входа вынесена в `entry_filter()`,
  выход считает `TradeSimulator`.
- `sma_crossover`: исторические результаты бэктеста изменились. Раньше SHORT-сигналы
  симулировались, занимали единственный слот и меняли баланс, а затем выбрасывались из
  статистики; теперь при `allow_short=False` SHORT не открывается вовсе (ADR-011).
  `dual_ma_crossover_after_testing` (`allow_short=True`) не изменился: 40 прогонов,
  PnL и число сделок совпали с прежними.
- `check_last_signal()` и `execute_live_trades()` учитывают `allow_short`: BEARISH
  сигнал не передаётся в live, если стратегия не торгует шорт.
- `BaseStrategy.__init__`: убрано двойное присваивание `self.allow_short` (T1-4).
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
