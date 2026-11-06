# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Версионирование: [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Тест паритета бэктеста и live (T3-8, ADR-027):** `tests/test_backtest_live_parity.py` —
  24 теста на синтетических свечах без сети, токена и PostgreSQL. Сравниваются решения:
  `check_last_signal(prefix)` против `detect_signals(prefix)` на каждом баре (включая
  свечи с нулевыми объёмами), направление, проценты TP/SL из `config/tp_sl.json`, бар
  сигнала в `signal_key`, правило одной позиции, идемпотентность повторного цикла,
  пропуск тикера без политики выходов. Live-путь идёт через настоящий
  `TradingScheduler._process_instrument` и настоящий `TradingOrchestrator`; подменены
  только брокерские компоненты. `KNOWN_DIVERGENCES` — список исключений с полями
  `field`, `reason`, `task`, `review_by`: незарегистрированное расхождение роняет тест,
  временное исключение без даты пересмотра — тоже. Тестами зафиксированы дефекты:
  `entry_filter` (EMA200) вызывается только в бэктесте и live открывает отклонённые
  сделки; фильтр `volume > 0` и порог ≥ 51 бара в live сдвигают индикаторы и бар
  сигнала (оба — T3-8b); база уровней live `last_price` против сырого `open` в бэктесте
  (T3-8a). Прод-код не изменён: паритетные тесты описывают текущее поведение.
  Замер на реальной истории: 88 комбинаций figi/timeframe, 385 333 бара, 0 баров с
  `volume == 0`, 0 комбинаций короче 51 бара — фильтры live на загруженных данных не
  срабатывают, дефект латентный (загрузчик объём не фильтрует). Паритет 1d не
  проверяется: нет записи в `config/tp_sl.json`, блок снимается T3-4a.
- **Дивидендный календарь (T2-22, ADR-025):** `config/dividends.json` v2 (события
  `dividend_gross`, `last_buy_date`, `source: api|manual`, `version` + `sha256`) и
  `src/backtest/dividends.py` — правило отсечки «первый торговый бар строго после
  `last_buy_date`» (weekend-бары участвуют), сопоставление с фактическим падением
  (`gap_match_ratio` 0.3 — санитарный пол, не калибровка), пометка
  `dividend_affected`, покрытие по тикерам и годам. `scripts/fetch_dividends.py` —
  ручная выгрузка `get_dividends` со сверкой тикера по FIGI (несовпадение —
  остановка). PnL и набор сделок не меняются: 929 сделок, +2.71 bps, CI
  [−15.53; +22.42], holdout 228 / −15.83 bps, вердикт `inconclusive` — те же числа,
  что в ADR-023. `dividend_affected` = 0: стратегия выходит раньше отсечки.
  `tests/test_dividends.py` — 27 тестов, включая «известное ограничение» MTSS 2025
  (weekend-бар делит гэп, T2-23).
- `--no-dividends` в `scripts/portfolio_run.py` и `scripts/verdict.py`: прогон без
  календаря для чувствительности.
- `model_revision` в `config/validation.json` (версия 4) и в метаданных отчётов:
  перезапуск на исправленной модели — не новая гипотеза, `n_trials` не растёт.
- Секция `## Dividend coverage` в отчёте мультиинструмента: применённые события,
  список неприменённых (`no_gap_match`) с причиной, тикеры без событий, гэпы >5%,
  которые календарь не объясняет.
- **Единый отчёт-вердикт (T2-21, ADR-023):** `src/backtest/verdict.py` — модель
  входов и упорядоченные гейты (`rejected` → `inconclusive` → `fragile` → `works`),
  `scripts/verdict.py` — сборка входов (пул, holdout, ×2 издержек, `tp_penalty`,
  leave-one-out, heatmap на пуле вокруг торгуемой точки, null-тест), Markdown + JSON.
  Секция `verdict` в `config/validation.json` версии 3 (holdout `2025-10-01…2026-10-01`,
  `min_oos_trades` 100, `cost_multiplier` 2.0, `tp_penalty_bps` 4.0,
  `oos_ratio_min_percent` 50, `n_trials`). Результат: пул 929 сделок +2.71 bps
  CI95 [−15.53; +22.42], holdout 228 сделок −15.83 bps CI95 [−57.26; +32.35],
  плато вокруг 2.5 / 2.0 — `no_plateau` (5/8 соседей), null-тест `ok` →
  **вердикт `inconclusive`** (B3, B4). Повторный прогон совпадает побайтно по всем
  полям снапшота.
- `analyze_plateau_around()` в `src/backtest/heatmap.py`: плато вокруг выбранной
  ячейки, а не вокруг лучшей — гейт C2 мерит то, чем торгуют.
