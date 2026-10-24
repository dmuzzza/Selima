"""
Trade Executor

Модуль для открытия позиций и выставления TP/SL ордеров.

Логика:
1. Получает TradeParameters от PositionManager
2. Открывает рыночную заявку
3. Дожидается исполнения
4. Выставляет Take Profit (лимитная заявка)
5. Выставляет Stop Loss (стоп-заявка)

Использование:
    from src.brokers.trade_executor import TradeExecutor
    from src.brokers.position_manager import PositionManager
    
    manager = PositionManager(token, account_id)
    executor = TradeExecutor(token, account_id)
    
    # Получаем параметры сделки
    trade_params = manager.calculate_trade_parameters(...)
    
    # Открываем позицию
    result = executor.execute_trade(trade_params)
    
    if result.success:
        print(f"Position opened: {result.market_order_id}")
"""

import logging
import time
from typing import Optional, Literal
from dataclasses import dataclass
from decimal import Decimal

from t_tech.invest import Client, OrderDirection, OrderType, StopOrderDirection, StopOrderExpirationType, StopOrderType, ExchangeOrderType
from t_tech.invest.exceptions import RequestError

from src.utils.masking import mask_account_id

logger = logging.getLogger(__name__)

STOP_LIMIT_SLIPPAGE_PERCENT = 0.2


@dataclass
class TradeExecutionResult:
    """Результат выполнения сделки."""
    success: bool
    
    # Рыночная заявка (открытие позиции)
    market_order_id: Optional[str] = None
    execution_price: Optional[float] = None
    executed_lots: Optional[int] = None
    
    # Take Profit ордер
    tp_order_id: Optional[str] = None
    tp_price: Optional[float] = None
    
    # Stop Loss ордер
    sl_order_id: Optional[str] = None
    sl_price: Optional[float] = None
    
    # Ошибка (если не успех)
    error_message: Optional[str] = None
    
    def __repr__(self):
        if self.success:
            return (
                f"TradeExecution(SUCCESS: "
                f"executed {self.executed_lots} lots @ {self.execution_price:.2f}, "
                f"TP={self.tp_price:.2f}, SL={self.sl_price:.2f})"
            )
        else:
            return f"TradeExecution(FAILED: {self.error_message})"


