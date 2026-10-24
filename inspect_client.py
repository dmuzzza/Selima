import os
from dotenv import load_dotenv
from tinkoff.invest import Client

load_dotenv()

token = os.getenv("TINKOFF_API_TOKEN")

if not token:
    print("❌ No token")
    exit()

print("Инспектируем методы Client...")
print("=" * 60)

client = Client(token)

# Выводим все методы Client
methods = [m for m in dir(client) if not m.startswith('_')]

for method in sorted(methods):
    print(f"  - {method}")

print("=" * 60)
