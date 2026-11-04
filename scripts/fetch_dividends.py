"""Fetch the dividend calendar from the T-Invest API into config/dividends.json.

Manual, never part of a backtest or CI run: the backtest reads the JSON file only,
so a report stays reproducible without network access.

    uv run python scripts/fetch_dividends.py                     # dry run, prints JSON
    uv run python scripts/fetch_dividends.py --write             # update the config
    uv run python scripts/fetch_dividends.py --tickers SBER SNGSP --write

The token is read from `TINKOFF_API_TOKEN` (environment or .env) and is never
printed. Manual events already present in the file are preserved: the API does not
return the full history for every instrument, and a gap must not be filled by
silently dropping a hand-checked entry.

API notes (verified against `sdk_t_bank_docs/services/instruments/methods.mdx`
and the installed SDK 1.51.0):

- `InstrumentsService.get_dividends(*, figi, from_, to, instrument_id)` filters by
  `record_date`; the SDK marks it deprecated but offers no replacement method;
- `Dividend` carries `dividend_net`, `record_date`, `last_buy_date`,
  `payment_date`, `declared_date`, `dividend_type`, `regularity`, `close_price`;
- `dividend_net` returns the announced (gross) amount: SBER 2024 came back as
  33.30 for a declared 33.30 RUB dividend. It is stored as `dividend_gross`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

logger = logging.getLogger("fetch_dividends")

INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"
DIVIDENDS_PATH = ROOT / "config" / "dividends.json"

DEFAULT_FROM = date(2020, 1, 1)
DEFAULT_TO = date(2030, 1, 1)


def load_instruments(path: Path) -> dict[str, str]:
    """ticker -> figi from config/instruments.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["ticker"]).upper(): str(item["figi"])
        for item in data.get("instruments", [])
        if item.get("ticker") and item.get("figi")
    }


def money_to_float(value) -> float | None:
    """`MoneyValue` -> float, keeping full precision."""
    if value is None:
        return None
    return float(value.units) + float(value.nano) / 1e9


def to_date(value) -> date | None:
    """`google.protobuf.Timestamp` -> date; the API returns UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch dividends into config/dividends.json")
    parser.add_argument("--tickers", nargs="*", help="subset of config/instruments.json")
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM.isoformat())
    parser.add_argument("--to", dest="date_to", default=DEFAULT_TO.isoformat())
    parser.add_argument(
        "--write", action="store_true", help="update the config file instead of printing"
    )
    parser.add_argument("--out", default=str(DIVIDENDS_PATH))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def fetch(tickers: dict[str, str], date_from: date, date_to: date) -> list[dict]:
    """Ask the API for every ticker; one failing instrument does not stop the run."""
    from t_tech.invest import Client
    from t_tech.invest.schemas import InstrumentIdType

    token = os.getenv("TINKOFF_API_TOKEN")
    if not token:
        raise SystemExit("TINKOFF_API_TOKEN is not set (environment or .env)")

    start = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(date_to, datetime.min.time(), tzinfo=UTC)

    events: list[dict] = []
    with Client(token) as client:
        for ticker, figi in sorted(tickers.items()):
            # A FIGI typed from memory resolves to another issuer and returns
            # someone else's dividends. Check the ticker before trusting a reply.
            try:
                instrument = client.instruments.get_instrument_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi
                ).instrument
            except Exception as exc:  # noqa: BLE001 - report and skip one instrument
                raise SystemExit(
                    f"cannot resolve FIGI {figi} for {ticker}: {type(exc).__name__}"
                ) from exc
            if instrument is None or not instrument.figi:
                raise SystemExit(f"FIGI {figi} does not resolve to an instrument ({ticker})")
            if (instrument.ticker or "").upper() != ticker:
                raise SystemExit(
                    f"FIGI mismatch: config/instruments.json maps {ticker} to {figi}, "
                    f"but the API returns {instrument.ticker or '<no ticker>'}. "
                    "Fix config/instruments.json, do not retry with another FIGI."
                )

            try:
                response = client.instruments.get_dividends(
                    figi=figi, from_=start, to=end
                )
            except Exception as exc:  # noqa: BLE001 - one instrument must not stop the run
                logger.warning("get_dividends failed for %s: %s", ticker, type(exc).__name__)
                continue

            found = 0
            for item in response.dividends:
                last_buy = to_date(item.last_buy_date)
                amount = money_to_float(item.dividend_net)
                if last_buy is None or amount is None or amount <= 0:
                    continue
                if (item.dividend_type or "").strip().lower() == "cancelled":
                    continue
                record = to_date(item.record_date)
                events.append(
                    {
                        "ticker": ticker,
                        "record_date": record.isoformat() if record else None,
                        "last_buy_date": last_buy.isoformat(),
                        "dividend_gross": round(amount, 6),
                        "source": "api",
                    }
                )
                found += 1
            logger.info("%s: %d events", ticker, found)
    return events


def merge_manual(existing: dict, fetched: list[dict]) -> list[dict]:
    """Keep hand-checked events; API events replace API events."""
    manual = [
        event
        for event in existing.get("events", [])
        if str(event.get("source", "")).lower() == "manual"
    ]
    merged = [*manual, *fetched]
    merged.sort(key=lambda event: (str(event["ticker"]), str(event["last_buy_date"])))
    return merged


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env")

    instruments = load_instruments(INSTRUMENTS_PATH)
    wanted = [ticker.upper() for ticker in (args.tickers or sorted(instruments))]
    unknown = [ticker for ticker in wanted if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")
    subset = {ticker: instruments[ticker] for ticker in wanted}

    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    fetched = fetch(subset, date_from, date_to)

    out = Path(args.out)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    events = merge_manual(existing, fetched)

    payload = {
        "version": existing.get("version", "1"),
        "notes": existing.get("notes", []),
        "events": events,
    }
    if not args.write:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"# fetched {len(fetched)} events; rerun with --write to update {out}")
        return 0

    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manual = sum(1 for event in events if event.get("source") == "manual")
    print(f"wrote {len(events)} events ({len(fetched)} api, {manual} manual) to {out}")
    print("bump `version` in the file if the model changed, not only the data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