- `scripts/heatmap.py` пишет JSON-снапшот (ячейки + вердикты обоих блоков) —
  машинный вход отчёта-вердикта.
- `tests/test_verdict.py`: 41 тест — каждый гейт отдельно, порядок групп
  (`rejected` бьёт `inconclusive`, `inconclusive` бьёт `fragile`), все причины
  группы печатаются, отсутствующие входы дают гейт, а не ноль, флаги не меняют
  статус, Markdown и JSON.
- `tests/test_heatmap.py`: 5 тестов плато вокруг выбранной ячейки (anchor_missing,
  anchor_thin, отрицательная точка, обрыв у выбранной точки при стабильной лучшей).
- `CLEAN_SET` расширен: `src/backtest/verdict.py`.
- **Leave-one-out в мультиинструментальном отчёте (T2-20a, ADR-022):**
  `leave_one_out_variants()` пересчитывает пул без лучшего тикера (по максимальному
  net ₽, автоматически), без SBER (если он в наборе и не лучший) и без двух лучших
  (при 3+ тикерах), с новым блочным bootstrap для каждого варианта. T2-20: без ALRS
  net падает до −0.17 bps, без ALRS и SNGSP — до −3.07 bps. `fragility_verdict()`:
  положительный вердикт, не выживший без лучшего тикера, понижается до `fragile`
  (правило T2-21). 12 новых тестов, 249 всего.
- **Мультиинструментальный прогон (T2-20, ADR-022):** `src/backtest/portfolio.py`
  (фингерпринты набора и параметров, пул сделок с полем `ticker`, пометка
  `gap_affected`, блочный bootstrap по календарным блокам с пустыми блоками,
  разбивка по тикерам, вклады, пороги), `scripts/portfolio_run.py` (Markdown + CSV +
  JSON-снапшот в `debug/`), `config/portfolio.json` версии 1 (20 ликвидных акций,
  TP 2.5 / SL 2.0, 4h, 100 000 ₽ на тикер — параметры зафиксированы до прогона),
  секция `multi_instrument` в `config/validation.json`. Результат: 929 сделок,
  net +34 864.50 ₽ (+2.71 bps), CI95 [−15.53; +22.42], 15/20 прибыльных тикеров,
  вклад лучшего (ALRS) +83.9% → вердикт `inconclusive`: **edge не доказан**.
  Отчёт печатает `portfolio_version`, `tickers_sha256`, `params_sha256`,
  `costs_version`, `tp_sl_version`, `validation_version`; сырые карты издержек и
  число свечей — только в JSON-снапшоте.
- `tests/test_portfolio.py`: 39 тестов — фингерпринты набора и параметров, хеш
  списка не зависит от порядка, независимость прогонов, пул = сумма прогонов,
  блоки содержат все тикеры периода, пустые блоки сохраняются, детерминизм
  bootstrap, ratio-статистика, тонкие тикеры, вклады при нулевом итоге, гэпы,
  порог 200, три варианта отчёта, CSV.
- `CLEAN_SET` расширен: `src/backtest/portfolio.py`.
- **Heatmap TP/SL (T2-19, ADR-021):** `src/backtest/heatmap.py` (скоринг ячеек в net
  bps, правило плато с условием `best > 0`, исключение тонких ячеек, edge-флаг),
  `scripts/heatmap.py` (Markdown + CSV, два блока: train-окна walk-forward — решение,
  вся история — диагностика), `config/validation.json` версии 1 (пороги: 30 сделок,
  маржа 20%, доля соседей 65%, минимум 3 evaluated-соседа). SBER 4h, сетка
  TP 1…4 × SL 0.5…4, 20 train-окон: лучшая ячейка TP 3 / SL 2.5 — +69.82 bps,
  157 сделок, 3/8 соседей проходят, вердикт `no_plateau` (TP 4% обрывает результат).
  SBER 1d — `no_eligible_cells` (ни одна ячейка не набрала 30 сделок).
- `tests/test_heatmap.py`: 27 тестов — пороги и фингерпринт, скоринг в bps,
  открытые сделки и нулевой размер, все ветки вердикта, исключение тонких соседей
  из знаменателя, `insufficient_neighbours`, edge-флаг, изоляция train-периода,
  дедупликация перекрывающихся окон, отбрасывание warmup-сделок, Markdown и CSV.
