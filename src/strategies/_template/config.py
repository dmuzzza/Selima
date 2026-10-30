"""
Template Strategy Configuration

Copy this file together with the package and replace the parameters.
TP/SL do not live here: they are per-strategy data in `config/tp_sl.json`
(ADR-019). Add a `template` block there (or a block under your own strategy
code) — a missing key is an error, there is no fallback pair.
"""

DEFAULT_CONFIG = {
    # Portfolio
    "initial_balance": 450000.0,
    "position_size_percent": 100.0,
    "allow_short": False,
    # Strategy parameters
    "sma_period": 50,
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
        dict: Configuration; TP/SL are resolved by `BaseStrategy` from
        `config/tp_sl.json` unless given explicitly in `custom_config`
    """
    config = get_default_config()

    if custom_config:
        config.update(custom_config)

    return config
