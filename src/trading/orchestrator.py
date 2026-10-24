"""
Trading Orchestrator

Координирует процесс реальной торговли:
1. Получает сигналы от стратегии
2. Рассчитывает размер позиций (PositionManager)
3. Открывает сделки (TradeExecutor)
4. Проверяет исполнение
5. Сохраняет в БД
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from src.strategies.base_strategy import Signal
from src.brokers.position_manager import PositionManager
from src.brokers.trade_executor import TradeExecutor
from src.utils.masking import mask_account_id

from .models import LiveTrade, TradeStatus, build_signal_key
from .repository import LiveTradeRepository

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """
    Координатор торговли.
    
    Связывает стратегию, расчёт позиций, исполнение и БД.
    """
    
    def __init__(
        self,
        token: str,
        account_id: str,
        strategy_name: str,
        timeframe: str,  # ← НОВЫЙ ПАРАМЕТР
        db_path: str = "data/live_trades.db",
        repository=None,
    ):
        """
        Инициализация координатора.
        
        Args:
            token: T-Invest API токен
            account_id: ID счёта для торговли
            strategy_name: Название стратегии (для логирования)
            timeframe: Таймфрейм стратегии ("1h", "4h", "1d")
            db_path: Путь к БД сделок (используется, если не передан repository)
            repository: Готовый репозиторий сделок (например PgLiveTradeRepository)
        """
        self.token = token
        self.account_id = account_id
        self.strategy_name = strategy_name
        self.timeframe = timeframe  # ← СОХРАНЯЕМ
        
        # Компоненты
        self.position_manager = PositionManager(token, account_id)
        self.trade_executor = TradeExecutor(token, account_id)
        self.repository = repository or LiveTradeRepository(db_path)
        
        logger.info(
            f"✓ TradingOrchestrator initialized: "
            f"strategy={strategy_name}, timeframe={timeframe}, "
            f"account={mask_account_id(account_id)}"
        )

    def get_position_id(self, figi: str, max_retries: int = 3) -> Optional[str]:
        """
        Получает position_id по FIGI.
        
        Args:
            figi: FIGI инструмента
            max_retries: Количество попыток (с задержкой)
            
        Returns:
            position_uid или None
        """
        import time
        from t_tech.invest import Client
        
        for attempt in range(max_retries):
            try:
                # Небольшая задержка перед каждой попыткой (кроме первой)
                if attempt > 0:
                    delay = 0.5 * attempt  # 0.5s, 1s
                    logger.debug(f"Retry #{attempt}: waiting {delay}s before checking position...")
                    time.sleep(delay)
                
                with Client(self.token) as client:
                    response = client.operations.get_positions(account_id=self.account_id)
                    
                    # Ищем позицию по FIGI
                    for position in response.securities:
                        if position.figi == figi:
                            # Проверяем balance (может быть 0 для SHORT?)
                            if position.balance != 0:
                                if hasattr(position, 'position_uid'):
                                    logger.info(f"✓ Position ID found: {position.position_uid} (attempt #{attempt + 1})")
                                    return position.position_uid
                            else:
                                logger.debug(f"Found position with balance=0: {figi}")
                    
                    logger.warning(f"Position not found for {figi} (attempt #{attempt + 1}/{max_retries})")
            
            except Exception as e:
                logger.error(f"Error getting position_id (attempt #{attempt + 1}): {e}")
        
        # Не нашли даже после retries
        logger.error(f"❌ Failed to get position_id for {figi} after {max_retries} attempts")
        return None


    def process_signals(
        self,
        signals: List[Signal],
        ticker: str,
        figi: str,
        tp_percent: float,
        sl_percent: float
    ) -> List[LiveTrade]:
        """
        Обработать сигналы от стратегии.
        
        Args:
            signals: Список сигналов от стратегии
            ticker: Тикер инструмента (SBER, GAZP)
            figi: FIGI код инструмента
            tp_percent: Take Profit в процентах
            sl_percent: Stop Loss в процентах
            
        Returns:
            List[LiveTrade]: Список созданных сделок
        """
        live_trades = []
        
        for signal in signals:
            logger.info(f"\n{'='*70}")
            logger.info(f"🔔 Processing signal: {signal}")
            
            try:
                trade = self._process_single_signal(
                    signal=signal,
                    ticker=ticker,
                    figi=figi,
                    tp_percent=tp_percent,
                    sl_percent=sl_percent
                )
                
                if trade:
                    live_trades.append(trade)
                    logger.info(f"✅ Trade processed: {trade.trade_id}")
                else:
                    logger.warning(f"⚠️ Trade not created for signal: {signal}")
                    
            except Exception as e:
                logger.error(f"✗ Error processing signal {signal}: {e}")
                
            logger.info(f"{'='*70}\n")
        
        logger.info(f"📊 Processed {len(signals)} signals → {len(live_trades)} trades created")
        return live_trades
    
    def _process_single_signal(
        self,
        signal: Signal,
        ticker: str,
        figi: str,
        tp_percent: float,
        sl_percent: float
    ) -> Optional[LiveTrade]:
        """
        Обработать один сигнал.
        
        Returns:
            LiveTrade или None если не удалось создать
        """
        # ═══════════════════════════════════════════════════════════
        # 0. ПРОВЕРКА ДУБЛИРОВАНИЯ ПОЗИЦИЙ ← НОВЫЙ БЛОК!
        # ═══════════════════════════════════════════════════════════
        
        # Проверяем, есть ли уже открытая позиция по этому инструменту
        existing_trades = self.repository.get_open_trades_by_ticker(
            ticker=ticker,
            account_id=self.account_id,
            timeframe=self.timeframe
        )
        
        if existing_trades:
            logger.warning(
                f"⚠️ DUPLICATE PREVENTED: Already have {len(existing_trades)} "
                f"open position(s) for {ticker} on {self.timeframe} timeframe"
            )
            for trade in existing_trades:
                logger.info(
                    f"   Existing: {trade.direction} @ {trade.entry_price:.2f}₽ "
                    f"(opened {trade.entry_time})"
                )
            return None  # ← НЕ открываем дубль!


        # ═══════════════════════════════════════════════════════════
        # 1. СОЗДАЁМ ЗАПИСЬ В БД (статус PENDING)
        #    signal_key защищает от повторной обработки того же сигнала
        # ═══════════════════════════════════════════════════════════
        trade_id = f"{ticker}_{signal.signal_type}_{uuid.uuid4().hex[:8]}"
        direction = "LONG" if signal.signal_type == "BULLISH" else "SHORT"
        signal_key = build_signal_key(
            figi=figi,
            timeframe=self.timeframe,
            candle_open_time=signal.timestamp,
            direction=direction,
        )

        live_trade = LiveTrade(
            trade_id=trade_id,
            strategy_name=self.strategy_name,
            timeframe=self.timeframe,
            timestamp=datetime.now(),
            ticker=ticker,
            figi=figi,
            direction=direction,
            signal_key=signal_key,
            tp_percent=tp_percent,
            sl_percent=sl_percent,
            status=TradeStatus.PENDING,
            account_id=self.account_id,
            metadata={"signal": signal.metadata or {}}
        )

        create_pending = getattr(self.repository, "create_pending_if_absent", None)
        if create_pending is not None:
            if not create_pending(live_trade):
                logger.warning(f"🚫 Signal already processed, skipping: {signal_key}")
                return None
        else:
            self.repository.save(live_trade)
            logger.info(f"📝 Trade record created: {trade_id} (PENDING)")
        
        # ═══════════════════════════════════════════════════════════
        # 2. РАССЧИТЫВАЕМ ПАРАМЕТРЫ ПОЗИЦИИ
        # ═══════════════════════════════════════════════════════════
        trade_params = self.position_manager.calculate_trade_parameters(
            ticker=ticker,
            figi=figi,
            direction=direction,
            strategy_name=self.strategy_name,
            tp_percent=tp_percent,
            sl_percent=sl_percent
        )
        
        if not trade_params:
            live_trade.status = TradeStatus.FAILED
            live_trade.error_message = "Cannot calculate trade parameters (limits exceeded)"
            # Ордер не отправлялся — освобождаем ключ, чтобы сигнал можно было
            # обработать повторно на следующем цикле.
            live_trade.signal_key = None
            self.repository.save(live_trade)
            return live_trade
        
        logger.info(f"💰 Position size: {trade_params.lots} lots = {trade_params.position_size:,.2f}₽")
        
        # ═══════════════════════════════════════════════════════════
        # 3. ОТКРЫВАЕМ ПОЗИЦИЮ
        # ═══════════════════════════════════════════════════════════
        execution_result = self.trade_executor.execute_trade(trade_params)
        
        if not execution_result.success:
            live_trade.status = TradeStatus.FAILED
            live_trade.error_message = execution_result.error_message
            self.repository.save(live_trade)
            return live_trade
        
        # ═══════════════════════════════════════════════════════════
        # 4. ОБНОВЛЯЕМ ЗАПИСЬ (статус OPEN)
        # ═══════════════════════════════════════════════════════════
        live_trade.status = TradeStatus.OPEN
        live_trade.entry_time = datetime.now()
        live_trade.entry_price = execution_result.execution_price
        live_trade.lots = execution_result.executed_lots
        live_trade.lot_size = trade_params.lot_size
        live_trade.position_size = execution_result.executed_lots * trade_params.lot_value
        live_trade.market_order_id = execution_result.market_order_id
        live_trade.tp_order_id = execution_result.tp_order_id
        live_trade.sl_order_id = execution_result.sl_order_id
        live_trade.tp_price = execution_result.tp_price
        live_trade.sl_price = execution_result.sl_price

        # ⭐ ПОЛУЧАЕМ position_id
        position_id = self.get_position_id(figi)
        if position_id:
            live_trade.position_id = position_id
            logger.info(f"📝 Position ID: {position_id}")
        else:
            logger.warning(f"⚠️ Could not get position_id for {ticker}")
        
        self.repository.save(live_trade)
        logger.info(f"✅ Trade OPEN: {trade_id}")
        
        return live_trade
    
    def get_open_trades(self) -> List[LiveTrade]:
        """
        Получить все открытые сделки.
        
        Returns:
            List[LiveTrade]: Список открытых сделок
        """
        return self.repository.get_open_trades()
