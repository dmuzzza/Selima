"""
Position Size Calculator

Модуль для расчёта максимального размера позиции с учётом:
- Маржинальных лимитов счёта
- Ставок риска инструментов (dlong_min/dshort_min)
- Количества одновременных сделок
"""

import logging
from typing import Optional, Literal
from dataclasses import dataclass
from t_tech.invest import Client

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """Результат расчёта размера позиции."""
    figi: str
    ticker: str
    instrument_name: str
    direction: Literal["LONG", "SHORT"]
    
    # Маржинальные данные счёта
    liquid_portfolio: float
    starting_margin: float
    free_margin: float
    
    # Параметры инструмента
    risk_rate: float
    leverage: float
    
    # Расчёты
    max_positions: int
    margin_per_position: float
    max_position_size: float


class PositionCalculator:
    """Калькулятор размера позиций."""
    
    def __init__(self, token: str):
        self.token = token
        self._instrument_cache = {}
        logger.debug("✓ PositionCalculator initialized")
    
    def calculate_position_size(
        self,
        account_id: str,
        figi: str,
        direction: Literal["LONG", "SHORT"] = "LONG",
        max_positions: int = 1,
    ) -> PositionSizeResult:
        """
        Рассчитать максимальный размер позиции.
        
        Args:
            account_id: ID счёта
            figi: FIGI код инструмента
            direction: Направление ("LONG" или "SHORT")
            max_positions: Количество одновременных позиций
            
        Returns:
            PositionSizeResult: Результат расчёта
        """
        try:
            with Client(self.token) as client:
                # 2. Ставки риска инструмента
                instrument_info = self._get_instrument_risk_rates(client, figi)

                if instrument_info is None:
                    raise ValueError(f"Instrument {figi} not found")

                ticker = instrument_info['ticker']
                instrument_name = instrument_info['name']

                # ═══════════════════════════════════════════════════════════
                # РАЗВЕТВЛЕНИЕ ЛОГИКИ: LONG — без маржи, SHORT — с маржой
                # ═══════════════════════════════════════════════════════════
                if direction == "LONG":
                    # LONG: немаржинальный счёт — берём доступный баланс из портфеля
                    portfolio = client.operations.get_portfolio(account_id=account_id)
                    liquid_portfolio = self._convert_money_value(
                        portfolio.total_amount_currencies
                    )
                    starting_margin = 0.0
                    free_margin = liquid_portfolio
                    margin_per_position = liquid_portfolio / max_positions

                    # Без маржи: плечо = 1, risk_rate не используется
                    risk_rate = 0.0
                    leverage = 1.0
                    max_position_size = margin_per_position

                    logger.debug(
                        f"✓ {ticker} (LONG, no margin): {max_position_size:,.2f}₽ "
                        f"(balance={liquid_portfolio:,.2f}₽)"
                    )
                else:
                    # SHORT: маржинальный счёт — плечо x1 (без умножения на dshort_min)
                    margin_attrs = client.users.get_margin_attributes(account_id=account_id)

                    liquid_portfolio = self._convert_money_value(margin_attrs.liquid_portfolio)
                    starting_margin = self._convert_money_value(margin_attrs.starting_margin)
                    free_margin = liquid_portfolio - starting_margin
                    margin_per_position = liquid_portfolio / max_positions

                    # Плечо x1: берём выделенную сумму как есть, без умножения
                    risk_rate = 1.0
                    leverage = 1.0
                    max_position_size = margin_per_position

                    logger.debug(
                        f"✓ {ticker} (SHORT, margin x1): {max_position_size:,.2f}₽ "
                        f"(balance={liquid_portfolio:,.2f}₽)"
                    )

                return PositionSizeResult(
                    figi=figi,
                    ticker=ticker,
                    instrument_name=instrument_name,
                    direction=direction,
                    liquid_portfolio=liquid_portfolio,
                    starting_margin=starting_margin,
                    free_margin=free_margin,
                    risk_rate=risk_rate,
                    leverage=leverage,
                    max_positions=max_positions,
                    margin_per_position=margin_per_position,
                    max_position_size=max_position_size,
                )
                
        except Exception as e:
            logger.error(f"✗ Failed to calculate position size: {e}")
            raise
    
    def _get_instrument_risk_rates(self, client: Client, figi: str) -> Optional[dict]:
        """Получить ставки риска инструмента."""
        if figi in self._instrument_cache:
            return self._instrument_cache[figi]
        
        try:
            response = client.instruments.share_by(id_type=1, id=figi)
            
            if response and response.instrument:
                instrument = response.instrument
                
                info = {
                    'figi': instrument.figi,
                    'ticker': instrument.ticker,
                    'name': instrument.name or "",
                    'dlong_min': self._convert_quotation(instrument.dlong_min),
                    'dshort_min': self._convert_quotation(instrument.dshort_min),
                }
                
                self._instrument_cache[figi] = info
                return info
            
            return None
            
        except Exception as e:
            logger.error(f"✗ Failed to get instrument {figi}: {e}")
            return None
    
    @staticmethod
    def _convert_money_value(money_value) -> float:
        if money_value is None:
            return 0.0
        return float(money_value.units) + float(money_value.nano) / 1e9
    
    @staticmethod
    def _convert_quotation(quotation) -> float:
        if quotation is None:
            return 0.0
        return float(quotation.units) + float(quotation.nano) / 1e9
