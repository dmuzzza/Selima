# Контракт стратегии (единый стандарт входов/выходов)

Цель документа: новая стратегия пишется за один файл, а бэктест, дашборд, live-торговля
и аналитика получают её результат одинаково, без правок в потребителях.

Документ фиксирует **два состояния**: `v1` — как в коде сейчас, `v2` — целевой контракт.
Пока идёт миграция, новый код пишется по `v2`, существующие стратегии адаптируются.

---

## 1. Что потребители ожидают от стратегии

| Потребитель | Вызов | Ожидаемый результат |
|---|---|---|
| Бэктест / дашборд | `strategy.run(candles)` | `{"signals": List[Signal], "trades": List[Trade], "stats": StrategyStats}` |
| Scheduler (live) | `strategy.check_last_signal(candles)` | `Signal \| None` |
| Scheduler (live) | `strategy.execute_live_trades(orchestrator, ticker, figi)` | `List[LiveTrade]` |
| Дашборд (отрисовка) | `strategy.signals`, `strategy.trades` | заполнены после `run()` |
| Реестр / UI | `strategy.name`, `strategy.description` | `str` |

Сейчас в `BaseStrategy` объявлены `detect_signals`, `execute_trades`, `name`,
`description`, а также **дефолтные реализации** `check_last_signal` и
`execute_live_trades` (T0-3). Стратегия их не переопределяет — иначе критерий входа в
бэктесте и в live расходится. Копии методов удалены из `src/strategies/sma_crossover/`;
`dual_ma_crossover_after_testing` наследует их от базового класса.

---

## 2. Модели данных (v1, фактические)

### `Signal` — `src/strategies/base_strategy.py:47`

| Поле | Тип | Обязательно | Смысл |
|---|---|---|---|
| `timestamp` | `datetime` | да | время свечи, на которой возник сигнал (`candle.open_time`) |
| `candle_index` | `int` | да | индекс сигнальной свечи в переданном списке |
| `signal_type` | `"BULLISH" \| "BEARISH"` | да | BULLISH → LONG, BEARISH → SHORT |
| `price` | `float` | да | цена закрытия сигнальной свечи |
| `metadata` | `dict` | нет | значения индикаторов для логов/дашборда |

### `Trade` — `src/strategies/base_strategy.py:62`

| Поле | Тип | Смысл |
|---|---|---|
| `trade_id`, `entry_time`, `entry_index`, `entry_price` | `int, datetime, int, float` | вход |
| `signal_type` | `str` | направление |
| `tp_level`, `sl_level`, `quantity` | `float` | уровни и размер позиции в рублях |
| `exit_time`, `exit_index`, `exit_price`, `exit_type` | опциональные | `exit_type`: `"TP" \| "SL"` |
| `breakeven_triggered` | `bool` | SL перенесён в безубыток |
| `pnl_percent`, `pnl_abs` | `float` | net-результат |
| `pnl_gross`, `pnl_net` | `float` | до/после комиссий |
| `commission_open/close/margin/total` | `float` | комиссии |
| `mfe_percent`, `mae_percent` | `float` | макс. благоприятное/неблагоприятное движение за 5 свечей |
| `post_entry_up_move`, `post_entry_down_move` | `float` | движение за 25 свечей |
| `pattern_name` | `str` | для свечных стратегий |

Инварианты:
- `is_open() == (exit_price is None)`;
- `is_long() == (signal_type == "BULLISH")`;
- `quantity` — **сумма позиции в рублях**, не количество акций (важно для комиссий).

### `StrategyStats` — `src/strategies/base_strategy.py:132`

`total_trades`, `closed_trades`, `winning_trades`, `losing_trades`, `win_rate`,
`avg_win`, `avg_loss`, `avg_win_abs`, `avg_loss_abs`, `avg_hold_days`, `total_pnl`,
`total_pnl_percent`, `final_balance`, `expectancy`, `profit_factor`, `payoff_ratio`,
`max_drawdown`, `sharpe_ratio`, `z_score`, `median_long_up_move`, `median_short_down_move`.

Считается один раз в `BaseStrategy.calculate_stats()` — стратегия **не должна**
переопределять этот метод без причины.

