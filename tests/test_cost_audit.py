"""Cost audit: attribution must not depend on the auditor's environment (ADR-018)."""

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from config import commissions as commissions_config

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cost_audit", ROOT / "scripts" / "cost_audit.py")
assert SPEC and SPEC.loader
cost_audit = importlib.util.module_from_spec(SPEC)
# dataclasses resolve annotations through sys.modules, so register before exec.
sys.modules[SPEC.name] = cost_audit
SPEC.loader.exec_module(cost_audit)

INITIAL_BALANCE = 100_000.0


def write_snapshot(
    path: Path,
    trades: list[str],
    *,
    total_pnl: float,
    costs: dict,
    margin_short_uncovered: bool | None = None,
) -> Path:
    """Minimal baseline snapshot, enough for `replay()`."""
    payload = {
        "strategy": "test",
        "tag": path.stem,
        "costs": {"label": "NET", **costs},
        "runs": 1,
        "total_trades": len(trades),
        "total_pnl": total_pnl,
        "results": [
            {
                "ticker": "TEST",
                "figi": "TEST",
                "timeframe": "4h",
                "status": "ok",
                "costs": {"label": "NET", **costs},
                "initial_balance": INITIAL_BALANCE,
                "position_size_percent": 100.0,
                "trades": len(trades),
                "closed": len(trades),
                "total_pnl": total_pnl,
                "trades_sha256": "",
                "trade_list": trades,
            }
        ],
    }
    if margin_short_uncovered is not None:
        payload["commissions"] = {
            "mode": costs.get("commission_mode", "SHORT_ONLY"),
            "margin_short_uncovered": margin_short_uncovered,
        }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def fingerprint(
    entry: datetime,
    entry_price: float,
    exit_: datetime,
    exit_type: str,
    exit_price: float,
    pnl: float,
    reason: str | None = None,
) -> str:
    parts = [
        entry.isoformat(),
        repr(entry_price),
        exit_.isoformat(),
        exit_type,
        repr(exit_price),
        repr(pnl),
    ]
    if reason is not None:
        parts.append(reason)
    return "|".join(parts)


def test_replay_uses_the_margin_model_recorded_in_the_snapshot(tmp_path, monkeypatch):
    """A =0 snapshot must not be attributed with the =1 default (the audit bug)."""
    # Short held over two nights: 100 000 notional -> 75 RUB per night.
    trade = fingerprint(
        datetime(2026, 1, 5, 10),
        100.0,
        datetime(2026, 1, 7, 10),
        "TP",
        95.0,
        0.0,
        "TP",
    )
    covered = write_snapshot(
        tmp_path / "covered.json",
        [trade],
        total_pnl=0.0,
        costs={"mode": "bps", "commission_mode": "ALL"},
        margin_short_uncovered=False,
    )
    uncovered = write_snapshot(
        tmp_path / "uncovered.json",
        [trade],
        total_pnl=0.0,
        costs={"mode": "bps", "commission_mode": "ALL"},
        margin_short_uncovered=True,
    )

    monkeypatch.setattr(commissions_config, "MARGIN_SHORT_UNCOVERED", True)
    assert cost_audit.replay(covered, "covered").trades[0].margin == 0.0
    assert cost_audit.replay(uncovered, "uncovered").trades[0].margin == pytest.approx(150.0)


def test_replay_falls_back_to_the_process_default_for_old_snapshots(tmp_path, monkeypatch):
    """Snapshots written before ADR-018 carry no model; the env still applies."""
    trade = fingerprint(
        datetime(2026, 1, 5, 10),
        100.0,
        datetime(2026, 1, 7, 10),
        "TP",
        95.0,
        0.0,
    )
    old = write_snapshot(
        tmp_path / "old.json",
        [trade],
        total_pnl=0.0,
        costs={"mode": "bps", "commission_mode": "ALL"},
    )

    monkeypatch.setattr(commissions_config, "MARGIN_SHORT_UNCOVERED", True)
    run = cost_audit.replay(old, "old")
    assert run.short_uncovered is None
    assert run.trades[0].margin == pytest.approx(150.0)


def test_market_cost_is_attributed_without_a_phantom_price_effect(tmp_path):
    """Inverting the bps offset must divide, not subtract (a fake +4 RUB bucket).

    The offset is deliberately large (100 bps per side): subtraction is a
    second-order error, so a 10 bps offset would hide it inside the tolerance.
    """
    entry_time, exit_time = datetime(2026, 1, 5, 10), datetime(2026, 1, 5, 14)
    side_bps = 100.0
    # Long, TP as a limit: the raw open is 100, the fill pays `side_bps`.
    gross = write_snapshot(
        tmp_path / "gross.json",
        [fingerprint(entry_time, 100.0, exit_time, "TP", 110.0, 10_000.0)],
        total_pnl=10_000.0,
        costs={"mode": "bps"},
    )
    net_entry = 100.0 * (1 + side_bps / 10_000)
    net_pnl = INITIAL_BALANCE / net_entry * (110.0 - net_entry)
    net = write_snapshot(
        tmp_path / "net.json",
        [fingerprint(entry_time, net_entry, exit_time, "TP", 110.0, net_pnl)],
        total_pnl=net_pnl,
        costs={"mode": "bps", "spread_bps": 2 * side_bps, "slippage_bps": 0.0},
    )

    base = cost_audit.replay(gross, "gross")
    with_costs = cost_audit.replay(net, "net")
    buckets = cost_audit.attribute_quiet(base, with_costs, reference=base)

    # The level did not move, so the whole delta is market cost, not price.
    assert buckets["price"] == pytest.approx(0.0, abs=0.01)
    assert buckets["market"] == pytest.approx(net_pnl - 10_000.0, abs=0.01)
    assert buckets["residual"] == pytest.approx(0.0, abs=0.01)
