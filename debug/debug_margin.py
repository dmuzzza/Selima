"""
Debug Margin Attributes

Скрипт для отладки маржинальных атрибутов.
Выводит ВСЕ поля из GetMarginAttributesResponse.
"""

import os
import logging
from dotenv import load_dotenv
from tinkoff.invest import Client, AccountType

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

logger = logging.getLogger(__name__)


def convert_money_value(money_value) -> float:
    """Конвертировать MoneyValue в float."""
    if money_value is None:
        return 0.0
    return float(money_value.units) + float(money_value.nano) / 1e9


def convert_quotation(quotation) -> float:
    """Конвертировать Quotation в float."""
    if quotation is None:
        return 0.0
    return float(quotation.units) + float(quotation.nano) / 1e9


def main():
    """Вывести все маржинальные атрибуты."""
    
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    
    if not token:
        logger.error("✗ TINKOFF_TOKEN not found in .env")
        return
    
    try:
        with Client(token) as client:
            # Получаем список счетов
            accounts_response = client.users.get_accounts()
            
            for account in accounts_response.accounts:
                # Только брокерские счета
                if account.type != AccountType.ACCOUNT_TYPE_TINKOFF:
                    continue
                
                account_id = account.id
                account_name = account.name
                
                print("\n" + "=" * 100)
                print(f"📊 СЧЁТ: {account_name} (ID: {account_id})")
                print("=" * 100)
                
                # ═══════════════════════════════════════════════════════════
                # ПОРТФЕЛЬ
                # ═══════════════════════════════════════════════════════════
                try:
                    portfolio = client.operations.get_portfolio(account_id=account_id)
                    
                    total_amount_shares = convert_money_value(portfolio.total_amount_shares)
                    total_amount_bonds = convert_money_value(portfolio.total_amount_bonds)
                    total_amount_etf = convert_money_value(portfolio.total_amount_etf)
                    total_amount_currencies = convert_money_value(portfolio.total_amount_currencies)
                    total_amount_futures = convert_money_value(portfolio.total_amount_futures)
                    
                    expected_yield_value = convert_quotation(portfolio.expected_yield)
                    
                    print(f"\n📈 ПОРТФЕЛЬ:")
                    print(f"  Акции:     {total_amount_shares:>15,.2f} ₽")
                    print(f"  Облигации: {total_amount_bonds:>15,.2f} ₽")
                    print(f"  ETF:       {total_amount_etf:>15,.2f} ₽")
                    print(f"  Валюта:    {total_amount_currencies:>15,.2f} ₽")
                    print(f"  Фьючерсы:  {total_amount_futures:>15,.2f} ₽")
                    print(f"  Доходность:{expected_yield_value:>15,.2f} ₽")
                    
                except Exception as e:
                    print(f"✗ Ошибка получения портфеля: {e}")
                
                # ═══════════════════════════════════════════════════════════
                # МАРЖИНАЛЬНЫЕ АТРИБУТЫ
                # ═══════════════════════════════════════════════════════════
                try:
                    margin_attrs = client.users.get_margin_attributes(account_id=account_id)
                    
                    liquid_portfolio = convert_money_value(margin_attrs.liquid_portfolio)
                    starting_margin = convert_money_value(margin_attrs.starting_margin)
                    minimal_margin = convert_money_value(margin_attrs.minimal_margin)
                    funds_sufficiency_level = convert_quotation(margin_attrs.funds_sufficiency_level)
                    amount_of_missing_funds = convert_money_value(margin_attrs.amount_of_missing_funds)
                    corrected_margin = convert_money_value(margin_attrs.corrected_margin)
                    
                    print(f"\n💰 МАРЖИНАЛЬНЫЕ АТРИБУТЫ:")
                    print(f"  liquid_portfolio (ликвидный портфель):        {liquid_portfolio:>15,.2f} ₽")
                    print(f"  starting_margin (начальная маржа):            {starting_margin:>15,.2f} ₽")
                    print(f"  minimal_margin (минимальная маржа):           {minimal_margin:>15,.2f} ₽")
                    print(f"  funds_sufficiency_level (уровень достаточн.): {funds_sufficiency_level:>15,.4f}")
                    print(f"  amount_of_missing_funds (недостающие средства):{amount_of_missing_funds:>15,.2f} ₽")
                    print(f"  corrected_margin (скорректированная маржа):   {corrected_margin:>15,.2f} ₽")
                    
                    # ═══════════════════════════════════════════════════════════
                    # РАСЧЁТ МАКСИМАЛЬНОЙ МАРЖИ (ПОПЫТКА ФОРМУЛ)
                    # ═══════════════════════════════════════════════════════════
                    print(f"\n🧮 РАСЧЁТЫ:")
                    
                    # Вариант 1: liquid_portfolio / starting_margin
                    if starting_margin > 0:
                        leverage = liquid_portfolio / starting_margin
                        max_variant_1 = liquid_portfolio * leverage
                        print(f"  Вариант 1 (leverage * liquid): {max_variant_1:>15,.2f} ₽ (плечо: {leverage:.2f}x)")
                    
                    # Вариант 2: liquid_portfolio - starting_margin
                    free_margin = liquid_portfolio - starting_margin
                    print(f"  Вариант 2 (свободная маржа):   {free_margin:>15,.2f} ₽")
                    
                    # Вариант 3: corrected_margin (если не 0)
                    if corrected_margin > 0:
                        print(f"  Вариант 3 (corrected_margin):  {corrected_margin:>15,.2f} ₽")
                    
                    # Вариант 4: liquid_portfolio / minimal_margin
                    if minimal_margin > 0:
                        leverage_min = liquid_portfolio / minimal_margin
                        max_variant_4 = liquid_portfolio * leverage_min
                        print(f"  Вариант 4 (minimal leverage):  {max_variant_4:>15,.2f} ₽ (плечо: {leverage_min:.2f}x)")
                    
                    print(f"\n➡️  СРАВНИ ЭТИ ЦИФРЫ С Т-БАНКОМ!")
                    
                except Exception as margin_error:
                    print(f"\n⚠️  Маржа недоступна: {margin_error}")
                
                print("=" * 100)
    
    except Exception as e:
        logger.error(f"✗ Ошибка: {e}")
        raise


if __name__ == "__main__":
    main()