### `LiveTrade` — `src/trading/models.py:31`

Отдельная модель для реальной торговли: `trade_id: str`, `position_id`, `lots`,
`lot_size`, `position_size`, `tp_price`/`sl_price`, `status: TradeStatus`,
`account_id` (шифруется при сохранении), `orders_collected`, `metadata`.

---

## 3. Методы стратегии

### 3.1 `detect_signals(candles) -> List[Signal]` — обязательно

Единственная «творческая» часть стратегии.

Требования:
- работает только по переданному списку, без сети и БД;
- не бросает исключение при нехватке данных — возвращает `[]` и пишет `warning`;
- заполняет `candle_index` индексом свечи **в этом списке**;
- заполняет `metadata` значениями индикаторов (для отладки и дашборда);
- идемпотентна: два вызова на одних данных дают одинаковый результат;
- сохраняет результат в `self.signals` (на это рассчитывает дашборд).

Эталон: `src/strategies/dual_ma_crossover_after_testing/strategy.py:167`.

### 3.2 `check_last_signal(candles) -> Optional[Signal]` — обязательно для live

Проверяет только последнюю **закрытую** свечу. Нужен, чтобы не прогонять всю историю
каждые 30 минут.

Требования:
- `candle_index = len(candles) - 1`;
- тот же критерий входа, что в `detect_signals` (иначе бэктест и live расходятся);
- `None`, если данных мало или сигнала нет.

**ДЕФЕКТ v1 (устранён, T0-3):** метод дублировался в каждой стратегии, критерий легко
расходился с `detect_signals`. В `src/sma_crossover/strategy.py` проверка пересечения была
реализована дважды. Теперь реализация одна — в `BaseStrategy`:

```python
def check_last_signal(self, candles: List[Candle]) -> Optional[Signal]:
    signals = self.detect_signals(candles)
    last_index = len(candles) - 1
    return next((s for s in reversed(signals) if s.candle_index == last_index), None)
```

**Ограничение:** фильтры входа, зависящие от цены открытия следующей свечи (например,
`max_adaptive_deviation_percent` в `dual_ma_crossover_after_testing`), на момент сигнала
неизвестны. Live может открыть сделку, которую бэктест отклонил бы. Устраняется в T3-4,
когда фильтры переедут в `ExitPolicy`/`EntryPolicy`.

### 3.3 `execute_trades(candles) -> List[Trade]` — обязательно для бэктеста

Симуляция по сигналам из `self.signals`.

Требования:
- вход по `open` следующей свечи после сигнала (`signal.candle_index == i - 1`);
- проверка TP/SL по `high`/`low` каждой последующей свечи;
- закрытие через `_calculate_pnl(trade, balance)` (комиссии и PnL — только базовый класс);
- сохранение в `self.trades`;
- открытая на конец периода позиция добавляется как `is_open()`.

**ДЕФЕКТ v1:** ~130 строк этого метода продублированы в каждой стратегии
(`dual_ma_crossover_after_testing/strategy.py:302`, `sma_crossover/strategy.py:390`,
`candle_patterns/strategy.py:382`), при этом:
- `position_size_percent` игнорируется (`position_size = current_balance`);
- одновременно допускается только одна позиция;
- `exit_time = candle.open_time`, а не время фактического касания уровня;
- TP проверяется раньше SL в одной свече → систематически оптимистичный результат;
- нет учёта проскальзывания и гэпов (открытие за уровнем).

### 3.4 `execute_live_trades(orchestrator, ticker, figi) -> List[LiveTrade]` — обязательно для live

Тонкая обёртка: `orchestrator.process_signals(self.signals, ticker, figi, tp_percent, sl_percent)`.

**ДЕФЕКТ v1 (устранён, T0-3):** метод копировался дословно в каждой стратегии. Теперь он
реализован в `BaseStrategy` и использует `self.tp_percent`/`self.sl_percent`, которые
базовый класс берёт из конфига (`tp_percent`/`sl_percent`, дефолты 1.5/1.0), а подкласс
может переопределить своими значениями.

---

## 4. Целевой контракт v2 (тонкая стратегия)

