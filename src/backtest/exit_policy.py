"""Exit policy: where TP/SL levels come from (T3-4, ADR-019).

Before T3-4 every strategy carried its own TP/SL table in `DEFAULT_CONFIG` and
`BaseStrategy` silently fell back to `1.5 / 1.0` when a ticker was missing. The
live scheduler did exactly that: it built the strategy without a ticker, so every
instrument traded with the fallback pair instead of its configured levels.

Now:

- `ExitPolicy` is a protocol: the simulator only asks for `levels(price, is_long)`;
- `PercentExitPolicy` is the only implementation shipped: fixed TP/SL percentages;
- the data lives in `config/tp_sl.json` (versioned, fingerprinted) and is loaded
  by `load_exit_policies()`;
- a missing strategy/timeframe/ticker is `MissingExitPolicyError`, never a default;
- explicit `tp_percent` + `sl_percent` in the strategy config still win: that is
  how walk-forward passes a grid point. Setting only one of them is an error.

Levels are always built from the price the policy is given. The simulator passes
the **raw** candle open, so costs cannot move the levels (ADR-017).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_EXIT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "tp_sl.json"


class MissingExitPolicyError(LookupError):
    """No TP/SL configured for a strategy/timeframe/ticker combination."""


class ExitPolicy(Protocol):
    """How take profit and stop loss levels are derived from an entry price."""

    @property
    def tp_percent(self) -> float:
        """Take profit distance, percent of the entry price."""

    @property
    def sl_percent(self) -> float:
        """Stop loss distance, percent of the entry price."""

    def levels(self, entry_price: float, is_long: bool) -> tuple[float, float]:
        """Return `(tp_level, sl_level)` for the given entry price."""

    def describe(self) -> dict:
        """JSON-ready description for reports."""


@dataclass(frozen=True)
class PercentExitPolicy:
    """Take profit and stop loss as fixed percentages of the entry price."""

    tp_percent: float
    sl_percent: float
    source: str = "config"
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.tp_percent <= 0 or self.sl_percent <= 0:
            raise ValueError(
                f"TP/SL percentages must be positive: "
                f"tp={self.tp_percent}, sl={self.sl_percent} (source={self.source})"
            )

    def levels(self, entry_price: float, is_long: bool) -> tuple[float, float]:
        """Return `(tp_level, sl_level)`; longs go up, shorts go down."""
        if is_long:
            tp_level = entry_price * (1 + self.tp_percent / 100.0)
            sl_level = entry_price * (1 - self.sl_percent / 100.0)
        else:
            tp_level = entry_price * (1 - self.tp_percent / 100.0)
            sl_level = entry_price * (1 + self.sl_percent / 100.0)
        return tp_level, sl_level

    def describe(self) -> dict:
        return {
            "mode": "percent",
            "tp_percent": self.tp_percent,
            "sl_percent": self.sl_percent,
            "source": self.source,
            "tp_sl_version": self.fingerprint,
        }


def _pair(pair: dict, *, where: str) -> tuple[float, float]:
    """Validate one TP/SL pair from the config file."""
    missing = [name for name in ("tp_percent", "sl_percent") if name not in pair]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    return float(pair["tp_percent"]), float(pair["sl_percent"])


@dataclass(frozen=True)
class ExitPolicyConfig:
    """Parsed config/tp_sl.json: per-strategy tables and a fingerprint."""

    version: str
    strategies: dict[str, dict]
    sha256: str
    path: str = str(DEFAULT_EXIT_POLICY_PATH)

    @property
    def fingerprint(self) -> str:
        """`version+sha12`, the same shape as `costs_version`."""
        return f"{self.version}+{self.sha256[:12]}"

    def for_strategy(
        self, strategy_code: str, timeframe: str, ticker: str | None
    ) -> PercentExitPolicy:
        """Resolve the policy; raise `MissingExitPolicyError` if the key is absent."""
        block = self.strategies.get(strategy_code)
        if block is None:
            raise MissingExitPolicyError(
                f"no TP/SL block for strategy '{strategy_code}' in {self.path}; "
                f"known strategies: {', '.join(sorted(self.strategies)) or '<none>'}"
            )

        mode = block.get("mode")
        if mode == "by_ticker":
            table = block.get("by_ticker") or {}
            if timeframe not in table:
                raise MissingExitPolicyError(
                    f"no TP/SL for strategy '{strategy_code}' timeframe '{timeframe}' "
                    f"in {self.path}; known timeframes: {', '.join(sorted(table)) or '<none>'}"
                )
            tickers = table[timeframe]
            if not ticker:
                raise MissingExitPolicyError(
                    f"strategy '{strategy_code}' needs a ticker on {timeframe}: "
                    f"its TP/SL table is per instrument "
                    f"({', '.join(sorted(tickers))})"
                )
            key = ticker.upper()
            pair = tickers.get(key)
            if pair is None:
                raise MissingExitPolicyError(
                    f"no TP/SL for ticker '{key}' ({strategy_code}, {timeframe}) in "
                    f"{self.path}; known tickers: {', '.join(sorted(tickers)) or '<none>'}"
                )
            tp, sl = _pair(pair, where=f"{strategy_code}/{timeframe}/{key}")
            return PercentExitPolicy(
                tp, sl, source=f"tp_sl.json:{timeframe}/{key}", fingerprint=self.fingerprint
            )

        if mode == "by_timeframe":
            table = block.get("by_timeframe") or {}
            pair = table.get(timeframe)
            if pair is None:
                raise MissingExitPolicyError(
                    f"no TP/SL for strategy '{strategy_code}' timeframe '{timeframe}' "
                    f"in {self.path}; known timeframes: {', '.join(sorted(table)) or '<none>'}"
                )
            tp, sl = _pair(pair, where=f"{strategy_code}/{timeframe}")
            return PercentExitPolicy(
                tp, sl, source=f"tp_sl.json:{timeframe}", fingerprint=self.fingerprint
            )

        raise ValueError(
            f"strategy '{strategy_code}' in {self.path}: unknown mode {mode!r} "
            "(expected 'by_ticker' or 'by_timeframe')"
        )

    def describe(self, strategy_code: str, timeframe: str, ticker: str | None) -> dict:
        """Policy description (including the config fingerprint) plus context."""
        return {
            "strategy": strategy_code,
            "timeframe": timeframe,
            "ticker": ticker,
            **self.for_strategy(strategy_code, timeframe, ticker).describe(),
        }


_cache: dict[Path, ExitPolicyConfig] = {}


def load_exit_policies(path: Path | str = DEFAULT_EXIT_POLICY_PATH) -> ExitPolicyConfig:
    """Read and cache config/tp_sl.json.

    Raises:
        FileNotFoundError: the file is missing.
        ValueError: invalid JSON, missing `strategies` block or a malformed pair.
    """
    resolved = Path(path).resolve()
    cached = _cache.get(resolved)
    if cached is not None:
        return cached

    raw = resolved.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {resolved}: {exc}") from exc

    strategies = data.get("strategies")
    if not isinstance(strategies, dict) or not strategies:
        raise ValueError(f"{resolved} must contain a non-empty 'strategies' object")

    # Fingerprint the payload, not the file bytes: reformatting must not change
    # the version, any parameter change must.
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    config = ExitPolicyConfig(
        version=str(data.get("version", "0")),
        strategies=strategies,
        sha256=digest,
        path=str(resolved),
    )
    _cache[resolved] = config
    logger.debug(f"Loaded TP/SL config {resolved} (version {config.version})")
    return config


def clear_exit_policy_cache() -> None:
    """Drop the cache; used by tests that rewrite the config file."""
    _cache.clear()


def resolve_exit_policy(
    *,
    strategy_code: str,
    timeframe: str,
    ticker: str | None = None,
    config: dict | None = None,
    path: Path | str = DEFAULT_EXIT_POLICY_PATH,
) -> PercentExitPolicy:
    """Pick the policy for one strategy run.

    Order:

    1. explicit `tp_percent` **and** `sl_percent` in `config` (walk-forward grid,
       tests) — source `config`;
    2. `config/tp_sl.json` for `(strategy_code, timeframe, ticker)`.

    Raises:
        ValueError: only one of `tp_percent`/`sl_percent` was given.
        MissingExitPolicyError: the file has no matching key.
    """
    settings = config or {}
    tp_percent = settings.get("tp_percent")
    sl_percent = settings.get("sl_percent")

    if tp_percent is not None or sl_percent is not None:
        if tp_percent is None or sl_percent is None:
            raise ValueError(
                "tp_percent and sl_percent must be set together "
                f"(strategy '{strategy_code}', timeframe '{timeframe}'): "
                f"tp={tp_percent}, sl={sl_percent}"
            )
        return PercentExitPolicy(
            float(tp_percent), float(sl_percent), source="config"
        )

    return load_exit_policies(path).for_strategy(strategy_code, timeframe, ticker)