- `CLEAN_SET` расширен: `src/backtest/heatmap.py`.
- **Null-тест (T2-18, ADR-020):** `src/backtest/null_test.py` (дрейфless-блуждание,
  прогон по сидам, bootstrap-CI), `src/strategies/_null_entry/` (случайные
  детерминированные входы, в реестр не попадает), `scripts/null_test.py` (CLI, код
  возврата 1 при вердикте `optimism`). Синтетика: `expectancy_net` −166.15 ₽/сделку
  (−17.06 bps) CI [−219.97; −110.74] при издержках −158.69 ₽, вердикт `ok`; контроль
  без издержек и комиссии: CI [−65.10; +50.08], ноль внутри. SBER 4h: net −256.79 ₽
  (−37.93 bps) CI [−309.08; −207.99], `expectancy_price` CI содержит ноль.
  `--gross` требует `COMMISSION_MODE=NONE`. Издержки печатаются в ₽ и bps позиции.
- `tests/test_null_test.py`: 14 тестов — детерминизм, отсутствие дрейфа,
  `high`/`low` вокруг `open`/`close`, изоляция от реестра, bootstrap, три ветки
  вердикта, сходимость слоёв PnL, bps по типам выхода (TP −7 bps, SL −14 bps),
  контроль без издержек, отрицательный интервал.
- `src/backtest/exit_policy.py`: `ExitPolicy` (протокол) + `PercentExitPolicy`;
  `ExitPolicyConfig` с версией и `sha256` (формат `version+sha12`),
  `MissingExitPolicyError`, `resolve_exit_policy()` (T3-4, ADR-019).- `config/tp_sl.json` версии 1: TP/SL по стратегиям — `by_ticker` (dual_ma,
  timeframe → ticker) и `by_timeframe` (sma_crossover, template). Значения
  перенесены из `DEFAULT_CONFIG` 1:1.
- `scripts/trade_set_snapshot.py`: снапшот набора сделок (индексы входа/выхода,
  направление, `exit_type`, `exit_reason`, уровни, три слоя PnL) и строгое
  сравнение `--compare`; допуск только по деньгам — 0.005 ₽.
- `tests/test_exit_policy.py`: 15 тестов политики — резолв из файла, ошибки на
  неизвестный тикер/таймфрейм/стратегию, приоритет явного конфига, «половина
  пары», хеш не зависит от форматирования, уровни от сырого `open` с издержками.
- Отчёты пишут `exit_policy.tp_sl_version` (`pnl_baseline`, `trade_set_snapshot`);
  walk-forward — `tp_sl_source: "grid"` и `tp_sl_grid_sha256`.
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

- Sharpe и Z-score считаются в **порядке реализации PnL**: ключ
  `(exit_time, entry_time)`, а не порядок списка `trades` (T1-4). Окно Sharpe —
  от самого раннего входа до самого позднего выхода по всем сделкам; прежнее
  `closed_trades[-1].exit_time` брало выход последней *открытой* сделки, занижало
  окно и завышало годовой множитель. Ниже `MIN_TRADES_FOR_SHARPE` (10) значение
  `None` с причиной в `sharpe_ratio_reason`; Z-score сохраняет порог 30 и тоже
  пишет причину в `z_score_reason` (`all trades have the same sign`,
  `degenerate denominator`). Дельта: 4 прогона из 10 (`*_1d`, 1–4 сделки) потеряли
  числовой Sharpe — −0.2307, −1.5149, −3.8322, −0.7436 стали `None`; прогоны с
  40+ сделками не изменились (0.3626, 0.2069, −0.5336, 0.2355, 0.8315 — те же),
  потому что симулятор уже выдаёт сделки в хронологическом порядке и окно
  совпадает. На текущих однопозиционных прогонах эффект только в `None` для
  малых выборок. `tests/test_sharpe_zscore.py` — 8 тестов с ручным расчётом
  (Sharpe = 1.5 ровно; Z = −4.8521 на серии, где знаки чередуются по входу, но
  группируются по выходу). `model_revision` **остаётся 2**: отчёты не используют
  ни Sharpe, ни Z-score, ни `total_pnl_percent` — снапшоты `portfolio_run`/`verdict`/
  `pnl_baseline` идентичны, объявлять старые цифры несопоставимыми не за что.
- `StrategyStats.total_pnl_percent_engaged`: доходность к **первоначально
  задействованному** капиталу (`initial_balance × position_size_percent / 100`) рядом
  со старым `total_pnl_percent` (к начальному капиталу счёта). Две базы — два разных
  числа; при `position_size_percent = 100` совпадают (T1-4). База второй —
  первоначальная, без компаундинга, поэтому при прибыльной серии это верхняя оценка.
  `__repr__` печатает обе, README объясняет разницу. `tests/test_strategy_stats.py` —
  5 тестов, включая ручной расчёт при 50% и 25% и защиту от деления на ноль.
  Метрики отчётов не изменились: в `config/portfolio.json` `position_size_percent = 100`,
  дельта `total_pnl_percent` = 0.0 на всех 10 прогонах; `pnl_baseline` `changed=0`,
  `portfolio_run`/`verdict` — снапшоты идентичны.
