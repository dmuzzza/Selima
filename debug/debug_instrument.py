"""
Debug Instrument Data

Скрипт для отладки данных инструмента.
Выводит ВСЕ поля из ShareResponse.
"""

import os
import logging
from dotenv import load_dotenv
from tinkoff.invest import Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

logger = logging.getLogger(__name__)


def convert_quotation(quotation) -> float:
    """Конвертировать Quotation в float."""
    if quotation is None:
        return 0.0
    return float(quotation.units) + float(quotation.nano) / 1e9


def main():
    """Вывести все данные инструмента."""
    
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    
    if not token:
        logger.error("✗ TINKOFF_TOKEN not found in .env")
        return
    
    # FIGI Сбербанка
    SBER_FIGI = "BBG004730N88"
    
    try:
        with Client(token) as client:
            # Получаем инструмент по FIGI
            response = client.instruments.share_by(id_type=1, id=SBER_FIGI)
            
            if response and response.instrument:
                instrument = response.instrument
                
                print("\n" + "=" * 100)
                print(f"📊 ДАННЫЕ ИНСТРУМЕНТА: {instrument.ticker} ({instrument.name})")
                print("=" * 100)
                
                # Базовые данные
                print(f"\n🔹 БАЗОВАЯ ИНФОРМАЦИЯ:")
                print(f"   FIGI:                {instrument.figi}")
                print(f"   Ticker:              {instrument.ticker}")
                print(f"   Name:                {instrument.name}")
                print(f"   ISIN:                {instrument.isin}")
                print(f"   Class Code:          {instrument.class_code}")
                print(f"   Currency:            {instrument.currency}")
                
                # Маржинальные данные (КЛЮЧЕВЫЕ!)
                print(f"\n💰 МАРЖИНАЛЬНЫЕ ПАРАМЕТРЫ:")
                
                # klong и kshort - ставки риска
                klong = convert_quotation(instrument.klong)
                kshort = convert_quotation(instrument.kshort)
                print(f"   klong (LONG):        {klong} (raw units={instrument.klong.units}, nano={instrument.klong.nano})")
                print(f"   kshort (SHORT):      {kshort} (raw units={instrument.kshort.units}, nano={instrument.kshort.nano})")
                
                # dlong и dshort - коэффициенты дисконтирования
                dlong = convert_quotation(instrument.dlong)
                dshort = convert_quotation(instrument.dshort)
                print(f"   dlong (LONG):        {dlong} (raw units={instrument.dlong.units}, nano={instrument.dlong.nano})")
                print(f"   dshort (SHORT):      {dshort} (raw units={instrument.dshort.units}, nano={instrument.dshort.nano})")
                
                # dlong_min и dshort_min - минимальные коэффициенты
                dlong_min = convert_quotation(instrument.dlong_min)
                dshort_min = convert_quotation(instrument.dshort_min)
                print(f"   dlong_min:           {dlong_min}")
                print(f"   dshort_min:          {dshort_min}")
                
                # Флаги
                print(f"\n🚦 ФЛАГИ:")
                print(f"   short_enabled_flag:  {instrument.short_enabled_flag}")
                print(f"   for_qual_investor:   {instrument.for_qual_investor_flag}")
                print(f"   buy_available:       {instrument.buy_available_flag}")
                print(f"   sell_available:      {instrument.sell_available_flag}")
                
                # Лоты
                print(f"\n📦 ЛОТЫ:")
                print(f"   lot:                 {instrument.lot}")
                
                # Пробуем разные формулы расчёта плеча
                print(f"\n🧮 РАСЧЁТ ПЛЕЧА (ПРОВЕРКА ФОРМУЛ):")
                
                if klong > 0:
                    leverage_1 = 1 / klong
                    print(f"   Формула 1: 1 / klong = 1 / {klong} = {leverage_1:.2f}x")
                
                if dlong > 0:
                    leverage_2 = 1 / dlong
                    print(f"   Формула 2: 1 / dlong = 1 / {dlong} = {leverage_2:.2f}x")
                
                if klong > 0:
                    leverage_3 = 1 / (klong / 100)
                    print(f"   Формула 3: 1 / (klong/100) = 1 / ({klong}/100) = {leverage_3:.2f}x")
                
                if dlong > 0:
                    leverage_4 = 1 / (dlong / 100)
                    print(f"   Формула 4: 1 / (dlong/100) = 1 / ({dlong}/100) = {leverage_4:.2f}x")
                
                # Обратный расчёт от плеча x7
                print(f"\n✅ ОЖИДАЕМОЕ ПЛЕЧО: x7")
                print(f"   Обратный расчёт: ставка риска = 100 / 7 = {100/7:.4f}%")
                
                print("\n" + "=" * 100 + "\n")
                
                print("📌 СРАВНИ С ПРИЛОЖЕНИЕМ T-INVEST:")
                print("   1. Открой SBER в приложении")
                print("   2. Найди информацию о плече (x7 для LONG и x7 для SHORT)")
                print("   3. Определи, какая формула даёт правильный результат")
                print()
                
            else:
                print(f"✗ Инструмент {SBER_FIGI} не найден")
                
    except Exception as e:
        logger.error(f"✗ Ошибка: {e}")
        raise


if __name__ == "__main__":
    main()
