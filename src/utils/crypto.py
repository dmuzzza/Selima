"""
Crypto Utilities

Шифрование/дешифрование чувствительных данных (account_id).
"""

import os
import logging
import hashlib
from typing import Optional
from cryptography.fernet import Fernet
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class AccountCrypto:
    """Шифрование/дешифрование account_id."""
    
    def __init__(self):
        """Инициализация с ключом из .env."""
        load_dotenv()
        
        key = os.getenv("ACCOUNT_ENCRYPTION_KEY")

        if not key:
            # Never log or generate the key automatically: it must be a stable secret,
            # otherwise previously encrypted account_id values become unreadable.
            raise ValueError(
                "ACCOUNT_ENCRYPTION_KEY is not set in .env. "
                "Generate it once with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\" "
                "and add it to .env as ACCOUNT_ENCRYPTION_KEY=<value>."
            )
        
        self.cipher = Fernet(key.encode())
        logger.debug("✓ AccountCrypto initialized")
    
    def encrypt(self, account_id: str) -> str:
        """Зашифровать account_id."""
        if not account_id:
            return ""
        
        try:
            encrypted = self.cipher.encrypt(account_id.encode())
            return encrypted.decode()
        except Exception as e:
            logger.error(f"❌ Encryption failed: {e}")
            raise
    
    def decrypt(self, encrypted_account_id: str) -> str:
        """Расшифровать account_id."""
        if not encrypted_account_id:
            return ""
        
        try:
            decrypted = self.cipher.decrypt(encrypted_account_id.encode())
            return decrypted.decode()
        except Exception as e:
            logger.error(f"❌ Decryption failed: {e}")
            raise
    
    @staticmethod
    def hash_account_id(account_id: str) -> str:
        """
        Создать детерминированный хеш для поиска.
        
        SHA256 всегда возвращает одинаковый результат для одного входа.
        
        Args:
            account_id: Полный номер счёта
            
        Returns:
            str: SHA256 хеш (hex)
        """
        if not account_id:
            return ""
        
        return hashlib.sha256(account_id.encode()).hexdigest()
    
    @staticmethod
    def get_short_id(account_id: str) -> str:
        """Получить последние 4 символа (для отображения)."""
        return account_id[-4:] if account_id else ""


# Singleton instance
_crypto_instance: Optional[AccountCrypto] = None

def get_crypto() -> AccountCrypto:
    """Получить singleton instance."""
    global _crypto_instance
    if _crypto_instance is None:
        _crypto_instance = AccountCrypto()
    return _crypto_instance