Идея: **стратегия отвечает только за сигнал**. Всё, что связано с деньгами и выходом,
делает общий движок. Тогда новая стратегия — это ~60–100 строк.

```python
# src/strategies/base_strategy.py (v2)
class BaseStrategy(ABC):
    code: ClassVar[str]          # "dual_ma_crossover"
    name: ClassVar[str]          # "Dual MA Crossover"
    description: ClassVar[str]

    @abstractmethod
    def detect_signals(self, candles: list[Candle]) -> list[Signal]:
        """Чистая функция. Никакой сети/БД. Возвращает все сигналы истории."""

    def check_last_signal(self, candles: list[Candle]) -> Signal | None:
        """Базовая реализация: последний сигнал, если он на последней свече."""
        signals = self.detect_signals(candles)
        last_idx = len(candles) - 1
        return next((s for s in signals if s.candle_index == last_idx), None)
```

Общий движок (новые модули, не внутри стратегии):

```python
# src/backtest/simulator.py
@dataclass(frozen=True)
class ExitRules:              # задаёт стратегия ИЛИ конфиг
    tp_percent: float
    sl_percent: float
    breakeven: BreakevenRules | None = None
    max_hold_bars: int | None = None

class TradeSimulator:
    def __init__(self, rules: ExitRules, sizing: SizingRules, costs: CostModel): ...
    def run(self, candles: list[Candle], signals: list[Signal]) -> list[Trade]: ...
```

```python
# src/strategies/exit_policy.py
class ExitPolicy(Protocol):
    """Как считать TP/SL. Дефолт — проценты от входа."""
    def build(self, signal: Signal, candle: Candle, ctx: MarketContext) -> ExitRules: ...
```

Стратегия может переопределить `exit_policy`, если выход не процентный (по уровню, ATR),
но **не реализует сам цикл симуляции**.

Что переезжает в базу/движок:

| Сейчас | Куда |
|---|---|
| `execute_trades()` (копия в каждой стратегии) | `TradeSimulator.run()` |
| `check_last_signal()` (копия) | `BaseStrategy.check_last_signal()` |
| `execute_live_trades()` (копия) | `BaseStrategy.execute_live_trades()` |
| `_calculate_pnl`, `_calculate_commissions`, `_apply_breakeven`, `_calculate_mfe_mae`, `_calculate_post_entry_moves` | уже в базе — оставить |
| `calculate_stats()` | уже в базе — оставить |
| TP/SL по тикерам (`dual_ma_crossover_after_testing/config.py`) | `ExitPolicy` + конфиг, валидируемый pydantic |

### Как выглядит новая стратегия после v2

```python
# src/strategies/rsi_reversal/strategy.py
from src.market_data.models import Candle
from src.strategies.base_strategy import BaseStrategy, Signal
from src.strategies.indicators_builder import IndicatorsBuilder


class RSIReversalStrategy(BaseStrategy):
    code = "rsi_reversal"
    name = "RSI Reversal"
    description = "Buy RSI<30 above EMA200, sell RSI>70 below EMA200"

    def __init__(self, timeframe: str, config: dict | None = None, debug: bool = False):
        super().__init__(timeframe, config, debug)
        self.rsi_period = self.config.get("rsi_period", 14)
        self.low = self.config.get("oversold", 30)
        self.high = self.config.get("overbought", 70)

    def detect_signals(self, candles: list[Candle]) -> list[Signal]:
        if len(candles) < self.rsi_period + 2:
            return []
        rsi = IndicatorsBuilder(candles).build_rsi(period=self.rsi_period)
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            prev, curr = rsi[i - 1], rsi[i]
            if prev is None or curr is None:
                continue
            if prev <= self.low < curr:
                signals.append(Signal(candles[i].open_time, i, "BULLISH",
                                      candles[i].close, {"rsi": curr}))
            elif prev >= self.high > curr:
                signals.append(Signal(candles[i].open_time, i, "BEARISH",
                                      candles[i].close, {"rsi": curr}))
        self.signals = signals
        return signals
```

Всё остальное (TP/SL, размер, комиссии, метрики, live, дашборд) работает без изменений.

---

## 5. Чек-лист новой стратегии

