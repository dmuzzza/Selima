"""Configuration of the null-entry strategy (T2-18).

TP/SL are passed explicitly by the caller (`tp_percent`/`sl_percent`), which is
the documented override path of `resolve_exit_policy()` (ADR-019): the null test
must use the same exits as the strategy under test, not a config file lookup.
"""

DEFAULT_CONFIG = {
    "initial_balance": 100_000.0,
    "position_size_percent": 100.0,
    "allow_short": True,
    # Share of candles that produce an entry signal.
    "entry_rate": 0.05,
    # Seed of the deterministic pseudo-random generator.
    "seed": 0,
    # Warmup: indicators-free strategy, but skipping the first bars keeps the
    # comparison with a real strategy honest.
    "warmup_bars": 0,
}


def get_config(custom_config: dict | None = None) -> dict:
    """Return the default configuration with overrides applied."""
    config = DEFAULT_CONFIG.copy()
    if custom_config:
        config.update(custom_config)
    return config
