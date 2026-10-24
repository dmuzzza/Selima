"""
Получение последних 30 свечей (таймфрейм 4H) по акции AFLT (Аэрофлот)
через T-Bank Invest API (бывший Tinkoff Invest API).

Установка зависимостей:
    pip install tinkoff-investments python-dotenv

Токен читается из файла .env, лежащего в корне проекта (рядом со скриптом
или в текущей рабочей директории), в виде переменной:
    TINKOFF_API_TOKEN=ваш_токен

Токен должен иметь как минимум права read-only (для получения рыночных
данных достаточно "только чтение").
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
import os

from t_tech.invest import CandleInterval, Client, InstrumentIdType, RequestError
from t_tech.invest.utils import quotation_to_decimal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("aflt_candles")

TICKER = "AFLT"
CLASS_CODE = "TQBR"  # основной режим торгов Мосбиржи для акций
TOP_N = 30
CANDLE_INTERVAL = CandleInterval.CANDLE_INTERVAL_4_HOUR

# Лимит для 4H интервала — до 700 свечей за один запрос, максимальный
# допустимый период запроса — до 3 месяцев. Берём запас в 120 дней,
# чтобы гарантированно набрать 30 последних завершённых и текущую свечу.
LOOKBACK_DAYS = 120

ENV_VAR_NAME = "TINKOFF_API_TOKEN"


def get_token() -> str:
    """Загружает .env из корня проекта и читает токен из переменной окружения."""
    # Ищем .env рядом со скриптом и в текущей рабочей директории
    script_dir_env = Path(__file__).resolve().parent / ".env"
    cwd_env = Path.cwd() / ".env"

    if script_dir_env.exists():
        load_dotenv(dotenv_path=script_dir_env)
    elif cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env)
    else:
        # Фолбэк: пусть dotenv сам попробует найти .env по дереву каталогов
        load_dotenv()

    token = os.getenv(ENV_VAR_NAME)
    if not token:
        logger.error(
            "Не найден токен. Убедитесь, что в .env есть строка "
            "%s=ваш_токен и файл лежит в корне проекта.",
            ENV_VAR_NAME,
        )
        sys.exit(1)
    return token


def resolve_figi(client: Client, ticker: str, class_code: str) -> str:
    """Определяет FIGI инструмента по тикеру и класс-коду."""
    try:
        response = client.instruments.get_instrument_by(
            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
            class_code=class_code,
            id=ticker,
        )
    except RequestError as exc:
        logger.error("Не удалось найти инструмент %s: %s", ticker, exc)
        sys.exit(1)

    instrument = response.instrument
    logger.info(
        "Инструмент найден: %s (%s), FIGI=%s",
        instrument.name,
        instrument.ticker,
        instrument.figi,
    )
    return instrument.figi


def fetch_candles(client: Client, figi: str) -> list:
    """Загружает свечи 4H за последние LOOKBACK_DAYS дней."""
    now = datetime.now(timezone.utc)
    from_ = now - timedelta(days=LOOKBACK_DAYS)

    try:
        candles = list(
            client.get_all_candles(
                figi=figi,
                from_=from_,
                to=now,
                interval=CANDLE_INTERVAL,
            )
        )
    except RequestError as exc:
        logger.error("Ошибка запроса свечей: %s", exc)
        sys.exit(1)
    candles = [c for c in candles if c.is_complete]

    if not candles:
        logger.warning("Свечи не получены — проверьте период или FIGI.")
    return candles


def build_table(candles: list, top_n: int) -> list:
    """Формирует список словарей с нужными полями для последних top_n свечей."""
    last_candles = candles[-top_n:]
    table = []
    for candle in last_candles:
        table.append(
            {
                "time": candle.time.astimezone().strftime("%Y-%m-%d %H:%M"),
                "open": float(quotation_to_decimal(candle.open)),
                "close": float(quotation_to_decimal(candle.close)),
                "high": float(quotation_to_decimal(candle.high)),
                "low": float(quotation_to_decimal(candle.low)),
                "volume": candle.volume,
                "is_complete": candle.is_complete,
            }
        )
    return table


def print_table(rows: list) -> None:
    """Выводит таблицу в консоль в читаемом виде."""
    if not rows:
        print("Нет данных для отображения.")
        return

    headers = ["time", "open", "close", "high", "low", "volume", "is_complete"]
    col_widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}

    header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
    separator = "-+-".join("-" * col_widths[h] for h in headers)
    print(header_line)
    print(separator)
    for row in rows:
        print(" | ".join(str(row[h]).ljust(col_widths[h]) for h in headers))


def main() -> None:
    token = get_token()

    with Client(token) as client:
        figi = resolve_figi(client, TICKER, CLASS_CODE)
        candles = fetch_candles(client, figi)

        if len(candles) < TOP_N:
            logger.warning(
                "Получено всего %d свечей вместо запрошенных %d — "
                "увеличьте LOOKBACK_DAYS при необходимости.",
                len(candles),
                TOP_N,
            )

        table = build_table(candles, TOP_N)
        print_table(table)


if __name__ == "__main__":
    main()