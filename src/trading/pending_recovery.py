"""Recovery of PENDING live trades left behind by a crashed process.

A PENDING record is inserted before the order is sent (ADR-010), so a crash
between that insert and the OPEN update leaves a record without position_id.
Such a record is invisible to both follow-up jobs:

- the order collector needs `position_id` to match stop orders;
- the reconciler works from active stop orders, which do not exist yet.

The `signal_key` stays occupied forever, so the signal is silently lost.

`PendingRecovery` compares the exchange state with the database and picks the
safest action for every stale record:

| Exchange state                                   | Action                       |
|--------------------------------------------------|------------------------------|
| open position for the FIGI                        | adopt: PENDING -> OPEN       |
| no position, no trade operations since the signal | CANCELLED, free signal_key   |
| no position, but a trade operation exists         | keep PENDING, needs review   |

The third case is the dangerous one: the position was opened and closed while
the process was down. Freeing the key would allow a second entry on the same
candle, so the key is kept and the record is logged for a human.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from t_tech.invest import Client
from t_tech.invest.schemas import InstrumentIdType, OperationType

from src.trading.models import LiveTrade, TradeStatus
from src.utils.masking import mask_account_id

logger = logging.getLogger(__name__)

# Operation types that mean "a position was actually traded".
TRADE_OPERATION_TYPES = frozenset(
    {
        OperationType.OPERATION_TYPE_BUY,
        OperationType.OPERATION_TYPE_BUY_CARD,
        OperationType.OPERATION_TYPE_BUY_MARGIN,
        OperationType.OPERATION_TYPE_SELL,
        OperationType.OPERATION_TYPE_SELL_CARD,
        OperationType.OPERATION_TYPE_SELL_MARGIN,
        OperationType.OPERATION_TYPE_DELIVERY_BUY,
        OperationType.OPERATION_TYPE_DELIVERY_SELL,
    }
)


@dataclass
class RecoverStats:
    """Outcome of one recovery pass."""

    checked: int = 0
    adopted: int = 0
    cancelled: int = 0
    needs_review: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"checked={self.checked}, adopted={self.adopted}, "
            f"cancelled={self.cancelled}, review={self.needs_review}, "
            f"errors={self.errors}"
        )


def _to_float(value: Any) -> float:
    """Convert Quotation/MoneyValue (units + nano) or a number to float."""
    if value is None:
        return 0.0
    units = getattr(value, "units", None)
    if units is not None:
        return float(units) + float(getattr(value, "nano", 0)) / 1e9
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class PendingRecovery:
    """Reconcile stale PENDING records against the exchange state."""

    def __init__(
        self,
        token: str,
        trade_repo,
        *,
        min_age_minutes: int = 30,
        client_factory: Callable[[str], Any] = Client,
        log: logging.Logger | None = None,
    ) -> None:
        self.token = token
        self.trade_repo = trade_repo
        self.min_age_minutes = min_age_minutes
        self.client_factory = client_factory
        self.log = log or logger

    def recover(self) -> RecoverStats:
        """Run one recovery pass over all stale PENDING records."""
        stats = RecoverStats()

        stale = self.trade_repo.get_stale_pending(self.min_age_minutes)
        if not stale:
            self.log.debug("✓ No stale PENDING trades")
            return stats

        self.log.warning(f"⚠️ Stale PENDING trades found: {len(stale)}")
        stats.checked = len(stale)

        by_account: dict[str, list[LiveTrade]] = {}
        for trade in stale:
            by_account.setdefault(trade.account_id, []).append(trade)

        for account_id, trades in by_account.items():
            masked = mask_account_id(account_id)
            try:
                with self.client_factory(self.token) as client:
                    portfolio = client.operations.get_portfolio(account_id=account_id)
                    for trade in trades:
                        self._recover_one(client, portfolio, trade, stats)
            except Exception as exc:  # noqa: BLE001 - one account must not stop the rest
                stats.errors += 1
                self.log.error(
                    f"❌ Pending recovery failed for account {masked}: {exc}",
                    exc_info=True,
                )

        self.log.info(f"📊 Pending recovery: {stats}")
        return stats

    # ─────────────────────────── Per-trade logic ───────────────────────────

    def _recover_one(self, client, portfolio, trade: LiveTrade, stats: RecoverStats) -> None:
        try:
            position = self._find_position(portfolio, trade.figi)
            if position is not None:
                self._adopt(client, trade, position)
                stats.adopted += 1
                return

            if self._has_trade_operations(client, trade):
                # Position was opened and closed while we were down: keep the key.
                trade.error_message = "PENDING after restart: position already closed, review manually"
                self.trade_repo.save(trade)
                stats.needs_review += 1
                self.log.error(
                    f"❌ {trade.ticker} {trade.direction} {trade.signal_key}: "
                    f"trade operations exist but position is gone — manual review required"
                )
                return

            trade.status = TradeStatus.CANCELLED
            trade.signal_key = None
            trade.error_message = "Order was never sent or never filled (recovered)"
            self.trade_repo.save(trade)
            stats.cancelled += 1
            self.log.warning(
                f"↩️ {trade.ticker} {trade.direction}: no position, no operations — "
                f"CANCELLED, signal released"
            )

        except Exception as exc:  # noqa: BLE001 - keep going with other trades
            stats.errors += 1
            self.log.error(f"❌ Pending recovery failed for {trade.trade_id}: {exc}", exc_info=True)

    @staticmethod
    def _find_position(portfolio, figi: str):
        """Open position for the FIGI from a portfolio response, if any."""
        for group in ("securities", "futures"):
            for position in getattr(portfolio, group, None) or []:
                if getattr(position, "figi", None) != figi:
                    continue
                if abs(_to_float(getattr(position, "quantity", None))) > 1e-12:
                    return position
        return None

    def _adopt(self, client, trade: LiveTrade, position) -> None:
        """Turn a stale PENDING record into OPEN using the exchange position."""
        shares = _to_float(getattr(position, "quantity", None))
        price = _to_float(getattr(position, "average_position_price", None))
        lots = abs(int(_to_float(getattr(position, "quantity_lots", None))))

        if lots <= 0:
            lots = 1
        lot_size = self._lot_size(client, trade.figi)

        trade.position_id = getattr(position, "position_uid", None) or None
        trade.status = TradeStatus.OPEN
        trade.entry_time = trade.entry_time or trade.timestamp
        trade.entry_price = price or trade.entry_price
        trade.lots = lots
        trade.lot_size = lot_size
        trade.position_size = abs(shares) * (price or 0.0) or trade.position_size
        trade.error_message = None
        self.trade_repo.save(trade)

        self.log.warning(
            f"✅ {trade.ticker} {trade.direction}: PENDING -> OPEN "
            f"(lots={trade.lots}, lot_size={trade.lot_size}, entry={trade.entry_price})"
        )

    def _lot_size(self, client, figi: str) -> int:
        """Lot size of the instrument; 1 when the lookup fails."""
        try:
            response = client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi
            )
            lot = getattr(getattr(response, "instrument", None), "lot", None)
            return int(lot) if lot else 1
        except Exception as exc:  # noqa: BLE001 - lot size is not critical
            self.log.warning(f"⚠️ Cannot resolve lot size for {figi}: {exc}")
            return 1

    def _has_trade_operations(self, client, trade: LiveTrade) -> bool:
        """Whether a buy/sell operation exists for the FIGI since the signal."""
        try:
            response = client.operations.get_operations(
                account_id=trade.account_id,
                figi=trade.figi,
                from_=trade.timestamp or trade.entry_time,
            )
        except Exception as exc:  # noqa: BLE001 - treat unknown as "review"
            self.log.warning(
                f"⚠️ Cannot read operations for {trade.ticker}: {exc}; keeping signal_key"
            )
            return True

        for operation in getattr(response, "operations", None) or []:
            if getattr(operation, "type", None) in TRADE_OPERATION_TYPES:
                return True
        return False