class TradeExecutor:
    """
    Исполнитель торговых операций.
    
    Открывает позиции через T-банк API и выставляет TP/SL.
    """
    
    def __init__(self, token: str, account_id: str):
        """
        Инициализация исполнителя.
        
        Args:
            token: T-Invest API токен
            account_id: ID счёта для торговли
        """
        self.token = token
        self.account_id = account_id
        logger.info(f"✓ TradeExecutor initialized: account={mask_account_id(account_id)}")
    
    def execute_trade(self, trade_params) -> TradeExecutionResult:
        """
        Выполнить сделку: открыть позицию + TP + SL.
        
        Args:
            trade_params: TradeParameters от PositionManager
            
        Returns:
            TradeExecutionResult: Результат выполнения
        """
        try:
            logger.info(
                f"🚀 Executing trade: {trade_params.ticker} {trade_params.direction} "
                f"{trade_params.lots} lots"
            )
            # ✅ ПРОВЕРЯЕМ ТОРГОВЫЙ СТАТУС ИНСТРУМЕНТА
            trading_status = self._check_trading_status(trade_params.figi)
            if not trading_status['available']:
                return TradeExecutionResult(
                    success=False,
                    error_message=f"Trading not available: {trading_status['reason']}"
                )
                
            # ═══════════════════════════════════════════════════════════
            # 1. ОТКРЫВАЕМ РЫНОЧНУЮ ЗАЯВКУ
            # ═══════════════════════════════════════════════════════════
            market_result = self._open_market_order(trade_params)
            
            if not market_result['success']:
                return TradeExecutionResult(
                    success=False,
                    error_message=market_result['error']
                )
            
            market_order_id = market_result['order_id']
            execution_price = market_result['execution_price']
            executed_lots = market_result['executed_lots']
            
            logger.info(
                f"✓ Market order executed: {executed_lots} lots @ {execution_price:.2f}"
            )
            
            # ═══════════════════════════════════════════════════════════
            # 2. РАССЧИТЫВАЕМ ЦЕНЫ TP И SL
            # ═══════════════════════════════════════════════════════════
            # ✅ ИСПОЛЬЗУЕМ last_price из TradeParameters (цена за акцию!)
            entry_price_per_share = trade_params.last_price
            
            logger.info(
                f"📊 Entry price: {entry_price_per_share:.2f} ₽/share "
                f"(market execution: {execution_price:.2f})"
            )
            
            tp_price = self._calculate_tp_price(
                entry_price_per_share,  # ← Цена за акцию
                trade_params.direction,
                trade_params.tp_percent,
                trade_params.min_price_increment
            )
            
            sl_price = self._calculate_sl_price(
                entry_price_per_share,  # ← Цена за акцию
                trade_params.direction,
                trade_params.sl_percent,
                trade_params.min_price_increment
            )
            
            logger.info(f"📊 TP={tp_price:.2f} ₽/share, SL={sl_price:.2f} ₽/share")

            # ✅ ВАЛИДАЦИЯ ЦЕН
            if tp_price <= 0 or sl_price <= 0:
                raise ValueError(f"Invalid TP/SL prices: TP={tp_price}, SL={sl_price}")
            
            # Проверяем разумность (не более ±50% от входа)
            max_deviation = entry_price_per_share * 0.5
            if abs(tp_price - entry_price_per_share) > max_deviation:
                logger.warning(f"⚠️ TP price seems unreasonable: {tp_price:.2f} vs entry {entry_price_per_share:.2f}")
            
            if abs(sl_price - entry_price_per_share) > max_deviation:
                logger.warning(f"⚠️ SL price seems unreasonable: {sl_price:.2f} vs entry {entry_price_per_share:.2f}")
            
            # ═══════════════════════════════════════════════════════════
            # 3. ВЫСТАВЛЯЕМ TAKE PROFIT (лимитная заявка)
            # ═══════════════════════════════════════════════════════════
            tp_result = self._place_tp_order(
                trade_params,
                executed_lots,
                tp_price
            )
            
            tp_order_id = tp_result.get('order_id') if tp_result['success'] else None
            
            # ═══════════════════════════════════════════════════════════
            # 4. ВЫСТАВЛЯЕМ STOP LOSS (стоп-заявка)
            # ═══════════════════════════════════════════════════════════
            sl_result = self._place_sl_order(
                trade_params,
                executed_lots,
                sl_price
            )
            
            sl_order_id = sl_result.get('order_id') if sl_result['success'] else None
            
            # ═══════════════════════════════════════════════════════════
            # 5. ВОЗВРАЩАЕМ РЕЗУЛЬТАТ
            # ═══════════════════════════════════════════════════════════
            result = TradeExecutionResult(
                success=True,
                market_order_id=market_order_id,
                execution_price=execution_price,
                executed_lots=executed_lots,
                tp_order_id=tp_order_id,
                tp_price=tp_price,
                sl_order_id=sl_order_id,
                sl_price=sl_price,
            )
            
            logger.info(f"✅ Trade executed successfully: {result}")
            return result
            
        except Exception as e:
            logger.error(f"✗ Trade execution failed: {e}")
            return TradeExecutionResult(
                success=False,
                error_message=str(e)
            )
    
    def _open_market_order(self, trade_params) -> dict:
        """
        Открыть рыночную заявку.
        
        Returns:
            dict: {'success': bool, 'order_id': str, 'execution_price': float, 'executed_lots': int, 'error': str}
        """
        try:
            with Client(self.token) as client:
                # Направление заявки
                if trade_params.direction == "LONG":
                    order_direction = OrderDirection.ORDER_DIRECTION_BUY
                else:
                    order_direction = OrderDirection.ORDER_DIRECTION_SELL
                
                # Выставляем рыночную заявку
                order_response = client.orders.post_order(
                    figi=trade_params.figi,
                    quantity=trade_params.lots,
                    direction=order_direction,
                    account_id=self.account_id,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                )
                
                order_id = order_response.order_id
                
                logger.info(f"📝 Market order placed: {order_id}")
                
                # Ждём исполнения (проверяем статус)
                execution_price, executed_lots = self._wait_for_order_execution(
                    order_id,
                    timeout=10
                )
                
                return {
                    'success': True,
                    'order_id': order_id,
                    'execution_price': execution_price,
                    'executed_lots': executed_lots,
                }
                
        except RequestError as e:
            logger.error(f"✗ Market order failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _wait_for_order_execution(self, order_id: str, timeout: int = 30) -> tuple[float, int]:
        """
        Дождаться исполнения ордера.
        
        Args:
            order_id: ID ордера
            timeout: Таймаут в секундах
            
        Returns:
            tuple: (execution_price, executed_lots)
        """
        from t_tech.invest import OrderExecutionReportStatus
        
        start_time = time.time()
        last_status = None
        
        with Client(self.token) as client:
            while time.time() - start_time < timeout:
                try:
                    order_state = client.orders.get_order_state(
                        account_id=self.account_id,
                        order_id=order_id
                    )
                    
                    # Логируем только при изменении статуса
                    if order_state.execution_report_status != last_status:
                        status_name = OrderExecutionReportStatus(order_state.execution_report_status).name
                        logger.info(f"📊 Order {order_id} status: {status_name}")
                        last_status = order_state.execution_report_status
                    
                    # Проверяем статус (используем enum)
                    if order_state.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                        # Ордер полностью исполнен
                        total_amount = self._convert_money_value(order_state.executed_order_price)
                        executed_lots = order_state.lots_executed
                        execution_price = total_amount / executed_lots
                        
                        logger.info(f"✅ Order {order_id} FILLED: {executed_lots} lots @ {execution_price:.2f}")
                        return execution_price, executed_lots
                    
                    elif order_state.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_REJECTED:
                        raise Exception(f"Order {order_id} was REJECTED: {order_state.message}")
                    
                    elif order_state.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_CANCELLED:
                        raise Exception(f"Order {order_id} was CANCELLED")
                    
                    # Статусы NEW и PARTIALLYFILL — продолжаем ждать
                    
                except Exception as e:
                    if "rejected" in str(e).lower() or "cancelled" in str(e).lower():
                        raise
                    logger.warning(f"⚠️ Error checking order status: {e}")
                
                time.sleep(0.5)
            
            # Таймаут — но может быть ордер уже исполнен
            # Делаем последнюю проверку
            try:
                order_state = client.orders.get_order_state(
                    account_id=self.account_id,
                    order_id=order_id
                )
                
                if order_state.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                    total_amount = self._convert_money_value(order_state.executed_order_price)
                    executed_lots = order_state.lots_executed
                    execution_price = total_amount / executed_lots
                    logger.warning(f"⚠️ Order {order_id} filled after timeout: {executed_lots} lots @ {execution_price:.2f}")
                    return execution_price, executed_lots
            except:
                pass
            
            raise Exception(f"Order {order_id} execution timeout ({timeout}s)")
    
    def _check_trading_status(self, figi: str) -> dict:
        """
        Проверить торговый статус инструмента.
        
        Returns:
            dict: {'available': bool, 'reason': str}
        """
        try:
            with Client(self.token) as client:
                # Получаем торговый статус
                trading_status = client.market_data.get_trading_status(figi=figi)
                
                if not trading_status.market_order_available_flag:
                    reason = "Market orders not available (market closed or halted)"
                    logger.warning(f"⚠️ {reason}")
                    return {'available': False, 'reason': reason}
                
                logger.debug(f"✓ Trading available for {figi}")
                return {'available': True, 'reason': ''}
                
        except Exception as e:
            logger.warning(f"⚠️ Cannot check trading status: {e}")
            # Продолжаем выполнение (может быть временная ошибка API)
            return {'available': True, 'reason': ''}

    
    def _calculate_tp_price(
        self,
        entry_price: float,
        direction: str,
        tp_percent: float,
        min_increment: float
    ) -> float:
        """
        Рассчитать цену Take Profit.
        
        Args:
            entry_price: Цена входа
            direction: LONG или SHORT
            tp_percent: TP в процентах
            min_increment: Шаг цены
            
        Returns:
            float: Цена TP (квантованная)
        """
        if direction == "LONG":
            tp_price = entry_price * (1 + tp_percent / 100)
        else:
            tp_price = entry_price * (1 - tp_percent / 100)
        
        # Квантуем по шагу цены
        rounded_price = self._round_price(tp_price, min_increment)
        
        logger.debug(
            f"TP calculation: {entry_price:.2f} → "
            f"{tp_price:.2f} (raw) → {rounded_price:.2f} (rounded)"
        )
        
        return rounded_price
    
    def _calculate_sl_price(
        self,
        entry_price: float,
        direction: str,
        sl_percent: float,
        min_increment: float
    ) -> float:
        """
        Рассчитать цену Stop Loss.
        
        Args:
            entry_price: Цена входа
            direction: LONG или SHORT
            sl_percent: SL в процентах
            min_increment: Шаг цены
            
        Returns:
            float: Цена SL (квантованная)
        """
        if direction == "LONG":
            sl_price = entry_price * (1 - sl_percent / 100)
        else:
            sl_price = entry_price * (1 + sl_percent / 100)
        
        # Квантуем по шагу цены
        rounded_price = self._round_price(sl_price, min_increment)
        
        logger.debug(
            f"SL calculation: {entry_price:.2f} → "
            f"{sl_price:.2f} (raw) → {rounded_price:.2f} (rounded)"
        )
        
        return rounded_price
    
    def _place_tp_order(self, trade_params, lots: int, tp_price: float) -> dict:
        """
        Выставить Take Profit (стоп-заявка типа Take Profit).
        
        Returns:
            dict: {'success': bool, 'order_id': str, 'error': str}
        """
        try:
            with Client(self.token) as client:
                # Направление обратное открытию
                if trade_params.direction == "LONG":
                    stop_direction = StopOrderDirection.STOP_ORDER_DIRECTION_SELL
                else:
                    stop_direction = StopOrderDirection.STOP_ORDER_DIRECTION_BUY
                
                # ✅ Стоп-заявка типа TAKE_PROFIT (бессрочная)
                stop_response = client.stop_orders.post_stop_order(
                    figi=trade_params.figi,
                    quantity=lots,
                    price=self._price_to_quotation(tp_price),        # Цена исполнения
                    stop_price=self._price_to_quotation(tp_price),   # Цена активации
                    direction=stop_direction,
                    account_id=self.account_id,
                    expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                    stop_order_type=StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT,  # ← TAKE PROFIT!
                    exchange_order_type=ExchangeOrderType.EXCHANGE_ORDER_TYPE_MARKET
                )
                
                order_id = stop_response.stop_order_id
                logger.info(f"✓ TP stop-order placed: {order_id} @ {tp_price:.2f}")
                
                return {
                    'success': True,
                    'order_id': order_id,
                }
                
        except RequestError as e:
            error_msg = f"TP order failed: {e}"
            logger.error(f"✗ {error_msg}")
            logger.error(f"   Details: figi={trade_params.figi}, lots={lots}, price={tp_price:.2f}")
            return {
                'success': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"TP order unexpected error: {e}"
            logger.error(f"✗ {error_msg}")
            return {
                'success': False,
                'error': error_msg
            }

    
    def _place_sl_order(self, trade_params, lots: int, sl_price: float) -> dict:
        """
        Выставить Stop Loss (стоп-заявка типа STOP_LIMIT).

        Использует два уровня цены:
        - stop_price:      цена активации (триггер)
        - execution_price: лимитная цена исполнения = stop_price ± STOP_LIMIT_SLIPPAGE_PERCENT

        Это гарантирует исполнение даже при быстром пробое стопа.

        Returns:
            dict: {'success': bool, 'order_id': str, 'error': str}
        """
        try:
            with Client(self.token) as client:

                # Направление обратное открытию
                if trade_params.direction == "LONG":
                    stop_direction = StopOrderDirection.STOP_ORDER_DIRECTION_SELL
                    # Пробой вниз → лимит чуть ниже стопа
                    execution_price = sl_price * (1 - STOP_LIMIT_SLIPPAGE_PERCENT / 100)
                else:
                    stop_direction = StopOrderDirection.STOP_ORDER_DIRECTION_BUY
                    # Пробой вверх → лимит чуть выше стопа
                    execution_price = sl_price * (1 + STOP_LIMIT_SLIPPAGE_PERCENT / 100)

                # Квантуем execution_price по шагу цены инструмента
                execution_price = self._round_price(
                    execution_price,
                    trade_params.min_price_increment
                )

                logger.info(
                    f"🛡️ SL order: stop_price={sl_price:.2f} → "
                    f"execution_price={execution_price:.2f} "
                    f"(slippage={STOP_LIMIT_SLIPPAGE_PERCENT}%, "
                    f"direction={trade_params.direction})"
                )

                stop_response = client.stop_orders.post_stop_order(
                    figi=trade_params.figi,
                    quantity=lots,
                    stop_price=self._price_to_quotation(sl_price),       # ← триггер
                    price=self._price_to_quotation(execution_price),      # ← лимит с отрывом
                    direction=stop_direction,
                    account_id=self.account_id,
                    expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL,
                    stop_order_type=StopOrderType.STOP_ORDER_TYPE_STOP_LOSS,
                    exchange_order_type=ExchangeOrderType.EXCHANGE_ORDER_TYPE_MARKET
                )

                order_id = stop_response.stop_order_id
                logger.info(
                    f"✓ SL order placed: {order_id} | "
                    f"trigger={sl_price:.2f} → limit={execution_price:.2f}"
                )
                return {
                    'success': True,
                    'order_id': order_id,
                }

        except RequestError as e:
            error_msg = f"SL order failed: {e}"
            logger.error(f"✗ {error_msg}")
            logger.error(
                f"  Details: figi={trade_params.figi}, lots={lots}, "
                f"stop_price={sl_price:.2f}, execution_price={execution_price:.2f}"
            )
            return {
                'success': False,
                'error': error_msg,
            }

        except Exception as e:
            error_msg = f"SL order unexpected error: {e}"
            logger.error(f"✗ {error_msg}")
            return {
                'success': False,
                'error': error_msg,
            }

    
    @staticmethod
    def _round_price(price: float, min_increment: float) -> float:
        """
        Квантовать цену по минимальному шагу.
        
        Args:
            price: Исходная цена
            min_increment: Минимальный шаг цены
            
        Returns:
            float: Квантованная цена
        """
        if min_increment <= 0:
            return round(price, 2)
        
        # Используем Decimal для точного округления
        from decimal import Decimal, ROUND_HALF_UP
        
        price_dec = Decimal(str(price))
        increment_dec = Decimal(str(min_increment))
        
        # Округляем до ближайшего шага
        steps = (price_dec / increment_dec).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        rounded = steps * increment_dec
        
        # Форматируем до нужного количества знаков
        # Для increment=0.01 нужно 2 знака
        if increment_dec >= Decimal('0.01'):
            rounded = rounded.quantize(Decimal('0.01'))
        elif increment_dec >= Decimal('0.001'):
            rounded = rounded.quantize(Decimal('0.001'))
        else:
            rounded = rounded.quantize(Decimal('0.0001'))
        
        result = float(rounded)
        
        logger.debug(
            f"Round price: {price} → {result} "
            f"(increment={min_increment})"
        )
        
        return result

    
    @staticmethod
    def _price_to_quotation(price: float):
        """
        Конвертировать цену в Quotation для API.
        
        Args:
            price: Цена в float
            
        Returns:
            Quotation: Объект для API
        """
        from t_tech.invest import Quotation
        from decimal import Decimal
        
        # Используем Decimal для точной конвертации
        price_dec = Decimal(str(price))
        
        units = int(price_dec)
        nano = int((price_dec - units) * Decimal('1000000000'))
        
        # Логируем для отладки
        logger.debug(f"Price to Quotation: {price} → units={units}, nano={nano}")
        
        return Quotation(units=units, nano=nano)
    
    @staticmethod
    def _convert_money_value(money_value) -> float:
        """Конвертировать MoneyValue в float."""
        if money_value is None:
            return 0.0
        return float(money_value.units) + float(money_value.nano) / 1e9
    
    @staticmethod
    def _convert_quotation(quotation) -> float:
        """Конвертировать Quotation в float."""
        if quotation is None:
            return 0.0
        return float(quotation.units) + float(quotation.nano) / 1e9
