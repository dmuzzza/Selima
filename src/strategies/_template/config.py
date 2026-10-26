"""
Template Strategy Configuration

Copy this file together with the package and replace the parameters.
TP/SL live here, not in the strategy code: they are per-timeframe data, not logic.
"""

DEFAULT_CONFIG = {
    # Portfolio
    "initial_balance": 450000.0,
    "position_size_percent": 100.0,
    "allow_short": False,
    # Strategy parameters
    "sma_period": 50,
    # TP/SL per timeframe
    "timeframes": {
        "1h": {"tp_percent": 1.2, "sl_percent": 0.7},
        "4h": {"tp_percent": 3.5, "sl_percent": 1.0},
        "1d": {"tp_percent": 3.5, "sl_percent": 2.6},
    },
}


def get_default_config() -> dict:
    """Return a copy of the default configuration."""
    return DEFAULT_CONFIG.copy()


def get_config(timeframe: str, custom_config: dict | None = None) -> dict:
    """
    Build the configuration for a concrete timeframe.

    Args:
        timeframe: Timeframe code (e.g. "1h", "4h", "1d")
        custom_config: Overrides applied on top of the defaults

    Returns:
        dict: Configuration with `tp_percent`/`sl_percent` resolved for the timeframe
    """
    config = get_default_config()

    if custom_config:
        config.update(custom_config)

    tf_config = config["timeframes"].get(timeframe, {})
    config["tp_percent"] = tf_config.get("tp_percent", 1.5)
    config["sl_percent"] = tf_config.get("sl_percent", 1.0)

    return config
