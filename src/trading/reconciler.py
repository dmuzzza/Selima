# src/trading/reconciler.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Any, Callable

from t_tech.invest import Client, StopOrderStatusOption

from src.trading.repository import LiveTradeRepository
from src.trading.stop_order_repository import StopOrderRepository


logger = logging.getLogger(__name__)


@dataclass
class ReconcileStats:
    executed_positions_closed: int = 0
    manual_positions_closed: int = 0
    cancelled_orders_synced: int = 0
    expired_orders_synced: int = 0
    active_orders_touched: int = 0
    unknown_orders_synced: int = 0
    missing_in_api: int = 0
    errors: int = 0

    @property
    def positions_closed_total(self) -> int:
        return self.executed_positions_closed + self.manual_positions_closed


class TradingReconciler:
    """
    Сервис сверки состояния (биржа/API -> наша БД):
    - синхронизация статусов стоп-ордеров (ACTIVE/EXECUTED/CANCELLED/EXPIRED/...)
    - закрытие сделок в live_trades по факту EXECUTED
    - fallback: ручное закрытие (позиции уже нет, а у нас trade OPEN)
    - запись диагностической информации из API в stop_orders.metadata (JSON-safe)
    """

    def __init__(
        self,
        token: str,
        trades_db_path: str = "",
        *,
        timezone=None,
        log: Optional[logging.Logger] = None,
        trade_repo=None,
        order_repo=None,
    ) -> None:
        self.token = token
        self.trades_db_path = trades_db_path
        self.tz = timezone
        self.log = log or logger

        self.trade_repo = trade_repo or LiveTradeRepository(trades_db_path)
        self.order_repo = order_repo or StopOrderRepository(trades_db_path)

    # ------------------------- JSON-safe helpers -------------------------

    @staticmethod
    def _safe_str(v: Any) -> str:
        try:
            return str(v)
        except Exception:
            return "<unprintable>"

    def _enum_to_name(self, enum_cls, value) -> str:
        """
        Безопасно превращает enum/int/None в строковое имя.
        Нужен, чтобы metadata точно сериализовалась в JSON.
        """
        if value is None:
            return "NONE"
        try:
            return enum_cls(value).name
        except Exception:
            return getattr(value, "name", self._safe_str(value))

    # ------------------------- API value converters -------------------------

    @staticmethod
    def _mv_to_float(mv) -> float:
        """
        MoneyValue/Quotation-like: units + nano.
        Работает и для Quotation, и для MoneyValue (оба имеют units/nano).
        """
        if mv is None:
            return 0.0
        units = float(getattr(mv, "units", 0))
        nano = float(getattr(mv, "nano", 0))
        return units + nano / 1e9

    def _get_last_price(self, client: Client, figi: str) -> float:
        try:
            resp = client.market_data.get_last_prices(figi=[figi])
            last_prices = getattr(resp, "last_prices", None) or []
            if not last_prices:
                return 0.0
            return self._mv_to_float(getattr(last_prices[0], "price", None))
        except Exception as e:
            self.log.warning(f"⚠️ Cannot get last price for {figi}: {e}")
            return 0.0

    def _get_stop_orders_all(self, client: Client, account_id: str):
        """
        Важно: без status=ALL метод часто возвращает только ACTIVE стоп-ордера.
        Пытаемся запросить ALL; если SDK не поддерживает параметр status, откатываемся.
        """
        try:
            return client.stop_orders.get_stop_orders(
                account_id=account_id,
                status=StopOrderStatusOption.STOP_ORDER_STATUS_ALL,
            )
        except TypeError:
            self.log.warning(
                "⚠️ SDK stop_orders.get_stop_orders() doesn't support status=ALL; "
                "EXECUTED/CANCELLED may be invisible. Consider updating invest-python."
            )
            return client.stop_orders.get_stop_orders(account_id=account_id)

    # ------------------------- Metadata attachment -------------------------

    def _attach_api_metadata(
        self,
        db_order,
        api_order,
        api_status,
        *,
        note: str = "",
    ) -> None:
        """
        Пишем в db_order.metadata диагностическую инфу из API.
        Важно: только JSON-совместимые типы (str/int/float/bool/None).
        """
        if getattr(db_order, "metadata", None) is None:
            db_order.metadata = {}

        db_order.metadata["api_checked_at"] = datetime.now().isoformat()
        if note:
            db_order.metadata["api_note"] = note

        if api_order is None:
            db_order.metadata["api_missing"] = True
            db_order.metadata["api_status"] = "MISSING"
            return

        db_order.metadata["api_missing"] = False
        db_order.metadata["api_status"] = self._enum_to_name(StopOrderStatusOption, api_status)

        # Доп. поля (могут отличаться по версиям SDK, поэтому getattr)
        try:
            api_order_type = getattr(api_order, "order_type", None)
            db_order.metadata["api_order_type"] = getattr(api_order_type, "name", self._safe_str(api_order_type)) if api_order_type is not None else None
        except Exception:
            db_order.metadata["api_order_type"] = None

        try:
            api_stop_price = getattr(api_order, "stop_price", None)
            db_order.metadata["api_stop_price"] = self._mv_to_float(api_stop_price) if api_stop_price is not None else None
        except Exception:
            db_order.metadata["api_stop_price"] = None

        try:
            api_price = getattr(api_order, "price", None)
            db_order.metadata["api_price"] = self._mv_to_float(api_price) if api_price is not None else None
        except Exception:
            db_order.metadata["api_price"] = None

        try:
            db_order.metadata["api_figi"] = getattr(api_order, "figi", None)
        except Exception:
            db_order.metadata["api_figi"] = None

        try:
            lots = getattr(api_order, "lots_requested", None)
            db_order.metadata["api_lots_requested"] = int(lots) if lots is not None else None
        except Exception:
            db_order.metadata["api_lots_requested"] = None

    # ------------------------- Domain helpers -------------------------

    @staticmethod
    def _set_stop_order_status(db_order, status_name: str) -> bool:
        """
        Аккуратно выставляет db_order.status, только если enum уже поддерживает этот статус.
        Возвращает True, если выставили, иначе False.
        """
        try:
            from src.trading.stop_order_models import StopOrderStatus
        except Exception:
            return False

        try:
            member = StopOrderStatus[status_name]
        except Exception:
            return False

        db_order.status = member
        return True

    def _manual_close_allowed(self, *, trade_id: str, position_id: str) -> bool:
        """
        MANUAL close разрешаем только если:
        - по position_id в БД есть стоп-ордера именно этого trade_id
        - ни один не EXECUTED
        - все финальные CANCELLED/EXPIRED
        """
        try:
            all_orders = self.order_repo.get_orders_by_position_id(position_id)
        except Exception as e:
            self.log.warning(f"⚠️ Cannot load stop_orders for position_id={position_id}: {e}")
            return False

        orders = [o for o in all_orders if getattr(o, "trade_id", None) == trade_id]
        if len(orders) < 2:
            return False

        statuses = {getattr(o.status, "value", str(o.status)) for o in orders}

        if "EXECUTED" in statuses:
            return False

        allowed_final = {"CANCELLED", "EXPIRED"}
        return statuses.issubset(allowed_final)



    # ------------------------- Core logic -------------------------

    def reconcile(
        self,
        *,
        skip_weekends: bool = True,
        is_weekend_fn: Optional[Callable[[], bool]] = None,
    ) -> ReconcileStats:
        stats = ReconcileStats()

        now = datetime.now(self.tz) if self.tz else datetime.now()
        self.log.info("\n" + "─" * 60)
        self.log.info("🟢 Reconcile: STOP-ORDERS + POSITIONS")
        self.log.info(f" Time: {now.strftime('%H:%M:%S')}")
        self.log.info("─" * 60)

        if skip_weekends and is_weekend_fn and is_weekend_fn():
            self.log.info("⏸️ Weekend - skipping")
            return stats

        # 1) Берём ACTIVE из нашей БД
        try:
            active_orders = self.order_repo.get_active_orders()
        except Exception as e:
            stats.errors += 1
            self.log.error(f"❌ Failed to read active stop orders from DB: {e}", exc_info=True)
            return stats

        if not active_orders:
            self.log.info("✓ No active stop-orders in DB")
            return stats

        # 2) Группируем по account_id (в моделях он уже расшифрован)
        orders_by_account: Dict[str, List] = {}
        for o in active_orders:
            orders_by_account.setdefault(o.account_id, []).append(o)

        self.log.info(f"📊 Active stop-orders in DB: {len(active_orders)}")
        self.log.info(f"📂 Accounts to check: {len(orders_by_account)}")

        with Client(self.token) as client:
            for account_id, db_orders in orders_by_account.items():
                self.log.info(f"\n 📂 Account: ***{account_id[-4:]} ({len(db_orders)} orders)")

                # --- Stop-orders sync (statuses) ---
                try:
                    api_resp = self._get_stop_orders_all(client, account_id)
                    api_orders = getattr(api_resp, "stop_orders", None) or []
                    api_by_id = {x.stop_order_id: x for x in api_orders}
                    self.log.info(f" API returned: {len(api_by_id)} orders")
                except Exception as e:
                    stats.errors += 1
                    self.log.error(f"❌ Failed to fetch stop-orders for account ***{account_id[-4:]}: {e}", exc_info=True)
                    continue

                for db_order in db_orders:
                    try:
                        api_order = api_by_id.get(db_order.stop_order_id)
                        api_status = getattr(api_order, "status", None) if api_order else None

                        db_order.last_checked_at = datetime.now()

                        # Not in API response
                        if api_status is None:
                            self._attach_api_metadata(db_order, api_order=None, api_status=None, note="not_in_api_response")
                            self.order_repo.save(db_order)
                            stats.missing_in_api += 1
                            continue

                        # ACTIVE
                        if api_status == StopOrderStatusOption.STOP_ORDER_STATUS_ACTIVE:
                            self._attach_api_metadata(db_order, api_order=api_order, api_status=api_status, note="active")
                            self.order_repo.save(db_order)
                            stats.active_orders_touched += 1
                            continue

                        # EXECUTED
                        if api_status == StopOrderStatusOption.STOP_ORDER_STATUS_EXECUTED:
                            self._set_stop_order_status(db_order, "EXECUTED")
                            db_order.executed_at = db_order.executed_at or datetime.now()

                            self._attach_api_metadata(db_order, api_order=api_order, api_status=api_status, note="executed")
                            self.order_repo.save(db_order)

                            # Закрываем сделку в БД (exit_price приближённо = stop_price)
                            if self.trade_repo.close_trade_by_id(
                                trade_id=db_order.trade_id,
                                exit_type=db_order.order_type,          # TP/SL
                                exit_price=float(db_order.stop_price),  # пока приближенно
                            ):
                                stats.executed_positions_closed += 1
                            continue

                        # CANCELED
                        if api_status == StopOrderStatusOption.STOP_ORDER_STATUS_CANCELED:
                            self._set_stop_order_status(db_order, "CANCELLED")
                            db_order.executed_at = db_order.executed_at or datetime.now()

                            self._attach_api_metadata(db_order, api_order=api_order, api_status=api_status, note="cancelled")
                            self.order_repo.save(db_order)
                            stats.cancelled_orders_synced += 1
                            continue

                        # EXPIRED (может быть не во всех версиях SDK)
                        if hasattr(StopOrderStatusOption, "STOP_ORDER_STATUS_EXPIRED") and (
                            api_status == StopOrderStatusOption.STOP_ORDER_STATUS_EXPIRED
                        ):
                            self._set_stop_order_status(db_order, "EXPIRED")
                            db_order.executed_at = db_order.executed_at or datetime.now()

                            self._attach_api_metadata(db_order, api_order=api_order, api_status=api_status, note="expired")
                            self.order_repo.save(db_order)
                            stats.expired_orders_synced += 1
                            continue

                        # Unknown / other statuses
                        self._set_stop_order_status(db_order, "UNKNOWN")
                        self._attach_api_metadata(db_order, api_order=api_order, api_status=api_status, note="unknown_status")
                        self.order_repo.save(db_order)
                        stats.unknown_orders_synced += 1

                    except Exception as e:
                        stats.errors += 1
                        self.log.error(
                            f"❌ Error syncing stop-order {getattr(db_order, 'stop_order_id', '<no-id>')}: {e}",
                            exc_info=True,
                        )

                # --- Manual close reconciliation (fallback) ---
                try:
                    open_trades = [
                        t for t in self.trade_repo.get_open_trades()
                        if t.account_id == account_id and getattr(t, "figi", None) and getattr(t, "position_id", None)
                    ]

                    if open_trades:
                        pos_resp = client.operations.get_positions(account_id=account_id)
                        sec_positions = getattr(pos_resp, "securities", None) or []

                        open_figis = set()

                        # securities: balance = int64
                        for p in (getattr(pos_resp, "securities", None) or []):
                            figi = getattr(p, "figi", None)
                            balance = getattr(p, "balance", 0)
                            try:
                                bal = float(balance)
                            except Exception:
                                bal = self._mv_to_float(balance)  # fallback, если SDK вернул Quotation/MoneyValue
                            if figi and abs(bal) > 1e-12:
                                open_figis.add(figi)

                        # futures: тоже balance = int64
                        for p in (getattr(pos_resp, "futures", None) or []):
                            figi = getattr(p, "figi", None)
                            balance = getattr(p, "balance", 0)
                            try:
                                bal = float(balance)
                            except Exception:
                                bal = self._mv_to_float(balance)
                            if figi and abs(bal) > 1e-12:
                                open_figis.add(figi)


                        for tr in open_trades:
                            if tr.figi in open_figis:
                                continue

                            # позиция исчезла -> проверяем, что стопы этой сделки финально CANCELLED/EXPIRED
                            if not self._manual_close_allowed(trade_id=tr.trade_id, position_id=tr.position_id):
                                continue

                            exit_price = self._get_last_price(client, tr.figi) or float(getattr(tr, "entry_price", 0.0) or 0.0)

                            if self.trade_repo.close_trade_by_id(
                                trade_id=tr.trade_id,
                                exit_type="MANUAL",
                                exit_price=float(exit_price),
                            ):
                                stats.manual_positions_closed += 1

                except Exception as e:
                    stats.errors += 1
                    self.log.warning(
                        f"⚠️ Manual-close reconciliation failed for account ***{account_id[-4:]}: {e}",
                        exc_info=True,
                    )

        self.log.info("\n" + "─" * 60)
        self.log.info("📊 RECONCILE COMPLETED")
        self.log.info(f" 🔔 Executed -> positions closed: {stats.executed_positions_closed}")
        self.log.info(f" 🧷 Manual closes detected: {stats.manual_positions_closed}")
        self.log.info(f" ✓ Cancelled synced: {stats.cancelled_orders_synced}")
        self.log.info(f" ⏳ Expired synced: {stats.expired_orders_synced}")
        self.log.info(f" ↩ Missing in API response: {stats.missing_in_api}")
        self.log.info(f" ⚠️ Unknown synced: {stats.unknown_orders_synced}")
        self.log.info(f" ❌ Errors: {stats.errors}")
        self.log.info("─" * 60 + "\n")

        return stats
