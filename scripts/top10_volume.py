"""
Скрипт для получения топ-10 инструментов по объёму торгов.

Выбирает тип инструмента (акции / фьючерсы / валюты и металлы),
загружает дневные свечи за последние N дней и суммирует объём.
Выводит топ-10 в консоль.

Использование:
    python -m scripts.top10_volume
    python -m scripts.top10_volume --type shares --days 7 --top 20
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytz
from dotenv import load_dotenv
from tinkoff.invest import Client
from tinkoff.invest.schemas import CandleInterval, RealExchange

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ФИЛЬТРЫ РОССИЙСКОГО РЫНКА
# ═══════════════════════════════════════════════════════════════════════════

# Страна риска для российских инструментов
RUS_COUNTRY_NAMES = {"Российская Федерация", "Россия", "Russian Federation", "Russia"}


def _is_available_for_trade(instr) -> bool:
    """Инструмент доступен для торговли через API."""
    return getattr(instr, "api_trade_available_flag", False) is True


def _is_moex(instr) -> bool:
    """Инструмент торгуется на Московской бирже."""
    return getattr(instr, "real_exchange", None) == RealExchange.REAL_EXCHANGE_MOEX


def _is_not_qual_only(instr) -> bool:
    """Инструмент НЕ требует статуса квал-инвестора."""
    return getattr(instr, "for_qual_investor_flag", False) is False


def _is_russian(instr) -> bool:
    """Страна риска инструмента — Россия."""
    country = getattr(instr, "country_of_risk_name", "")
    return country in RUS_COUNTRY_NAMES


# ═══════════════════════════════════════════════════════════════════════════
# ВЫБОР ТИПА ИНСТРУМЕНТА
# ═══════════════════════════════════════════════════════════════════════════

INSTRUMENT_TYPES = {
    "shares": "Акции",
    "futures": "Фьючерсы",
    "currencies": "Валюты и металлы",
}


def show_menu() -> str:
    """Показать меню выбора типа инструмента."""
    print("\n" + "=" * 60)
    print("  ВЫБОР ТИПА ИНСТРУМЕНТА")
    print("=" * 60)
    for idx, (key, label) in enumerate(INSTRUMENT_TYPES.items(), 1):
        print(f"  {idx}. {label}")
    print("=" * 60)

    while True:
        choice = input("\n  Введите номер (1-3): ").strip()
        if choice in ("1", "2", "3"):
            key = list(INSTRUMENT_TYPES.keys())[int(choice) - 1]
            label = INSTRUMENT_TYPES[key]
            print(f"\n  ✓ Выбрано: {label}")
            return key
        print("  ⚠ Введите число от 1 до 3.")


# ═══════════════════════════════════════════════════════════════════════════
# ПОЛУЧЕНИЕ СПИСКА ИНСТРУМЕНТОВ
# ═══════════════════════════════════════════════════════════════════════════

def get_instruments(client: Client, instrument_type: str) -> List[Dict[str, str]]:
    """
    Получить список инструментов указанного типа из Tinkoff API.
    Только российские, доступные для торгов через API, на MOEX, не для квалов.

    Args:
        client: Tinkoff API Client
        instrument_type: "shares" | "futures" | "currencies"

    Returns:
        List[dict]: [{"figi": ..., "ticker": ..., "name": ...}, ...]
    """
    instruments = []

    try:
        if instrument_type == "shares":
            response = client.instruments.shares(instrument_status=1)
        elif instrument_type == "futures":
            response = client.instruments.futures(instrument_status=1)
        elif instrument_type == "currencies":
            response = client.instruments.currencies(instrument_status=1)
        else:
            raise ValueError(f"Unknown instrument type: {instrument_type}")

        total = 0
        filtered = 0

        for instr in response.instruments:
            total += 1

            # 🔍 Фильтрация: российский рынок + доступен для торгов
            if not _is_available_for_trade(instr):
                continue
            if not _is_moex(instr):
                continue
            if not _is_not_qual_only(instr):
                continue
            # Для акций дополнительно проверяем страну риска
            # (у валют country_of_risk_name часто пустое, для них достаточно MOEX)
            if instrument_type == "shares":
                if not _is_russian(instr):
                    continue

            filtered += 1
            instruments.append({
                "figi": instr.figi,
                "ticker": instr.ticker,
                "name": instr.name,
            })

        logger.info(
            f"  Всего получено: {total}, "
            f"после фильтрации (РФ, MOEX, доступны): {filtered}"
        )

    except Exception as e:
        logger.error(f"Failed to fetch {instrument_type}: {e}")
        raise

    return instruments


# ═══════════════════════════════════════════════════════════════════════════
# РАСЧЁТ СУММАРНОГО ОБЪЁМА
# ═══════════════════════════════════════════════════════════════════════════

def calculate_volume(
    client: Client,
    figi: str,
    days_back: int,
) -> int:
    """
    Загрузить дневные свечи за последние N дней и посчитать суммарный объём.

    Args:
        client: Tinkoff API Client
        figi: FIGI инструмента
        days_back: Сколько дней назад смотреть

    Returns:
        int: Суммарный объём (в лотах)
    """
    try:
        msk_tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(msk_tz)
        end_time = now
        start_time = end_time - timedelta(days=days_back)

        response = client.market_data.get_candles(
            figi=figi,
            from_=start_time,
            to=end_time,
            interval=CandleInterval.CANDLE_INTERVAL_DAY,
        )

        candles = response.candles
        if not candles:
            return 0

        total_volume = sum(c.volume for c in candles)
        return total_volume

    except Exception as e:
        logger.debug(f"  ⚠️ {figi}: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# ОСНОВНАЯ ЛОГИКА
# ═══════════════════════════════════════════════════════════════════════════

def run(
    instrument_type: str,
    days_back: int = 5,
    top_n: int = 10,
    show_all: bool = False,
) -> List[Dict[str, Any]]:
    """
    Основная точка входа.

    Args:
        instrument_type: "shares" | "futures" | "currencies"
        days_back: Количество дней для анализа объёма
        top_n: Сколько результатов показать
        show_all: Показать все инструменты, а не только топ

    Returns:
        List[dict]: Результаты с полями ticker, name, figi, volume
    """
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise RuntimeError("TINKOFF_API_TOKEN not found in environment")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    label = INSTRUMENT_TYPES[instrument_type]

    with Client(token) as client:
        # 1. Получить список инструментов
        logger.info(f"Загружаем список инструментов: {label}")
        instruments = get_instruments(client, instrument_type)
        logger.info(f"✓ Найдено {len(instruments)} инструментов")

        if not instruments:
            print("\n❌ Инструменты не найдены.")
            return []

        # 2. Для каждого инструмента считаем объём
        logger.info(
            f"Считаем суммарный объём торгов за последние {days_back} дн. "
            f"(всего {len(instruments)} шт.)..."
        )

        results: List[Dict[str, Any]] = []
        total = len(instruments)

        for idx, instr in enumerate(instruments, 1):
            vol = calculate_volume(client, instr["figi"], days_back)
            results.append({
                "ticker": instr["ticker"],
                "name": instr["name"],
                "figi": instr["figi"],
                "volume": vol,
            })

            if idx % 20 == 0 or idx == total:
                logger.info(f"  Прогресс: {idx}/{total}")

        # 3. Сортировка по объёму (убывание)
        results.sort(key=lambda x: x["volume"], reverse=True)

        # 4. Вывод
        items_to_show = results if show_all else results[:top_n]

        print("\n" + "=" * 80)
        print(f"  ТОП-{min(top_n, len(items_to_show))} {label.upper()} ПО ОБЪЁМУ ТОРГОВ")
        print(f"  за последние {days_back} дн. (дневные свечи)")
        print("=" * 80)
        print(
            f"  {'#':<4} {'Тикер':<10} {'Название':<40} {'FIGI':<20} {'Объём':>18}"
        )
        print("  " + "-" * 76)

        for rank, item in enumerate(items_to_show, 1):
            vol_str = f"{item['volume']:,}"
            name_trunc = item["name"][:38] if item["name"] else "-"
            print(
                f"  {rank:<4} "
                f"{item['ticker']:<10} "
                f"{name_trunc:<40} "
                f"{item['figi']:<20} "
                f"{vol_str:>18}"
            )

        # Статистика
        total_vol = sum(r["volume"] for r in results)
        non_zero = sum(1 for r in results if r["volume"] > 0)
        print("\n" + "=" * 80)
        print(
            f"  Всего инструментов: {len(results)} | "
            f"С ненулевым объёмом: {non_zero}"
        )
        print(f"  Суммарный объём по всем: {total_vol:,.0f}")
        print("=" * 80)

        return items_to_show


# ═══════════════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Топ инструментов по объёму торгов"
    )
    parser.add_argument(
        "--type", type=str, default=None,
        choices=list(INSTRUMENT_TYPES.keys()),
        help="Тип инструмента: shares, futures, currencies. Если не указан — меню.",
    )
    parser.add_argument(
        "--days", type=int, default=5,
        help="Количество дней для расчёта объёма (по умолчанию: 5)",
    )
    parser.add_argument(
        "--top", type=int, default=10,
        help="Сколько инструментов показать (по умолчанию: 10)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Показать все инструменты, а не только топ",
    )

    args = parser.parse_args()

    instrument_type = args.type
    if instrument_type is None:
        instrument_type = show_menu()

    run(
        instrument_type=instrument_type,
        days_back=args.days,
        top_n=args.top,
        show_all=args.all,
    )


if __name__ == "__main__":
    main()