"""Add live_trades.signal_key and its unique index (PostgreSQL).

Idempotent: safe to run multiple times. Connection settings come from the
standard PG_* environment variables; no credentials are printed.

Usage:
    uv run python migrations/migrate_add_signal_key.py
"""

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_add_signal_key")

REQUIRED_ENV = ("PG_USER", "PG_PASSWORD", "PG_HOST", "PG_DBNAME")

STATEMENTS = (
    "ALTER TABLE live_trades ADD COLUMN IF NOT EXISTS signal_key TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_trades_signal_key "
    "ON live_trades(signal_key) WHERE signal_key IS NOT NULL",
)


def build_url() -> str:
    """Build the PostgreSQL URL from PG_* environment variables."""
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    return (
        f"postgresql://{os.getenv('PG_USER')}:{os.getenv('PG_PASSWORD')}"
        f"@{os.getenv('PG_HOST')}:5432/{os.getenv('PG_DBNAME')}?sslmode=require"
    )


def main() -> None:
    load_dotenv()
    engine = create_engine(build_url(), pool_pre_ping=True)
    with engine.begin() as conn:
        for statement in STATEMENTS:
            conn.execute(text(statement))
            log.info("applied: %s", statement)
    log.info("migration complete")


if __name__ == "__main__":
    main()
