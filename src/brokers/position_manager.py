"""
Position Manager

Модуль управления позициями с динамическим расчётом размера.

Логика:
1. Получает сигнал от стратегии (ticker, direction, tp%, sl%)
2. Проверяет количество открытых позиций
3. Рассчитывает размер новой позиции (с учётом уже открытых)
4. Возвращает параметры для открытия сделки

Использование:
    from src.brokers.position_manager import PositionManager
    
    manager = PositionManager(token, account_id)
    
    # Получили сигнал от стратегии
    trade_params = manager.calculate_trade_parameters(
        ticker="SBER",
        figi="BBG004730N88",
        direction="LONG",
        strategy_name="SMA Crossover",
        tp_percent=1.5,
        sl_percent=1.0
    )
    
    # Открываем позицию
    if trade_params:
        print(f"Открыть {trade_params['ticker']} на {trade_params['position_size']:,.2f} ₽")
"""

import logging
from typing import Optional, Literal, List
from dataclasses import dataclass
from t_tech.invest import Client

from .position_calculator import PositionCalculator
from config.trading_config import get_trading_config
from config.commissions import MARGIN_FEE_BRACKETS
from src.utils.masking import mask_account_id

logger = logging.getLogger(__name__)


@dataclass
class TradeParameters:
    """Параметры для открытия сделки."""
    # Инструмент
    ticker: str
    figi: str
    instrument_name: str
    
    # Направление и стратегия
    direction: Literal["LONG", "SHORT"]
    strategy_name: str
    
    # TP/SL
    tp_percent: float
    sl_percent: float
    
    # Размер позиции
    position_size: float       # Фактический размер позиции в рублях (с учётом лотов)
    margin_required: float     # Требуемая маржа
    leverage: float            # Плечо
    
    # ✅ НОВЫЕ ПОЛЯ - торговые параметры
    lots: int                  # Количество лотов для покупки
    lot_size: int              # Размер лота (кол-во акций в лоте)
    last_price: float          # Цена последней сделки за 1 акцию
    lot_value: float           # Стоимость 1 лота (last_price × lot_size)
    min_price_increment: float # Шаг цены
    
    # Контекст
    open_positions_count: int  # Количество уже открытых позиций
    remaining_positions: int   # Сколько ещё можно открыть
    
    def __repr__(self):
        return (
            f"Trade({self.ticker} {self.direction}, "
            f"{self.lots} lots × {self.lot_value:.2f}₽ = {self.position_size:,.2f}₽, "
            f"TP={self.tp_percent}%, SL={self.sl_percent}%)"
        )


