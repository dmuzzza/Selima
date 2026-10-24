"""Print a Ticker -> FIGI table for a given list of tickers.

Usage:
    TINKOFF_API_TOKEN=... python scripts/tickers_figi_table.py
"""

import os
import sys

from dotenv import load_dotenv
from t_tech.invest import Client


load_dotenv()  # picks up .env from the project root


# Tickers are passed here
TICKERS = ["SBER", "GAZP", "LKOH", "YDEX", "ALRS", "MAGN", "TATN", "GMKN", "NVTK", "SMLT", "X5", "RUAL", "VKCO", "NLMK", "SPBE", "ASTR", "SVCB", "TRNFP", "SGZH", "PHOR", "MTSS", "HEAD", "IRAO", "SELG", "SFIN", "RTKM", "RTKM", "CNRU", "MSNG"]


def main() -> None:
    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        sys.exit("TINKOFF_API_TOKEN not found in .env")

    wanted = {t.upper() for t in TICKERS}

    with Client(token) as client:
        figi_by_ticker = {}
        for share in client.instruments.shares().instruments:
            if share.ticker in wanted:
                figi_by_ticker[share.ticker] = share.figi

    # Table output
    print(f"{'Ticker':<10} | FIGI")
    print("-" * 40)
    for ticker in TICKERS:
        figi = figi_by_ticker.get(ticker.upper(), "not found")
        print(f"{ticker.upper():<10} | {figi}")


if __name__ == "__main__":
    main()
