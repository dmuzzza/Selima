"""
Конфигурация паттернов для CandlePatternStrategy.
Изменяй пороги здесь без редактирования основной логики.
"""

PATTERNS_CONFIG = {
    "doji": {
        "enabled": False,
        "body_threshold": 0.07,  # 7% от диапазона
    },
    
    "hammer": {
        "enabled": False,
        "shadow_ratio": 3.0,  # Нижняя тень >= 2.0x тела
        "upper_shadow_max": 0.1,  # Верхняя тень <= 10% диапазона
    },
    
    "shooting_star": {
        "enabled": False,
        "shadow_ratio": 2.0,  # Верхняя тень >= 2.0x тела
        "lower_shadow_max": 0.1,  # Нижняя тень <= 10% диапазона
    },
    
    "engulfing": {
        "enabled": False,
    },
}