- `TradeSimulator` принимает `exit_policy` вместо `exit_rules`; `ExitRules` удалён
  (ADR-019).
- `BaseStrategy` резолвит TP/SL через `resolve_exit_policy()`: явные
  `tp_percent`+`sl_percent` в конфиге имеют приоритет, иначе — `config/tp_sl.json`.
- `dual_ma_crossover_after_testing` и `sma_crossover`: таблицы TP/SL удалены из
  `DEFAULT_CONFIG`; `get_config()` больше не подставляет `1.5 / 1.0`.
- `_template`: TP/SL убраны из конфига, шаблон ссылается на `config/tp_sl.json`.
- `CLEAN_SET` расширен: `src/backtest/exit_policy.py`, `src/backtest/null_test.py`,
  `src/strategies/_null_entry`.
- `config/costs.json` версия 2 → 3: 11 явных записей для тикеров набора T2-20  (SNGSP, MTSS, MGNT, CHMF, NLMK, MAGN, RTKM, AFLT, PIKK, TRNFP, IRAO) вместо
  наследования default 4/5 bps. Значения оценочные (шаг цены + премия за
  ликвидность), не из стакана. **Отчёты с `costs_version` v2 несопоставимы с v3:**
  издержки тех же тикеров изменились (ADR-022).
- `config/validation.json` версия 2 → 3: секция `verdict` (пороги гейтов, границы
  holdout, `n_trials`). Фингерпринт в отчётах теперь `3+…`; пороги heatmap и
  `multi_instrument` не менялись.
- `config/validation.json` версия 1 → 2: добавлены секция `multi_instrument` и
  `gap_threshold_percent`. Пороги heatmap не менялись; фингерпринт изменился,
  поэтому `validation_version` в отчётах теперь `2+…`.
- `config/costs.json` версия 1 → 2: `tick_size` CRU6 0.01 → 0.001 (шаг цены MOEX CNY,
  стоимость шага 1 ₽, лот 1000 CNY). Тиковые издержки CRU6 были завышены в 10 раз:
  слой издержек рынка −1 977.56 → −200.58 ₽, net при `MARGIN_SHORT_UNCOVERED=0`
  1 710.47 → 3 475.33 ₽, 11 сделок в обоих прогонах (ADR-018).
- `CLEAN_SET` расширен: `config/commissions.py` (2 замечания ruff исправлены).

### Fixed

- `scheduler.py`: `_process_instrument()` вызывал `check_last_signal()` дважды за цикл —
  до блока `TEST_MODE` и повторно в продакшн-ветке, то есть `detect_signals()` считался
  дважды на каждый тик, а результат первого вызова нигде не использовался. Теперь сигнал
  разрешается один раз, внутри ветки (T1-4). Live-путь, метрики бэктеста не затронуты:
  `pnl_baseline` до/после — `changed=0`, 521 сделка, 124 609.28 ₽. Новый тест
  `tests/test_scheduler_signal_lookup.py` считает вызовы `detect_signals` за цикл
  (на старом коде падает: 2 вызова).
- `scripts/pnl_baseline.py` падал с `KeyError: 'total_pnl'` на любом инструменте без
  записи TP/SL: ветка `no_exit_policy` не записывала `total_pnl`, а `collect()` суммировал
  его безусловно. Дефолтный прогон по 20 инструментам не завершался вовсе (T1-4).
  Пропуски теперь несут `total_pnl = None` (нуль неотличим от реального нулевого
  результата и исказил бы средние), агрегаты — новая `summarize()` — фильтруют по
  `status`, а не по значению; снапшот хранит `skipped_by_status` и полный список
  `skipped`, `compare()` печатает `n/a`, итоговая строка предупреждает и перечисляет
  пропущенные тикеры. Метрики не изменились: ok-прогоны бит-в-бит (521 сделка,
  124 609.28 ₽). Покрытие baseline — **11 из 20** тикеров на 4h: у остальных нет
  `(dual_ma_crossover_after_testing, 4h, ticker)` в `config/tp_sl.json` (T3-4a),
  поэтому сумма относится к неполному набору, а не ко всему портфелю. Тесты 322 → 327.
- Live-путь (`scheduler.py`, `scheduler_notifier.py`) создавал стратегию без
  тикера, поэтому **все** инструменты торговали с фолбэком `TP 1.5% / SL 1.0%`
  вместо значений из конфига. Теперь тикер передаётся, при отсутствии ключа
  пишется `ERROR` и инструмент пропускается.
- Молчаливые дефолты TP/SL удалены: отсутствующий тикер — `MissingExitPolicyError`
  с перечнем доступных ключей.
- `dashboard/compute.py` передаёт тикер при создании стратегии.
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
