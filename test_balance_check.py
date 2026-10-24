"""
Temporary script: test margin fee adjustment logic.
Delete after testing.
"""

import os
import logging
from dotenv import load_dotenv
from src.brokers.position_manager import PositionManager
from config.commissions import MARGIN_FEE_BRACKETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

logger = logging.getLogger(__name__)


def test_adjustment(token, account_id):
    """Test _adjust_short_for_margin_fee with simulated values."""
    pm = PositionManager(token, account_id)

    lot_value = 253.98  # T lot value
    risk_rate = 0.20    # dshort_min for T

    print("\n" + "=" * 80)
    print("MARGIN FEE ADJUSTMENT TESTS")
    print("=" * 80)
    print(f"\nBrackets:")
    for b in MARGIN_FEE_BRACKETS:
        threshold = b.max_amount * 0.85
        print(f"  {b.min_amount:>10,.0f} - {b.max_amount:>10,.0f} | fee={b.daily_fee:>5.0f} | upper 15% from {threshold:>10,.0f}")

    test_cases = [
        # (description, max_lots, position_size)
        ("In upper 15% of bracket 0-50k (49780)",  196, 49_780.08),
        ("Just above 50k, below 85k (55000)",       217, 55_000.00),
        ("Middle of 50k-100k (70000)",              276, 70_000.00),
        ("In upper 15% of 50k-100k (90000)",        354, 90_000.00),
        ("Just above 100k, below 212.5k (120000)",  472, 120_000.00),
        ("Small position in 0-50k (8000)",           31, 8_000.00),
    ]

    for desc, lots, size in test_cases:
        print(f"\n{'-' * 80}")
        print(f"TEST: {desc}")
        print(f"  Input:  lots={lots}, size={size:,.2f}")
        result = pm._adjust_short_for_margin_fee(
            max_lots=lots,
            lot_value=lot_value,
            actual_position_size=size,
            risk_rate=risk_rate,
        )
        print(f"  Output: lots={result['lots']}, size={result['position_size']:,.2f}, margin={result['margin_required']:,.2f}")

    print("\n" + "=" * 80 + "\n")


def main():
    load_dotenv()
    token = os.getenv("TINKOFF_API_TOKEN")
    account_short = os.getenv("ACCOUNT_ID_DUAL_MA_SHORT")
    if not token or not account_short:
        print("ERROR: token or account not found")
        return
    test_adjustment(token, account_short)


if __name__ == "__main__":
    main()