class PositionManager:
    """
    Менеджер позиций с динамическим расчётом размера.
    
    Отслеживает открытые позиции и рассчитывает размер новых
    с учётом текущей загрузки маржи.
    """
    
    def __init__(self, token: str, account_id: str):
        """
        Инициализация менеджера позиций.
        
        Args:
            token: T-Invest API токен
            account_id: ID счёта для торговли
        """
        self.token = token
        self.account_id = account_id
        
        # Калькулятор размера позиций
        self.calculator = PositionCalculator(token)
        
        # Конфигурация
        self.config = get_trading_config()
        self.max_positions = self.config["max_positions_per_account"]
        self.min_position_size = self.config["min_position_size"]
        
        logger.info(
            f"✓ PositionManager initialized: "
            f"account={mask_account_id(account_id)}, max_positions={self.max_positions}"
        )
    
    def calculate_trade_parameters(
        self,
        ticker: str,
        figi: str,
        direction: Literal["LONG", "SHORT"],
        strategy_name: str,
        tp_percent: float,
        sl_percent: float,
    ) -> Optional[TradeParameters]:
        """
        Рассчитать параметры для открытия сделки.
        
        Вызывается стратегией при получении сигнала.
        
        Args:
            ticker: Тикер инструмента (например, "SBER")
            figi: FIGI код инструмента
            direction: Направление ("LONG" или "SHORT")
            strategy_name: Название стратегии (для логирования)
            tp_percent: Take Profit в процентах
            sl_percent: Stop Loss в процентах
            
        Returns:
            TradeParameters: Параметры сделки или None если нельзя открыть
        """
        try:
            # ═══════════════════════════════════════════════════════════
            # 1. ПРОВЕРЯЕМ КОЛИЧЕСТВО ОТКРЫТЫХ ПОЗИЦИЙ
            # ═══════════════════════════════════════════════════════════
            open_positions_count = self._get_open_positions_count()
            
            if open_positions_count >= self.max_positions:
                logger.warning(
                    f"⚠ Cannot open new position: "
                    f"already have {open_positions_count}/{self.max_positions} positions"
                )
                return None
            
            remaining_slots = self.max_positions - open_positions_count
            
            logger.info(
                f"📊 Position check: {open_positions_count}/{self.max_positions} open, "
                f"{remaining_slots} slots available"
            )
            
            # ═══════════════════════════════════════════════════════════
            # 2. РАССЧИТЫВАЕМ РАЗМЕР НОВОЙ ПОЗИЦИИ
            # ═══════════════════════════════════════════════════════════
            # ✅ ДИНАМИЧЕСКИЙ ПОДХОД: делим свободный баланс на оставшиеся слоты
            result = self.calculator.calculate_position_size(
                account_id=self.account_id,
                figi=figi,
                direction=direction,
                max_positions=remaining_slots  # ← делим на оставшиеся слоты
            )
            
            # ═══════════════════════════════════════════════════════════
            # 3. ПОЛУЧАЕМ ТОРГОВЫЕ ПАРАМЕТРЫ (лотность, цена)
            # ═══════════════════════════════════════════════════════════
            trading_params = self._get_instrument_trading_params(figi)
            
            if trading_params is None:
                logger.error(f"✗ Cannot get trading params for {ticker}")
                return None
            
            lot = trading_params['lot']
            last_price = trading_params['last_price']
            min_price_increment = trading_params['min_price_increment']
            
            # Стоимость 1 лота
            lot_value = last_price * lot
            
            # ═══════════════════════════════════════════════════════════
            # 4. РАССЧИТЫВАЕМ КОЛИЧЕСТВО ЛОТОВ
            # ═══════════════════════════════════════════════════════════
            max_lots = int(result.max_position_size // lot_value)
            
            if max_lots <= 0:
                logger.warning(
                    f"⚠ Not enough funds for 1 lot of {ticker}: "
                    f"max_size={result.max_position_size:,.2f}₽, "
                    f"lot_value={lot_value:,.2f}₽"
                )
                return None
            
            # Фактический размер позиции (целое число лотов)
            actual_position_size = max_lots * lot_value
            
            # Фактическая маржа (пересчитываем под реальный размер)
            actual_margin_required = actual_position_size * result.risk_rate
            
            # ═══════════════════════════════════════════════════════════
            # 4a. КОРРЕКТИРОВКА РАЗМЕРА SHORT ПО МАРЖИНАЛЬНОЙ КОМИССИИ
            # ═══════════════════════════════════════════════════════════
            if direction == "SHORT":
                adjusted = self._adjust_short_for_margin_fee(
                    max_lots, lot_value, actual_position_size, result.risk_rate
                )
                max_lots = adjusted["lots"]
                actual_position_size = adjusted["position_size"]
                actual_margin_required = adjusted["margin_required"]
            
            logger.info(
                f"📊 Lot calculation for {ticker}: "
                f"ideal_size={result.max_position_size:,.2f}₽ → "
                f"{max_lots} lots × {lot_value:,.2f}₽ = {actual_position_size:,.2f}₽"
            )
            
            # ═══════════════════════════════════════════════════════════
            # 5. ПРОВЕРЯЕМ МИНИМАЛЬНЫЙ РАЗМЕР
            # ═══════════════════════════════════════════════════════════
            if actual_position_size < self.min_position_size:
                logger.warning(
                    f"⚠ Position too small: {actual_position_size:,.2f} < "
                    f"{self.min_position_size:,.2f} (min)"
                )
                return None

            
            # ═══════════════════════════════════════════════════════════
            # 4. ФОРМИРУЕМ ПАРАМЕТРЫ СДЕЛКИ
            # ═══════════════════════════════════════════════════════════
            trade_params = TradeParameters(
                ticker=ticker,
                figi=figi,
                instrument_name=result.instrument_name,
                direction=direction,
                strategy_name=strategy_name,
                tp_percent=tp_percent,
                sl_percent=sl_percent,
                position_size=actual_position_size,        # ✅ Фактический размер
                margin_required=actual_margin_required,    # ✅ Фактическая маржа
                leverage=result.leverage,
                lots=max_lots,                             # ✅ Количество лотов
                lot_size=lot,                              # ✅ Размер лота
                last_price=last_price,                     # ✅ Текущая цена
                lot_value=lot_value,                       # ✅ Стоимость лота
                min_price_increment=min_price_increment,   # ✅ Шаг цены
                open_positions_count=open_positions_count,
                remaining_positions=remaining_slots,
            )
            
            logger.info(
                f"✓ Trade parameters: {trade_params.ticker} {trade_params.direction} "
                f"{trade_params.lots} lots = {trade_params.position_size:,.2f}₽ "
                f"(leverage x{trade_params.leverage:.1f})"
            )
            
            return trade_params
            
        except Exception as e:
            logger.error(f"✗ Failed to calculate trade parameters: {e}")
            return None

            
            # ═══════════════════════════════════════════════════════════
            # 4. ФОРМИРУЕМ ПАРАМЕТРЫ СДЕЛКИ
            # ═══════════════════════════════════════════════════════════
            trade_params = TradeParameters(
                ticker=ticker,
                figi=figi,
                instrument_name=result.instrument_name,
                direction=direction,
                strategy_name=strategy_name,
                tp_percent=tp_percent,
                sl_percent=sl_percent,
                position_size=result.max_position_size,
                margin_required=result.margin_per_position,
                leverage=result.leverage,
                open_positions_count=open_positions_count,
                remaining_positions=remaining_positions,
            )
            
            logger.info(
                f"✓ Trade parameters calculated: {trade_params.ticker} {trade_params.direction} "
                f"size={trade_params.position_size:,.2f}₽ "
                f"(leverage x{trade_params.leverage:.1f})"
            )
            
            return trade_params
            
        except Exception as e:
            logger.error(f"✗ Failed to calculate trade parameters: {e}")
            return None
    
    def _get_open_positions_count(self) -> int:
        """
        Получить количество открытых позиций на счёте.
        
        Учитываются только АКЦИИ (не валюты, облигации, фонды).
        
        Returns:
            int: Количество открытых позиций по акциям
        """
        try:
            with Client(self.token) as client:
                # Получаем портфель
                portfolio = client.operations.get_portfolio(account_id=self.account_id)
                
                # ✅ Фильтруем только акции с ненулевым количеством
                open_share_positions = [
                    pos for pos in portfolio.positions
                    if pos.instrument_type == "share"  # ← ТОЛЬКО АКЦИИ!
                    and self._convert_quotation(pos.quantity) != 0
                ]
                
                count = len(open_share_positions)
                
                logger.debug(
                    f"Open share positions: {count} "
                    f"(total positions: {len(portfolio.positions)})"
                )
                
                # Для отладки выведем все позиции
                if logger.isEnabledFor(logging.DEBUG):
                    for pos in portfolio.positions:
                        qty = self._convert_quotation(pos.quantity)
                        if qty != 0:
                            logger.debug(
                                f"  - {pos.instrument_type}: "
                                f"figi={pos.figi}, qty={qty}"
                            )
                
                return count
                
        except Exception as e:
            logger.error(f"✗ Failed to get open positions: {e}")
            # В случае ошибки возвращаем max, чтобы не открывать новые позиции
            return self.max_positions

        
    def _get_instrument_trading_params(self, figi: str) -> Optional[dict]:
        """
        Получить торговые параметры инструмента для расчёта лотов.
        
        Args:
            figi: FIGI код инструмента
            
        Returns:
            dict: {
                'lot': int,                      # Лотность
                'last_price': float,             # Цена последней сделки за 1 акцию
                'min_price_increment': float     # Шаг цены
            }
        """
        try:
            with Client(self.token) as client:
                # 1. Получаем информацию об инструменте (lot, min_price_increment)
                share_response = client.instruments.share_by(id_type=1, id=figi)
                
                if not share_response or not share_response.instrument:
                    logger.error(f"✗ Share not found for FIGI: {figi}")
                    return None
                
                share = share_response.instrument
                lot = share.lot
                min_price_increment = self._convert_quotation(share.min_price_increment)
                
                # 2. Получаем текущую цену (GetLastPrices)
                last_prices_response = client.market_data.get_last_prices(
                    instrument_id=[figi]
                )
                
                if not last_prices_response.last_prices:
                    logger.error(f"✗ No last price available for FIGI: {figi}")
                    return None
                
                # Берём первый элемент (у нас один инструмент)
                last_price_data = last_prices_response.last_prices[0]
                last_price = self._convert_quotation(last_price_data.price)
                
                if last_price <= 0:
                    logger.error(f"✗ Invalid last price for {figi}: {last_price}")
                    return None
                
                logger.debug(
                    f"Instrument params for {figi}: "
                    f"lot={lot}, last_price={last_price:.2f}, "
                    f"min_increment={min_price_increment:.4f}"
                )
                
                return {
                    'lot': lot,
                    'last_price': last_price,
                    'min_price_increment': min_price_increment,
                }
                
        except Exception as e:
            logger.error(f"✗ Failed to get instrument trading params for {figi}: {e}")
            return None

    
    def _adjust_short_for_margin_fee(
        self,
        max_lots: int,
        lot_value: float,
        actual_position_size: float,
        risk_rate: float,
    ) -> dict:
        """
        Скорректировать размер SHORT-позиции по маржинальной комиссии.

        Логика:
        1. Если позиция попадает в верхние 15% своего брекета — оставляем как есть.
        2. Иначе спускаемся на уровень ниже и берём максимальное количество лотов,
           при котором сумма СТРОГО МЕНЬШЕ max_amount нижнего брекета (<, не <=).

        Returns:
            dict: {'lots': int, 'position_size': float, 'margin_required': float}
        """
        try:
            # Находим текущий брекет для рассчитанной позиции
            current_bracket = None
            for bracket in MARGIN_FEE_BRACKETS:
                if bracket.min_amount <= actual_position_size <= bracket.max_amount:
                    current_bracket = bracket
                    break
            if current_bracket is None:
                current_bracket = MARGIN_FEE_BRACKETS[-1]

            # Верхние 15% текущего брекета
            upper_threshold = current_bracket.max_amount * 0.85
            if actual_position_size >= upper_threshold:
                logger.info(
                    f"   💰 SHORT size {actual_position_size:,.2f}₽ is in upper 15% "
                    f"of bracket ({current_bracket.min_amount:,.0f}-{current_bracket.max_amount:,.0f}₽)"
                )
                return {
                    "lots": max_lots,
                    "position_size": actual_position_size,
                    "margin_required": actual_position_size * risk_rate,
                }

            # Иначе — спускаемся на уровень ниже
            lower_bracket = self._find_lower_bracket(current_bracket)
            if lower_bracket is None:
                logger.warning(
                    f"   ⚠ SHORT size {actual_position_size:,.2f}₽ not in upper 15%, "
                    f"but no lower bracket available"
                )
                return {
                    "lots": max_lots,
                    "position_size": actual_position_size,
                    "margin_required": actual_position_size * risk_rate,
                }

            # Максимальная сумма нижнего брекета (строго меньше)
            cap = lower_bracket.max_amount

            # Пересчитываем лоты под кап
            adjusted_lots = int(cap // lot_value)

            if adjusted_lots <= 0:
                logger.warning(
                    f"   ⚠ Cannot fit 1 lot within cap {cap:,.2f}₽ "
                    f"(lot_value={lot_value:,.2f}₽)"
                )
                return {
                    "lots": max_lots,
                    "position_size": actual_position_size,
                    "margin_required": actual_position_size * risk_rate,
                }

            adjusted_size = adjusted_lots * lot_value

            # Проверяем, что не вышли за кап (строго меньше)
            if adjusted_size >= cap:
                adjusted_lots -= 1
                adjusted_size = adjusted_lots * lot_value

            adjusted_margin = adjusted_size * risk_rate

            logger.info(
                f"   💰 SHORT margin-fee adjustment: "
                f"{actual_position_size:,.2f}₽ → {adjusted_size:,.2f}₽ "
                f"({adjusted_lots} lots, cap {cap:,.2f}₽)"
            )

            return {
                "lots": adjusted_lots,
                "position_size": adjusted_size,
                "margin_required": adjusted_margin,
            }

        except Exception as e:
            logger.error(f"✗ Margin-fee adjustment failed: {e}")
            return {
                "lots": max_lots,
                "position_size": actual_position_size,
                "margin_required": actual_position_size * risk_rate,
            }

    @staticmethod
    def _find_lower_bracket(current_bracket):
        """
        Найти брекет на уровень ниже текущего.
        
        Returns:
            MarginFeeBracket: Нижний брекет или None, если текущий — самый нижний.
        """
        idx = None
        for i, bracket in enumerate(MARGIN_FEE_BRACKETS):
            if bracket is current_bracket:
                idx = i
                break
        if idx is None:
            return None
        if idx == 0:
            return None
        return MARGIN_FEE_BRACKETS[idx - 1]

    @staticmethod
    def _convert_quotation(quotation) -> float:
        """Конвертировать Quotation в float."""
        if quotation is None:
            return 0.0
        return float(quotation.units) + float(quotation.nano) / 1e9
    
    
