"""Transaction costs for the backtest: spread, slippage, commission.

The engine applies costs to execution prices, never to the levels themselves:
TP/SL are still detected on the raw `high`/`low`, so a cost model cannot change
*whether* a trade exits, only *at what price*.

Conventions (ADR-016):

- the spread is quoted as a **full** spread; each side pays half of it. A round
  trip therefore costs one full spread plus slippage on both sides;
- slippage is a separate per-side number, quoted in the same unit as the spread;
- commission is a third line, applied per side, and stays where it already was
  (`BaseStrategy._calculate_commissions`). It is reported next to the spread and
  slippage so that a report cannot silently mix gross and net;
- entry is always a market order: half spread + slippage against the trader;
- SL and gap exits are market orders: half spread + slippage against the trader;
- TP is a limit order: no spread, no slippage. A limit fill happens at the level
  or better, so charging costs there would make wide TP look worse than it is.

A model with zero spread and zero slippage is marked `GROSS` and not tradable:
an out-of-sample report or a live candidate must run with non-zero costs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from config.commissions import COMMISSION_MODE, TRANSACTION_COMMISSION_PERCENT

logger = logging.getLogger(__name__)

DEFAULT_COSTS_PATH = Path(__file__).resolve().parents[2] / "config" / "costs.json"

# Exits executed with a market order; TP is a limit order and pays nothing.
MARKET_EXIT_TYPES = frozenset({"SL", "GAP", "TIME"})


class CostModel(Protocol):
    """Prices actually paid by the trader for an entry or an exit."""

    def entry_price(self, price: float, is_long: bool) -> float:
        """Market entry price: worse than `price` by half spread + slippage."""

    def exit_price(self, price: float, is_long: bool, exit_type: str) -> float:
        """Execution price for `exit_type`; limit (TP) exits keep `price`."""

    def describe(self) -> dict:
        """JSON-ready description of the model for reports."""

    @property
    def is_tradable(self) -> bool:
        """False when costs are all zero: the report is gross, not tradable."""


def _commission_bps_per_side() -> float:
    """Broker commission in bps per side, honouring COMMISSION_MODE=NONE."""
    if COMMISSION_MODE == "NONE":
        return 0.0
    return TRANSACTION_COMMISSION_PERCENT * 100.0


def _cost_report(trading_round_trip_bps: float) -> dict:
    """Commission lines shared by both models.

    `COMMISSION_MODE` is global and applies per direction, so the report keeps
    the commission as a separate line: `total_round_trip_bps` includes it only
    when the mode charges every trade. With SHORT_ONLY/LONG_ONLY a long-only
    strategy pays nothing, and folding 5 bps into the total would overstate the
    cost of a long report.
    """
    commission = _commission_bps_per_side()
    charged_always = COMMISSION_MODE == "ALL"
    return {
        "trading_round_trip_bps": round(trading_round_trip_bps, 4),
        "commission_mode": COMMISSION_MODE,
        "commission_bps_per_side": commission,
        "total_round_trip_bps": round(
            trading_round_trip_bps + 2.0 * commission if charged_always else trading_round_trip_bps,
            4,
        ),
    }


@dataclass(frozen=True)
class BpsCosts:
    """Costs quoted in basis points of the traded price."""

    spread_bps: float = 0.0
    slippage_bps: float = 0.0

    def _market_offset(self, price: float) -> float:
        """Absolute price deterioration of a market order on one side."""
        return abs(price) * (self.spread_bps / 2.0 + self.slippage_bps) / 10_000.0

    def entry_price(self, price: float, is_long: bool) -> float:
        offset = self._market_offset(price)
        return price + offset if is_long else price - offset

    def exit_price(self, price: float, is_long: bool, exit_type: str) -> float:
        if exit_type == "TP":
            return price
        offset = self._market_offset(price)
        return price - offset if is_long else price + offset

    @property
    def is_tradable(self) -> bool:
        return self.spread_bps > 0.0 or self.slippage_bps > 0.0

    @property
    def trading_round_trip_bps(self) -> float:
        """Full spread + slippage on both sides, excluding commission."""
        return self.spread_bps + 2.0 * self.slippage_bps

    def describe(self) -> dict:
        return {
            "mode": "bps",
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            **_cost_report(self.trading_round_trip_bps),
            "tradable": self.is_tradable,
            "label": "NET" if self.is_tradable else "GROSS",
        }


@dataclass(frozen=True)
class TickCosts:
    """Costs quoted in exchange ticks: for futures and other non-bps instruments."""

    tick_size: float
    spread_ticks: float = 0.0
    slippage_ticks: float = 0.0

    def _market_offset(self) -> float:
        """Absolute price deterioration of a market order on one side."""
        return self.tick_size * (self.spread_ticks / 2.0 + self.slippage_ticks)

    def entry_price(self, price: float, is_long: bool) -> float:
        offset = self._market_offset()
        return price + offset if is_long else price - offset

    def exit_price(self, price: float, is_long: bool, exit_type: str) -> float:
        if exit_type == "TP":
            return price
        offset = self._market_offset()
        return price - offset if is_long else price + offset

    @property
    def is_tradable(self) -> bool:
        return self.spread_ticks > 0.0 or self.slippage_ticks > 0.0

    def describe(self) -> dict:
        return {
            "mode": "ticks",
            "tick_size": self.tick_size,
            "spread_ticks": self.spread_ticks,
            "slippage_ticks": self.slippage_ticks,
            "market_offset_per_side": round(self._market_offset(), 6),
            # Ticks cannot be converted to bps without a price, so the
            # commission is reported as a separate line only.
            "commission_mode": COMMISSION_MODE,
            "commission_bps_per_side": _commission_bps_per_side(),
            "tradable": self.is_tradable,
            "label": "NET" if self.is_tradable else "GROSS",
        }


# Default when no cost model is supplied: historical backtests stay reproducible.
ZERO_COSTS = BpsCosts()


def build_costs(spec: dict) -> CostModel:
    """Build a cost model from one entry of config/costs.json.

    Raises:
        ValueError: unknown mode, missing tick_size or negative values.
    """
    mode = spec.get("mode", "bps")
    if mode == "bps":
        spread = float(spec.get("spread_bps", 0.0))
        slippage = float(spec.get("slippage_bps", 0.0))
        if spread < 0 or slippage < 0:
            raise ValueError("costs must be non-negative")
        return BpsCosts(spread_bps=spread, slippage_bps=slippage)

    if mode == "ticks":
        tick_size = float(spec.get("tick_size", 0.0))
        spread = float(spec.get("spread_ticks", 0.0))
        slippage = float(spec.get("slippage_ticks", 0.0))
        if tick_size <= 0:
            raise ValueError("tick_size must be positive for mode='ticks'")
        if spread < 0 or slippage < 0:
            raise ValueError("costs must be non-negative")
        return TickCosts(
            tick_size=tick_size, spread_ticks=spread, slippage_ticks=slippage
        )

    raise ValueError(f"unknown cost mode: {mode}")


@dataclass(frozen=True)
class CostConfig:
    """Parsed config/costs.json: defaults, per-instrument overrides, fingerprint."""

    version: str
    default_spec: dict
    instruments: dict[str, dict]
    sha256: str

    def for_ticker(self, ticker: str | None) -> CostModel:
        """Cost model for a ticker; unknown tickers fall back to the default."""
        spec = self.default_spec
        if ticker:
            override = self.instruments.get(ticker.upper())
            if override is not None:
                # An override may set only the spread; slippage then comes
                # from the default block.
                spec = {**self.default_spec, **override}
        return build_costs(spec)

    def describe(self, ticker: str | None) -> dict:
        """Model description plus the config fingerprint."""
        return {
            "costs_version": f"{self.version}+{self.sha256[:12]}",
            "ticker": ticker,
            **self.for_ticker(ticker).describe(),
        }


_cache: dict[Path, CostConfig] = {}


def load_costs(path: Path | str = DEFAULT_COSTS_PATH) -> CostConfig:
    """Read and cache config/costs.json.

    Raises:
        FileNotFoundError: the file is missing.
        ValueError: the file is not valid JSON or has no `default` block.
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

    default_spec = data.get("default")
    if not isinstance(default_spec, dict):
        raise ValueError(f"{resolved} must contain a 'default' object")

    # Fingerprint the payload, not the file bytes: reformatting the JSON must not
    # change the version, but any parameter change must.
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    config = CostConfig(
        version=str(data.get("version", "0")),
        default_spec=default_spec,
        instruments={
            str(ticker).upper(): spec
            for ticker, spec in (data.get("instruments") or {}).items()
        },
        sha256=digest,
    )
    _cache[resolved] = config
    logger.debug(f"Loaded cost config {resolved} (version {config.version})")
    return config


def clear_costs_cache() -> None:
    """Drop the cache; used by tests that rewrite the config file."""
    _cache.clear()
