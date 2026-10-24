import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from src.market_data.timeframe import Timeframe
# Загрузить переменные из .env
load_dotenv()

from src.brokers.broker_factory import BrokerFactory

def main():
    print("=" * 60)
    print("Trading Robot - First Connection Test")
    print("=" * 60)
    
    # Получить токен и тип брокера из .env
    token = os.getenv("TINKOFF_API_TOKEN")
    broker_type = os.getenv("BROKER_TYPE", "sdk")  # По умолчанию SDK
    
    if not token:
        print("❌ TINKOFF_TOKEN not found in .env")
        return
    
    print(f"✓ Token loaded from .env")
    print(f"✓ Using broker: {broker_type}")
    
    # Создать брокер через фабрику
    print("\nCreating broker...")
    try:
        broker = BrokerFactory.create(broker_type, token)
    except ValueError as e:
        print(f"❌ {e}")
        return
    
    # Получить аккаунты
    print("\nFetching accounts...")
    try:
        accounts = broker.get_accounts()
        
        if not accounts:
            print("❌ No accounts found")
            return
        
        print(f"✓ Found {len(accounts)} account(s):")
        for acc in accounts:
            print(f"  - ID: {acc['id']}")
            print(f"    Name: {acc['name']}")
    
    except Exception as e:
        print(f"❌ Error getting accounts: {e}")
        return
    
    # Загрузить свечи (тест)
    print("\n" + "=" * 60)
    print("Testing candle download...")
    print("=" * 60)
    
    try:
        # Сбербанк (SBER)
        figi = "BBG004730N88"
        timeframe = Timeframe.D1
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=7)
        
        print(f"\nLoading {figi} {timeframe} candles...")
        print(f"Period: {start_time} to {end_time}")
        
        candles = broker.get_candles(figi, timeframe, start_time, end_time)
        
        if not candles:
            print("❌ No candles loaded")
            return
        
        print(f"\n✓ Loaded {len(candles)} candles")
        print(f"\nFirst candle: {candles[0]}")
        print(f"Last candle:  {candles[-1]}")
        
    except Exception as e:
        print(f"❌ Error loading candles: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)

if __name__ == "__main__":
    main()
