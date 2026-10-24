import logging
from .base import AbstractBroker
from .sdk_broker import TinkoffSDKBroker

logger = logging.getLogger(__name__)


class BrokerFactory:
    """Фабрика для создания брокеров"""
    
    @staticmethod
    def create(broker_type: str, token: str) -> AbstractBroker:
        """
        Создать брокер нужного типа
        
        Args:
            broker_type: "sdk" или "grpc"
            token: T-Invest API токен
        
        Returns:
            AbstractBroker экземпляр (TinkoffSDKBroker или TinkoffGRPCBroker)
        
        Примеры:
            broker = BrokerFactory.create("sdk", token)
            broker = BrokerFactory.create("grpc", token)  # Не реализовано пока
        """
        broker_type = broker_type.lower().strip()
        
        if broker_type == "sdk":
            logger.info("Creating TinkoffSDKBroker...")
            return TinkoffSDKBroker(token)
        
        elif broker_type == "grpc":
            logger.warning("gRPC broker not implemented yet, falling back to SDK")
            return TinkoffSDKBroker(token)
        
        else:
            raise ValueError(
                f"Unknown broker type: '{broker_type}'. "
                f"Use 'sdk' or 'grpc'"
            )
