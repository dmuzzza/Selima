"""Trading mode resolution from environment.

The mode is never hardcoded: it comes from ``TRADING_MODE`` and defaults to
``dry_run``, so a forgotten flag can never open a real position.

Modes
-----
``dry_run``
    Signals are detected and logged, but no orders are sent to the broker.
``live``
    Real orders. Requires ``LIVE_CONFIRM=yes`` as a second, explicit switch.
"""

import os
from enum import Enum
from typing import Mapping

__all__ = ["TradingMode", "TradingModeError", "resolve_trading_mode"]


class TradingMode(str, Enum):
    """How the scheduler is allowed to act on signals."""

    DRY_RUN = "dry_run"
    LIVE = "live"

    @property
    def is_live(self) -> bool:
        """True when real orders are allowed."""
        return self is TradingMode.LIVE


class TradingModeError(ValueError):
    """Raised when the configured trading mode is invalid or unsafe."""


def resolve_trading_mode(environ: Mapping[str, str] | None = None) -> TradingMode:
    """Read and validate the trading mode.

    Args:
        environ: Environment mapping; defaults to ``os.environ`` (useful in tests).

    Returns:
        TradingMode: resolved mode, ``dry_run`` when ``TRADING_MODE`` is unset.

    Raises:
        TradingModeError: unknown value, or ``live`` without ``LIVE_CONFIRM=yes``.
    """
    env = os.environ if environ is None else environ

    raw = (env.get("TRADING_MODE") or "").strip().lower()
    if not raw:
        raw = TradingMode.DRY_RUN.value

    try:
        mode = TradingMode(raw)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in TradingMode)
        raise TradingModeError(
            f"Unknown TRADING_MODE='{raw}'. Allowed values: {allowed}."
        ) from exc

    if mode.is_live:
        confirm = (env.get("LIVE_CONFIRM") or "").strip().lower()
        if confirm != "yes":
            raise TradingModeError(
                "TRADING_MODE=live requires LIVE_CONFIRM=yes. "
                "Refusing to start with real money enabled implicitly."
            )

    return mode