1. Пакет `src/strategies/<code>/` с `__init__.py`, `config.py`, `strategy.py`.
2. Класс наследует `BaseStrategy`, задаёт `code`, `name`, `description`.
3. `__init__` вызывает `super().__init__(timeframe, config, debug)` **после** слияния
   дефолтного конфига (см. `get_config()` в существующих пакетах).
4. `detect_signals` — чистая, без сети/БД/`datetime.now()`, возвращает `[]` при нехватке данных.
5. `check_last_signal` не переопределяется (или переопределяется с тем же критерием).
6. `execute_trades` не пишется — используется `TradeSimulator` (после v2).
7. TP/SL берутся из `config` или `ExitPolicy`, не из литералов в коде.
8. Регистрация: добавить код в `STRATEGY_REGISTRY` (`src/strategies/strategy_registry.py`).
9. Тест: `tests/strategies/test_<code>.py` на синтетических свечах —
   (а) сигнал есть/нет на заданном паттерне, (б) число сделок, (в) знак PnL.
10. Прогон `strategy.run(real_candles)` без исключений, `stats.closed_trades > 0`.
11. Проверка `check_last_signal` на последней свече совпадает с последним сигналом
    `detect_signals` (защита от расхождения backtest/live).
12. Обновить `docs/ARCHITECTURE.md` (дерево) и `docs/CHANGELOG.md`.

---

## 6. Антипаттерны, которые нельзя повторять

| Антипаттерн | Пример в текущем коде |
|---|---|
| Бизнес-логика в точке входа | `scheduler._process_instrument` создаёт стратегию на каждый инструмент |
| Конфиг в виде литералов | TP/SL по тикерам в `dual_ma_crossover_after_testing/config.py:32-57` |
| Глобальные переключатели | `DRY_RUN`, `TEST_MODE`, `COMMISSION_MODE` в модулях |
| Импорт внутри функции | `from src.strategies.indicators_builder import IndicatorsBuilder` в методах (`strategy.py:135,154`) |
| Молчаливое проглатывание ошибок | `except Exception: return [None] * len(candles)` в расчёте индикаторов |
| Смешение naive/aware datetime | `datetime.now()` в `orchestrator.py:211` при aware-времени в БД |
| Дублирование модели сделки | `src/backtest/backtester.py:34` — свой `Trade` |
| Сеть в конструкторе | `PositionManager(token, account_id)` создаёт клиента при инициализации |

---

## 7. План миграции на v2 (без остановки торговли)

| Шаг | Действие | Риск |
|---|---|---|
| 1 | ~~Добавить `check_last_signal` и `execute_live_trades` в `BaseStrategy`~~ — **сделано (T0-3)** | — |
| 2 | ~~Удалить копии этих методов из стратегий~~ — **сделано для `src/strategies/sma_crossover/`; остальные копии удаляются в T1-2** | — |
| 3 | Вынести цикл TP/SL в `TradeSimulator`, стратегии вызывают его | средний: сверить PnL до/после на истории |
| 4 | Ввести `ExitRules`/`ExitPolicy`, перенести TP/SL-таблицы в конфиг | средний |
| 5 | Перевести `LiveTrade` и `Trade` на общий `TradeRecord` (или явный маппер) | высокий |
| 6 | Добавить тест-паритет: `detect_signals` vs `check_last_signal`, бэктест vs live-параметры | — |

Критерий завершения: новая стратегия добавляется двумя файлами (`config.py`,
`strategy.py`) + строкой в реестре, и для неё автоматически работают бэктест,
дашборд, live и аналитика.

---

## 8. Требования к тестируемости

- Стратегия не обращается к сети, БД, файлам и системным часам.
- Все параметры — через `config` (dict → pydantic-модель).
- `run(candles)` детерминирован: одинаковый вход → одинаковый `Trade`-список.
- Минимальный набор тестов на стратегию: 1 happy path, 1 «нет сигнала», 1 «мало данных»,
  1 паритет backtest/live.
- Общие тесты (пишутся один раз): `TradeSimulator` (TP/SL/гэп/безубыток/комиссии),
  `BaseStrategy.calculate_stats`, `PositionManager` (моки брокера).
