"""
Account Information Module

Модуль для работы с информацией по счетам брокера T-Invest.
Получение балансов, маржинальных данных и метрик счетов.

Использование:
    from src.brokers.account_info import AccountInfo
    
    account_info = AccountInfo(token)
    balances = account_info.get_account_balances()
    account_info.print_balances_table()
"""

import logging
from dataclasses import dataclass
from typing import List, Optional
from t_tech.invest import Client, AccountType

from src.utils.masking import mask_account_id

logger = logging.getLogger(__name__)


@dataclass
class AccountBalance:
    """Информация о балансе счета."""
    account_id: str
    account_name: str
    account_type: str
    available_balance: float       # Доступный баланс (сумма валют) в рублях
    liquid_portfolio: float        # Ликвидная стоимость портфеля
    max_margin_balance: float      # МАКСИМАЛЬНАЯ сумма для торговли с маржой
    starting_margin: float         # Начальная маржа (для новых сделок)
    minimal_margin: float          # Минимальная маржа (для поддержания позиций)


class AccountInfo:
    """
    Класс для работы с информацией по счетам брокера T-Invest.
    
    Получает:
    - Список счетов
    - Портфели (балансы)
    - Маржинальные атрибуты
    """
    
    def __init__(self, token: str):
        """
        Инициализация модуля работы со счетами.
        
        Args:
            token: T-Invest API токен
        """
        self.token = token
        logger.info("✓ AccountInfo initialized")
    
    def get_account_balances(self) -> List[AccountBalance]:
        """
        Получить информацию по балансам всех счетов.
        
        Фильтрует только ACCOUNT_TYPE_TINKOFF (брокерские счета).
        
        Returns:
            List[AccountBalance]: Список балансов по счетам
        """
        balances = []
        
        try:
            with Client(self.token) as client:
                # Получаем список счетов
                accounts_response = client.users.get_accounts()
                
                for account in accounts_response.accounts:
                    # Фильтруем только брокерские счета T-Invest
                    if account.type != AccountType.ACCOUNT_TYPE_TINKOFF:
                        logger.debug(f"Skipping account {account.id} (type={account.type})")
                        continue
                    
                    account_id = account.id
                    account_name = account.name
                    
                    try:
                        # Получаем портфель (баланс)
                        portfolio = client.operations.get_portfolio(account_id=account_id)
                        
                        # Парсим доступный баланс (сумма валют)
                        available_balance = self._convert_money_value(
                            portfolio.total_amount_currencies
                        )
                        
                        # ═══════════════════════════════════════════════════════════
                        # ПОПЫТКА ПОЛУЧИТЬ МАРЖИНАЛЬНЫЕ АТРИБУТЫ
                        # ═══════════════════════════════════════════════════════════
                        liquid_portfolio = available_balance
                        max_margin_balance = available_balance
                        starting_margin = 0.0
                        minimal_margin = 0.0
                        
                        try:
                            # Пытаемся получить маржинальные атрибуты
                            margin_attrs = client.users.get_margin_attributes(account_id=account_id)
                            
                            # Ликвидный портфель
                            liquid_portfolio = self._convert_money_value(
                                margin_attrs.liquid_portfolio
                            )
                            
                            # Начальная маржа (для новых сделок)
                            starting_margin = self._convert_money_value(
                                margin_attrs.starting_margin
                            )
                            
                            # Минимальная маржа (для поддержания позиций)
                            minimal_margin = self._convert_money_value(
                                margin_attrs.minimal_margin
                            )
                            
                            # ═══════════════════════════════════════════════════════════
                            # РАСЧЁТ МАКСИМАЛЬНОЙ СУММЫ ДЛЯ ТОРГОВЛИ С МАРЖОЙ
                            # ═══════════════════════════════════════════════════════════
                            # Формула: liquid_portfolio - starting_margin + liquid_portfolio
                            # Это даёт максимальную сумму, которую можно использовать
                            
                            if starting_margin > 0:
                                # Рассчитываем коэффициент кредитного плеча
                                # leverage = liquid_portfolio / starting_margin
                                # max_margin = liquid_portfolio * leverage
                                
                                # Упрощённая формула от T-банка:
                                # Максимум для торговли = liquid_portfolio - starting_margin + liquid_portfolio
                                max_margin_balance = liquid_portfolio + (liquid_portfolio - starting_margin)
                            else:
                                # Если starting_margin == 0, значит маржа полностью доступна
                                max_margin_balance = liquid_portfolio
                            
                            logger.debug(f"✓ Margin attributes retrieved for {mask_account_id(account_id)}")
                            
                        except Exception as margin_error:
                            # Если маржа отключена или недоступна
                            error_msg = str(margin_error)
                            
                            if "margin status is disabled" in error_msg.lower() or "30051" in error_msg:
                                logger.warning(
                                    f"⚠ Margin disabled for account {mask_account_id(account_id)} ({account_name}). "
                                    f"Using available_balance for all fields."
                                )
                            else:
                                # Другая ошибка - логируем, но продолжаем
                                logger.warning(
                                    f"⚠ Failed to get margin attributes for {mask_account_id(account_id)}: {margin_error}"
                                )
                        
                        # ═══════════════════════════════════════════════════════════
                        # СОХРАНЯЕМ ДАННЫЕ
                        # ═══════════════════════════════════════════════════════════
                        balance = AccountBalance(
                            account_id=account_id,
                            account_name=account_name,
                            account_type="TINKOFF",
                            available_balance=available_balance,
                            liquid_portfolio=liquid_portfolio,
                            max_margin_balance=max_margin_balance,
                            starting_margin=starting_margin,
                            minimal_margin=minimal_margin,
                        )
                        
                        balances.append(balance)
                        logger.debug(f"✓ Processed account {mask_account_id(account_id)}: {account_name}")
                        
                    except Exception as account_error:
                        # Если не удалось обработать конкретный счёт
                        logger.error(
                            f"✗ Failed to process account {mask_account_id(account_id)} ({account_name}): {account_error}"
                        )
                        # Продолжаем обработку других счетов
                        continue
                
                logger.info(f"✓ Retrieved {len(balances)} account balances")
                return balances
                
        except Exception as e:
            logger.error(f"✗ Failed to get account balances: {e}")
            raise
    
    def print_balances_table(self) -> None:
        """
        Вывести таблицу с балансами счетов в консоль.
        
        Формат:
        | ID счета | Название | Доступный баланс | Маржиналка |
        """
        balances = self.get_account_balances()
        
        if not balances:
            print("Нет доступных счетов типа TINKOFF.")
            return
        
        # Заголовок
        print("\n" + "=" * 110)
        print(f"{'ID счета':<20} | {'Название':<25} | {'Баланс (₽)':>15} | {'Маржиналка (₽)':>18}")
        print("=" * 110)
        
        # Данные
        for balance in balances:
            print(
                f"{balance.account_id:<20} | "
                f"{balance.account_name:<25} | "
                f"{balance.available_balance:>15,.2f} | "
                f"{balance.max_margin_balance:>18,.2f}"
            )
        
        print("=" * 110 + "\n")
    
    @staticmethod
    def _convert_money_value(money_value) -> float:
        """
        Конвертировать MoneyValue из API в float (рубли).
        
        T-Invest API возвращает деньги в формате:
        - units: целая часть
        - nano: дробная часть (наноединицы, 1e-9)
        
        Args:
            money_value: MoneyValue объект из API
            
        Returns:
            float: Сумма в рублях
        """
        if money_value is None:
            return 0.0
        
        return float(money_value.units) + float(money_value.nano) / 1e9
