"""
Account Selector

Выбирает брокерский счёт по направлению сигнала (LONG/SHORT).
- LONG  → ACCOUNT_ID_DUAL_MA_LONG
- SHORT → ACCOUNT_ID_DUAL_MA_SHORT
"""

import logging
import os
from typing import Literal, List, Optional
from dotenv import load_dotenv

from src.utils.masking import mask_account_id

logger = logging.getLogger(__name__)


class AccountSelector:
    """
    Селектор брокерских счетов по направлению сигнала.
    """

    def __init__(self):
        """Инициализация селектора."""
        load_dotenv()

        self.accounts = {
            "LONG": os.getenv("ACCOUNT_ID_DUAL_MA_LONG"),
            "SHORT": os.getenv("ACCOUNT_ID_DUAL_MA_SHORT"),
        }

        self._validate_accounts()

        logger.info("✓ AccountSelector initialized")
        self._log_accounts()

    def _validate_accounts(self):
        """Проверить, что все account_id загружены из .env."""
        missing = []

        for direction, account_id in self.accounts.items():
            if not account_id:
                missing.append(f"{direction}: account_id is None")

        if missing:
            error_msg = "Missing account IDs in .env:\n" + "\n".join(missing)
            logger.error(error_msg)
            raise ValueError(error_msg)

    @staticmethod
    def _mask(account_id: Optional[str]) -> str:
        """Mask an account id for logs: keep only the last 4 characters."""
        return mask_account_id(account_id)

    def _log_accounts(self):
        """Логировать доступные счета (в маскированном виде)."""
        logger.debug("Available accounts:")
        for direction, account_id in self.accounts.items():
            logger.debug(f"  {direction}: {self._mask(account_id)}")

    def get_account_id(
        self,
        direction: Literal["LONG", "SHORT"],
    ) -> str:
        """
        Получить account_id по направлению сигнала.

        Args:
            direction: "LONG" или "SHORT"

        Returns:
            str: Account ID
        """
        if direction not in self.accounts:
            raise ValueError(
                f"Unknown direction: {direction}. "
                f"Available: {list(self.accounts.keys())}"
            )

        account_id = self.accounts[direction]
        if not account_id:
            raise ValueError(f"No account configured for direction {direction}")

        logger.info(f"Selected account for {direction}: {self._mask(account_id)}")
        return account_id

    def get_all_accounts(self) -> List[str]:
        """
        Получить все счета.

        Returns:
            List[str]: Список account_id
        """
        return list(set(acc for acc in self.accounts.values() if acc))
