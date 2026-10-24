"""
Order Collector

Собирает стоп-ордера (TP/SL) для новых сделок.

После открытия сделки стоп-ордера появляются на бирже не мгновенно.
Этот класс ждёт 30 секунд, затем запрашивает ордера через API
и сохраняет их в БД.
"""

import logging
import sqlite3
import time
from datetime import datetime
from typing import List, Dict, Optional
from t_tech.invest import Client

from .repository import LiveTradeRepository
from .stop_order_repository import StopOrderRepository
from .stop_order_models import StopOrderRecord, StopOrderStatus
from .models import LiveTrade

logger = logging.getLogger(__name__)


class OrderCollector:
    """
    Собирает стоп-ордера для новых сделок.
    
    Workflow:
    1. Находит сделки с orders_collected = 0
    2. Ждёт 30 секунд (чтобы ордера появились на бирже)
    3. Запрашивает стоп-ордера через API
    4. Сохраняет найденные ордера в БД
    5. Помечает сделку: orders_collected = 1
    """
    
    def __init__(self, token: str, db_path: str = "data/live_trades.db",
                 trade_repo=None, order_repo=None):
        """
        Args:
            token: T-Invest API токен
            db_path: Путь к БД (используется, если не переданы репозитории)
            trade_repo: Кастомный репозиторий сделок (например PgLiveTradeRepository)
            order_repo: Кастомный репозиторий стоп-ордеров (например PgStopOrderRepository)
        """
        self.token = token
        self.db_path = db_path
        self.trade_repo = trade_repo or LiveTradeRepository(db_path)
        self.order_repo = order_repo or StopOrderRepository(db_path)
        logger.info("✓ OrderCollector initialized")
    
    def collect_pending_orders(self, wait_seconds: int = 30) -> Dict[str, int]:
        """
        Собрать стоп-ордера для всех сделок, у которых orders_collected = 0.
        
        Args:
            wait_seconds: Сколько секунд ждать после открытия сделки
        
        Returns:
            dict: {"collected": int, "errors": int}
        """
        stats = {"collected": 0, "errors": 0}
        
        # ═══════════════════════════════════════════════════════════════════
        # 1. ПОЛУЧАЕМ СДЕЛКИ БЕЗ СОБРАННЫХ ОРДЕРОВ
        # ═══════════════════════════════════════════════════════════════════
        
        trades = self._get_trades_without_orders()
        
        if not trades:
            logger.debug("✓ No trades without orders")
            return stats
        
        logger.info(f"📋 Found {len(trades)} trade(s) without orders")
        
        # ═══════════════════════════════════════════════════════════════════
        # 2. ФИЛЬТРУЕМ ПО ВРЕМЕНИ (должно пройти wait_seconds)
        # ═══════════════════════════════════════════════════════════════════
        
        ready_trades = []
        now = datetime.now()
        
        for trade in trades:
            if not trade.entry_time:
                logger.warning(f"⚠️ Trade {trade.trade_id} has no entry_time, skipping")
                continue
            
            elapsed = (now - trade.entry_time).total_seconds()
            
            if elapsed >= wait_seconds:
                ready_trades.append(trade)
                logger.info(
                    f"   ✓ {trade.ticker} ready (elapsed: {elapsed:.0f}s)"
                )
            else:
                logger.debug(
                    f"   ⏳ {trade.ticker} waiting (elapsed: {elapsed:.0f}s, "
                    f"need: {wait_seconds}s)"
                )
        
        if not ready_trades:
            logger.info("⏳ All trades still waiting...")
            return stats
        
        logger.info(f"🔍 Collecting orders for {len(ready_trades)} trade(s)")
        
        # ═══════════════════════════════════════════════════════════════════
        # 3. СОБИРАЕМ ОРДЕРА ДЛЯ КАЖДОЙ СДЕЛКИ
        # ═══════════════════════════════════════════════════════════════════
        
        with Client(self.token) as client:
            for trade in ready_trades:
                try:
                    logger.info(f"\n📥 Collecting orders for {trade.ticker} (***{trade.account_id[-4:]})")
                    
                    result = self.collect_orders_for_trade(client, trade)
                    
                    if result["success"]:
                        stats["collected"] += result["orders_count"]
                        logger.info(
                            f"   ✅ Collected {result['orders_count']} order(s)"
                        )
                    else:
                        stats["errors"] += 1
                        logger.warning(f"   ⚠️ No orders found")
                
                except Exception as e:
                    logger.error(f"   ❌ Error collecting orders: {e}")
                    stats["errors"] += 1
        
        logger.info(
            f"\n📊 Collection summary: {stats['collected']} orders collected, "
            f"{stats['errors']} errors"
        )
        
        return stats
    
    def _get_trades_without_orders(self) -> List[LiveTrade]:
        """
        Получить сделки, для которых ещё не собраны ордера.

        Возвращает сделки с orders_collected = 0 (статусы OPEN/PENDING,
        position_id задан, entry_time задан).

        Использует метод репозитория (работает и с SQLite, и с PostgreSQL).
        """
        return self.trade_repo.get_trades_without_orders()
    
    def _map_order_type(self, api_order_type) -> str:
        """
        Преобразовать тип ордера из API в наш формат.
        
        Args:
            api_order_type: StopOrderType из Tinkoff API
            
        Returns:
            str: "TP" | "SL" | "STOP_LIMIT"
        """
        from t_tech.invest import StopOrderType
        
        mapping = {
            StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT: "TP",
            StopOrderType.STOP_ORDER_TYPE_STOP_LOSS: "SL",
            StopOrderType.STOP_ORDER_TYPE_STOP_LIMIT: "SL",  # у нас SL выставляется как STOP_LIMIT
        }

        
        order_type = mapping.get(api_order_type, "UNKNOWN")
        
        if order_type == "UNKNOWN":
            logger.warning(f"⚠️ Unknown stop_order_type: {api_order_type}")
        
        return order_type


    def collect_orders_for_trade(self, client: Client, trade: LiveTrade) -> Dict[str, any]:
        """Собирает стоп-ордера для конкретной сделки по FIGI."""
        result = {"success": False, "orders_count": 0}
        
        try:
            # Получаем ВСЕ стоп-ордера на счёте
            response = client.stop_orders.get_stop_orders(account_id=trade.account_id)
            
            if not response.stop_orders:
                logger.warning(f"No stop orders on account ...{trade.account_id[-4:]}")
                return result
            
            logger.info(f"   📊 Found {len(response.stop_orders)} stop order(s) on account")
            
        except Exception as e:
            logger.error(f"Failed to get stop orders: {e}")
            return result
        
        # ═══════════════════════════════════════════════════════════════
        # ФИЛЬТРУЕМ ПО FIGI (как в твоём тестовом коде!)
        # ═══════════════════════════════════════════════════════════════
        matching_orders = []
        
        for order in response.stop_orders:
            # Проверяем наличие figi и сравниваем

            if hasattr(order, 'figi') and order.figi == trade.figi:
                logger.info(f"   ✓ Matched: {order.stop_order_id} (FIGI: {order.figi})")
                matching_orders.append(order)
        
        if not matching_orders:
            logger.warning(f"⚠️ No orders found for {trade.ticker} (FIGI: {trade.figi})")
            self.mark_orders_collected(trade, success=False)
            return result
        
        logger.info(f"   ✅ Found {len(matching_orders)} matching order(s) for {trade.ticker}")
        
        # ═══════════════════════════════════════════════════════════════
        # СОХРАНЯЕМ В БД (присваиваем position_id ИЗ СДЕЛКИ!)
        # ═══════════════════════════════════════════════════════════════
        saved_count = 0
        
        for order in matching_orders:
            try:
                order_type = self._map_order_type(order.order_type)
                stop_price = self._extract_price(order)
                
                order_record = StopOrderRecord(
                    stop_order_id=order.stop_order_id,
                    position_id=trade.position_id,  # ← Берём из сделки!
                    trade_id=trade.trade_id,        # ← Берём из сделки!
                    account_id=trade.account_id,
                    figi=trade.figi,
                    ticker=trade.ticker,
                    # direction=trade.direction,
                    order_type=order_type,
                    stop_price=stop_price,
                    lots_requested=order.lots_requested,
                    status=StopOrderStatus.ACTIVE,
                    created_at=datetime.now(),
                    last_checked_at=datetime.now()
                )
                
                if self.order_repo.save(order_record):
                    saved_count += 1
                    logger.info(f"   ✓ Saved {order_type} @ {stop_price:.2f}₽")
            
            except Exception as e:
                logger.error(f"Failed to save order {order.stop_order_id}: {e}")
        
        if saved_count > 0:
            self.mark_orders_collected(trade, success=True)
            result["success"] = True
            result["orders_count"] = saved_count
        else:
            self.mark_orders_collected(trade, success=False)
        
        return result


    
    def _determine_order_type(self, order, trade: LiveTrade) -> str:
        """
        Определить тип ордера (TP или SL).
        
        Логика:
        - LONG: верхний ордер = TP, нижний = SL
        - SHORT: нижний ордер = TP, верхний = SL
        """
        stop_price = self._extract_price(order)
        
        # Берём сохранённые цены TP и SL из сделки
        tp_price = trade.tp_price
        sl_price = trade.sl_price
        
        if not tp_price or not sl_price:
            logger.error(f"❌ Trade {trade.trade_id} missing TP/SL prices!")
            return "UNKNOWN"
        
        if trade.direction == "LONG":
            # LONG: TP выше SL
            # Сравниваем stop_price: если ближе к верхней цене → TP
            if stop_price > sl_price:
                return "TP"
            else:
                return "SL"
        
        else:  # SHORT
            # SHORT: TP ниже SL
            # Если stop_price ниже → TP
            if stop_price < sl_price:
                return "TP"
            else:
                return "SL"

    
    def _extract_price(self, order) -> float:
        """
        Извлечь цену срабатывания из стоп-ордера.
        
        Args:
            order: StopOrder из API
        
        Returns:
            float: Цена
        """
        # Стоп-ордер может иметь stop_price в разных форматах
        if hasattr(order, 'stop_price') and order.stop_price:
            # Quotation формат
            if hasattr(order.stop_price, 'units'):
                return float(order.stop_price.units) + float(order.stop_price.nano) / 1e9
            return float(order.stop_price)
        
        # Fallback - берём price
        if hasattr(order, 'price') and order.price:
            if hasattr(order.price, 'units'):
                return float(order.price.units) + float(order.price.nano) / 1e9
            return float(order.price)
        
        logger.warning(f"⚠️ Could not extract price from order {order.stop_order_id}")
        return 0.0
    
    def mark_orders_collected(self, trade: LiveTrade, success: bool):
        """
        Отмечает сделку как обработанную.
        
        Args:
            trade: Сделка
            success: True если ордера найдены и сохранены
        """
        if success:
            # Ордера найдены — больше не пытаемся
            trade.orders_collected = True
            trade.error_message = None
            logger.info(f"✓ Marked {trade.trade_id} as collected")
        else:
            # Ордера НЕ найдены — оставляем флаг = 0, попробуем позже
            trade.orders_collected = False  # ← ИСПРАВЛЕНО!
            trade.error_message = "No stop orders found on exchange"
            logger.warning(f"⚠️ Will retry collecting orders for {trade.trade_id}")
        
        self.trade_repo.save(trade)

