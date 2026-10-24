# check_db.py
import sqlite3

conn = sqlite3.connect("data/live_trades.db")
cursor = conn.cursor()

print("📊 Открытые сделки:")
cursor.execute("""
    SELECT trade_id, entry_price, lots, lot_size, tp_price, sl_price, position_size
FROM live_trades
WHERE ticker = 'PIKK'
ORDER BY id DESC LIMIT 3;
     
    
""")
for row in cursor.fetchall():
    print(row)
    print(f"  {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} | {row[7]} | {row[8]} | {row[22]}")

print("\n🛑 Стоп-ордера:")
cursor.execute("""
    SELECT * 
    FROM stop_orders
""")

# rows = cursor.fetchall()
# if rows:
#     print(rows)
#     print(f"Найдено: {len(rows)} записей\n")
#     for row in rows:
        
#         print(f"  Order: {row[0]}")
#         print(f"    Trade ID: {row[1]}")
#         print(f"    Position ID: {row[2]}")
#         print(f"    Type: {row[3]} | Ticker: {row[4]} | Price: {row[5]:.2f}₽")
#         print()
# else:
#     print("❌ Нет записей")

# conn.close()




import sqlite3
conn = sqlite3.connect("marketdata.db")
cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='market_data'")
print(cursor.fetchone()[0])

# from dotenv import load_dotenv

# from tinkoff.invest import Client
# import os
# load_dotenv()
# TOKEN = os.getenv("TINKOFF_API_TOKEN")  # замените на ваш токен
# tickers = ['GAZP', 'MGNT', 'POSI', 'OZON', 'SNGSP', 'AFKS', 'CHMF', 'EUTR', 'FLOT', 'SBER', 'MOEX', 'PIKK', 'VTBR', 'T', 'NVTK', 'AFLT', 'LKOH', "X5"]
# figies = ['TCS80A107UL4', 'BBG00475KKY8']

# # Вариант 2: Для боевого режима (если у вас боевой токен)
# with Client(TOKEN) as client:
#     instruments = client.instruments
    
#     for ticker in tickers:
#         try:
#             response = instruments.shares(instrument_status=1)
#             for share in response.instruments:
#                 if share.ticker == ticker:
#                     print(f"{ticker}: {share.figi} - {share.name}")
#                     break
#             else:
#                 print(f"{ticker}: не найден")
            

#         except Exception as e:
#             print(f"Ошибка для {ticker}: {e}")
    
#     print('-----------------------------------------------------')

#     for figi in figies:
#         try:
#             response = instruments.shares(instrument_status=1)
#             for share in response.instruments:
#                 if share.figi == figi:
#                     print(f"{figi}: {share.ticker} - {share.name}")
#                     break
#             else:
#                 print(f"{figi}: не найден")
            

#         except Exception as e:
#             print(f"Ошибка для {ticker}: {e}")




# import os
# import json
# from datetime import datetime, timezone

# from dotenv import load_dotenv
# from tinkoff.invest import Client

# load_dotenv()
# TOKEN = os.getenv("TINKOFF_API_TOKEN")

# # Исходные списки (можно расширять)
# tickers = [
#     'GAZP', 'MGNT', 'POSI', 'OZON', 'SNGSP', 'AFKS', 'CHMF', 'EUTR',
#     'FLOT', 'SBER', 'MOEX', 'PIKK', 'VTBR', 'T', 'NVTK', 'AFLT', 'LKOH', "X5"
# ]
# # figies = ['TCS80A107UL4', 'BBG00475KKY8']

# # Статические поля для всех инструментов
# ENABLED = True
# TIMEFRAMES = ["1h", "4h", "1d"]

# def main():
#     instruments_data = {}  # ключ – figi, значение – запись для JSON

#     with Client(TOKEN) as client:
#         # Получаем все торгуемые акции один раз
#         response = client.instruments.shares(instrument_status=1)
#         shares = response.instruments

#         # Индексы для быстрого поиска
#         by_ticker = {share.ticker: share for share in shares}
#         by_figi   = {share.figi: share   for share in shares}

#         # Обрабатываем тикеры
#         for ticker in tickers:
#             share = by_ticker.get(ticker)
#             if share:
#                 instruments_data[share.figi] = {
#                     "figi": share.figi,
#                     "ticker": share.ticker,
#                     "enabled": ENABLED,
#                     "timeframes": TIMEFRAMES
#                 }
#             else:
#                 print(f"Предупреждение: тикер {ticker} не найден")

#         # Обрабатываем figi
#         # for figi in figies:
#         #     share = by_figi.get(figi)
#         #     if share:
#         #         instruments_data[share.figi] = {
#         #             "figi": share.figi,
#         #             "ticker": share.ticker,
#         #             "enabled": ENABLED,
#         #             "timeframes": TIMEFRAMES
#         #         }
#         #     else:
#         #         print(f"Предупреждение: FIGI {figi} не найден")

#     # Сортируем записи по тикеру для удобства
#     instruments_list = sorted(instruments_data.values(), key=lambda x: x["ticker"])

#     # Формируем итоговую структуру
#     output = {
#         "version": "1.0",
#         "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
#         "description": "Trading instruments configuration",
#         "instruments": instruments_list
#     }

#     # Записываем в файл с красивым форматированием
#     with open("instruments.json", "w", encoding="utf-8") as f:
#         json.dump(output, f, ensure_ascii=False, indent=2)

#     print(f"\nФайл instruments.json успешно создан. Добавлено инструментов: {len(instruments_list)}")

# if __name__ == "__main__":
#     main()