"""TP/SL heatmap: is a good result a plateau or a single lucky point?

A grid search that reports only the best cell says nothing about robustness: a
single point can be good by chance, and a neighbour one step away can already be
negative. This module scores every grid cell, picks the best one and checks its
neighbourhood:

    plateau = best cell is positive AND >= `plateau_share` of its neighbours
              (±1 step on each axis) stay within `plateau_margin_percent` of it

Cells below `min_trades_per_cell` do not take part in the decision and are
marked in the report: two trades are not evidence. The thresholds live in
`config/validation.json` and are printed in every report together with the
fingerprint, so a verdict can be reproduced.

Two blocks, deliberately separated:

- `collect_train_trades()` — cells scored on walk-forward *train* windows. This
  is the only block the decision may use: the parameters never see the data they
  are judged on (ADR-015);
- `collect_full_history_trades()` — cells scored on the whole history. It is
  diagnostics only: the same bars selected the best cell and graded it.

A plateau does not prove an edge. It only says the result does not rest on one
parameter point; the final verdict is the job of the T2-21 report (net PnL, CI,
OOS, null test).
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.backtest.walk_forward import StrategyRunner, WalkForwardWindow
from src.market_data.models import Candle
from src.strategies.base_strategy import Trade

GridPoint = tuple[float, float]

# Defaults mirror config/validation.json and are used only when the file is not
# passed explicitly; reports always print the effective values.
DEFAULT_MIN_TRADES_PER_CELL = 30
DEFAULT_PLATEAU_MARGIN_PERCENT = 20.0
DEFAULT_PLATEAU_SHARE = 0.65
DEFAULT_MIN_NEIGHBOURS_EVALUATED = 3


@dataclass(frozen=True)
class HeatmapThresholds:
    """Decision thresholds plus provenance, so a report is reproducible."""

    min_trades_per_cell: int = DEFAULT_MIN_TRADES_PER_CELL
    plateau_margin_percent: float = DEFAULT_PLATEAU_MARGIN_PERCENT
    plateau_share: float = DEFAULT_PLATEAU_SHARE
    min_neighbours_evaluated: int = DEFAULT_MIN_NEIGHBOURS_EVALUATED
    source: str = "defaults"
    fingerprint: str = "defaults"

    def __post_init__(self) -> None:
        if self.min_trades_per_cell < 1:
            raise ValueError("min_trades_per_cell must be positive")
        if not 0.0 <= self.plateau_margin_percent < 100.0:
            raise ValueError("plateau_margin_percent must be in [0, 100)")
        if not 0.0 < self.plateau_share <= 1.0:
            raise ValueError("plateau_share must be in (0, 1]")
        if self.min_neighbours_evaluated < 1:
            raise ValueError("min_neighbours_evaluated must be positive")

    def describe(self) -> dict:
        return {
            "min_trades_per_cell": self.min_trades_per_cell,
            "plateau_margin_percent": self.plateau_margin_percent,
            "plateau_share": self.plateau_share,
            "min_neighbours_evaluated": self.min_neighbours_evaluated,
            "thresholds_version": self.fingerprint,
            "thresholds_source": self.source,
        }


def load_thresholds(path: str | Path) -> HeatmapThresholds:
    """Read the `heatmap` section of config/validation.json.

    The fingerprint is `<version>+<sha256[:12]>` of the file, the same convention
    as `config/tp_sl.json` and `config/costs.json`.
    """
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    section = payload.get("heatmap")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: missing 'heatmap' section")
    version = str(payload.get("version", ""))
    if not version:
        raise ValueError(f"{path}: missing 'version'")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return HeatmapThresholds(
        min_trades_per_cell=int(section["min_trades_per_cell"]),
        plateau_margin_percent=float(section["plateau_margin_percent"]),
        plateau_share=float(section["plateau_share"]),
        min_neighbours_evaluated=int(
            section.get("min_neighbours_evaluated", DEFAULT_MIN_NEIGHBOURS_EVALUATED)
        ),
        source=str(path),
        fingerprint=f"{version}+{digest}",
    )


def build_grid(tp_values: Sequence[float], sl_values: Sequence[float]) -> list[dict]:
    """Cartesian product of TP and SL percentages, TP-major."""
    return [
        {"tp_percent": tp, "sl_percent": sl}
        for tp in tp_values
        for sl in sl_values
    ]


@dataclass(frozen=True)
class CellStats:
    """One grid cell scored on closed trades."""

    tp: float
    sl: float
    trades: int
    pnl_net: float
    expectancy_bps: float
    win_rate: float

    def is_eligible(self, min_trades: int) -> bool:
        return self.trades >= min_trades


def score_cell(tp: float, sl: float, trades: Iterable[Trade]) -> CellStats:
    """Aggregate closed trades of one cell.

    `expectancy_bps` is the mean per-trade net PnL divided by the position size
    (which is expressed in rubles, ADR-012) and scaled to basis points. Averaging
    per trade keeps a large position from dominating the cell, and matches the
    per-trade bps figures in the null test and the cost audit.
    """
    closed = [trade for trade in trades if not trade.is_open()]
    values = [
        (trade.pnl_net or 0.0) / trade.quantity * 10_000.0
        for trade in closed
        if trade.quantity > 0
    ]
    winners = sum(1 for trade in closed if (trade.pnl_net or 0.0) > 0)
    return CellStats(
        tp=tp,
        sl=sl,
        trades=len(closed),
        pnl_net=sum(trade.pnl_net or 0.0 for trade in closed),
        expectancy_bps=sum(values) / len(values) if values else 0.0,
        win_rate=winners / len(closed) * 100.0 if closed else 0.0,
    )


def score_cells(trades_by_cell: Mapping[GridPoint, Iterable[Trade]]) -> list[CellStats]:
    """Score every cell, sorted by TP then SL for a stable report."""
    cells = [score_cell(tp, sl, trades) for (tp, sl), trades in trades_by_cell.items()]
    return sorted(cells, key=lambda cell: (cell.tp, cell.sl))


@dataclass(frozen=True)
class PlateauVerdict:
    """Outcome of the neighbourhood check around the best eligible cell."""

    status: str
    best: CellStats | None
    neighbours: tuple[CellStats, ...] = ()
    evaluated_neighbours: int = 0
    passing_neighbours: int = 0
    share: float | None = None
    threshold_bps: float | None = None
    best_on_edge: bool = False
    reason: str = ""

    @property
    def is_plateau(self) -> bool:
        return self.status == "plateau"

    def describe(self) -> dict:
        return {
            "status": self.status,
            "best": (
                {
                    "tp_percent": self.best.tp,
                    "sl_percent": self.best.sl,
                    "trades": self.best.trades,
                    "expectancy_bps": round(self.best.expectancy_bps, 2),
                }
                if self.best is not None
                else None
            ),
            "evaluated_neighbours": self.evaluated_neighbours,
            "passing_neighbours": self.passing_neighbours,
            "share": round(self.share, 3) if self.share is not None else None,
            "threshold_bps": (
                round(self.threshold_bps, 2) if self.threshold_bps is not None else None
            ),
            "best_on_edge": self.best_on_edge,
            "reason": self.reason,
        }


def analyze_plateau(
    cells: Sequence[CellStats],
    tp_values: Sequence[float],
    sl_values: Sequence[float],
    thresholds: HeatmapThresholds,
) -> PlateauVerdict:
    """Pick the best eligible cell and check whether it sits on a plateau.

    A cell is a neighbour of the best one when it is ±1 step away on each axis
    and exists in the grid (edge cells have fewer neighbours). Neighbours below
    `min_trades_per_cell` are excluded from the share: thin cells cannot support
    or refute a plateau. Too few evaluated neighbours make the check
    inconclusive instead of a false "plateau" on a single surviving neighbour.
    """
    eligible = [cell for cell in cells if cell.is_eligible(thresholds.min_trades_per_cell)]
    if not eligible:
        return PlateauVerdict(
            status="no_eligible_cells",
            best=None,
            reason=f"no cell reached {thresholds.min_trades_per_cell} trades",
        )

    # `cells` is sorted by (TP, SL), so ties go to the first cell in grid order.
    best = max(eligible, key=lambda cell: cell.expectancy_bps)
    best_on_edge = _is_edge(best, tp_values, sl_values)
    if best.expectancy_bps <= 0.0:
        return PlateauVerdict(
            status="no_positive_cell",
            best=best,
            best_on_edge=best_on_edge,
            reason="best eligible cell is not positive; a margin around it means nothing",
        )

    by_key = {(cell.tp, cell.sl): cell for cell in cells}
    neighbour_cells = [
        by_key[key] for key in _neighbour_keys(best, tp_values, sl_values) if key in by_key
    ]
    evaluated = [
        cell for cell in neighbour_cells if cell.is_eligible(thresholds.min_trades_per_cell)
    ]
    if len(evaluated) < thresholds.min_neighbours_evaluated:
        return PlateauVerdict(
            status="insufficient_neighbours",
            best=best,
            neighbours=tuple(neighbour_cells),
            evaluated_neighbours=len(evaluated),
            best_on_edge=best_on_edge,
            reason=(
                f"only {len(evaluated)} neighbours reached "
                f"{thresholds.min_trades_per_cell} trades "
                f"(need {thresholds.min_neighbours_evaluated})"
            ),
        )

    threshold_bps = best.expectancy_bps * (1.0 - thresholds.plateau_margin_percent / 100.0)
    passing = [
        cell
        for cell in evaluated
        if cell.expectancy_bps > 0.0 and cell.expectancy_bps >= threshold_bps
    ]
    share = len(passing) / len(evaluated)
    return PlateauVerdict(
        status="plateau" if share >= thresholds.plateau_share else "no_plateau",
        best=best,
        neighbours=tuple(neighbour_cells),
        evaluated_neighbours=len(evaluated),
        passing_neighbours=len(passing),
        share=share,
        threshold_bps=threshold_bps,
        best_on_edge=best_on_edge,
        reason=(
            f"{len(passing)}/{len(evaluated)} neighbours >= {threshold_bps:.2f} bps "
            f"(share {share:.0%}, need {thresholds.plateau_share:.0%})"
        ),
    )


def _is_edge(
    cell: CellStats, tp_values: Sequence[float], sl_values: Sequence[float]
) -> bool:
    """True when the cell sits on the grid border: the optimum may be outside."""
    tp_values = list(tp_values)
    sl_values = list(sl_values)
    if cell.tp not in tp_values or cell.sl not in sl_values:
        return False
    return (
        tp_values.index(cell.tp) in (0, len(tp_values) - 1)
        or sl_values.index(cell.sl) in (0, len(sl_values) - 1)
    )


def _neighbour_keys(
    cell: CellStats, tp_values: Sequence[float], sl_values: Sequence[float]
) -> list[GridPoint]:
    """Keys of the 8 surrounding cells that exist in the grid."""
    tp_values = list(tp_values)
    sl_values = list(sl_values)
    try:
        tp_index = tp_values.index(cell.tp)
        sl_index = sl_values.index(cell.sl)
    except ValueError:
        return []
    keys: list[GridPoint] = []
    for tp_step in (-1, 0, 1):
        for sl_step in (-1, 0, 1):
            if tp_step == 0 and sl_step == 0:
                continue
            tp_pos = tp_index + tp_step
            sl_pos = sl_index + sl_step
            if 0 <= tp_pos < len(tp_values) and 0 <= sl_pos < len(sl_values):
                keys.append((tp_values[tp_pos], sl_values[sl_pos]))
    return keys


def collect_train_trades(
    candles: Sequence[Candle],
    windows: Iterable[WalkForwardWindow],
    *,
    runner: StrategyRunner,
    grid: Sequence[dict],
    warmup_bars: int = 0,
    dedupe: bool = True,
) -> dict[GridPoint, list[Trade]]:
    """Score the grid on walk-forward train periods only.

    `warmup_bars` candles are prepended to every train window so indicators are
    warm at `train_start`; trades opened during warmup belong to earlier data and
    are dropped, exactly as in `run_walk_forward()`.

    Train windows overlap by construction (train is longer than the step), so the
    same trade is produced by several windows. With `dedupe=True` a trade is
    counted once per cell, keyed by entry time, index and direction: the trade
    count stays honest instead of being inflated by the window layout.
    """
    if warmup_bars < 0:
        raise ValueError("warmup_bars must be non-negative")
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    by_cell: dict[GridPoint, list[Trade]] = {}
    seen: set[tuple] = set()

    for window in windows:
        train_candles = _candles_with_warmup(
            ordered, window.train_start, window.train_end, warmup_bars
        )
        if not train_candles:
            continue
        for config in grid:
            key = _grid_key(config)
            bucket = by_cell.setdefault(key, [])
            for trade in runner(config, train_candles):
                if trade.is_open() or trade.entry_time < window.train_start:
                    continue
                if dedupe:
                    identity = (key, trade.entry_time, trade.entry_index, trade.signal_type)
                    if identity in seen:
                        continue
                    seen.add(identity)
                bucket.append(trade)

    return by_cell


def collect_full_history_trades(
    candles: Sequence[Candle],
    *,
    runner: StrategyRunner,
    grid: Sequence[dict],
) -> dict[GridPoint, list[Trade]]:
    """Score the grid on the whole history. Diagnostics only, never for a choice."""
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    return {
        _grid_key(config): [
            trade for trade in runner(config, ordered) if not trade.is_open()
        ]
        for config in grid
    }


def _grid_key(config: dict) -> GridPoint:
    return (float(config["tp_percent"]), float(config["sl_percent"]))


def _candles_between(
    candles: Sequence[Candle], start: datetime, end: datetime
) -> list[Candle]:
    """Candles with `start <= open_time < end`; input must be sorted."""
    return [candle for candle in candles if start <= candle.open_time < end]


def _candles_with_warmup(
    candles: Sequence[Candle], start: datetime, end: datetime, warmup_bars: int
) -> list[Candle]:
    """Candles in [start, end) plus up to `warmup_bars` candles right before it."""
    indices = [i for i, candle in enumerate(candles) if start <= candle.open_time < end]
    if not indices:
        return []
    return list(candles[max(0, indices[0] - warmup_bars) : indices[-1] + 1])


def render_markdown(
    *,
    title: str,
    cells: Sequence[CellStats],
    tp_values: Sequence[float],
    sl_values: Sequence[float],
    thresholds: HeatmapThresholds,
    verdict: PlateauVerdict,
    meta: Mapping[str, object],
) -> str:
    """Markdown report: parameters, thresholds, matrix, plateau verdict."""
    lines: list[str] = [f"# {title}", ""]

    for label, value in meta.items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")
    lines.append("## Thresholds")
    lines.append("")
    for label, value in thresholds.describe().items():
        lines.append(f"- **{label}**: {value}")
    lines.append("")
    lines.append(
        f"Cells with fewer than {thresholds.min_trades_per_cell} trades are marked "
        "with `*` and excluded from the plateau check."
    )
    lines.append("")
    lines.append(
        f"Plateau: best cell > 0 and at least {thresholds.plateau_share:.0%} of the "
        f"neighbours (±1 step) within {thresholds.plateau_margin_percent:.0f}% of it."
    )
    lines.append("")

    lines.append("## Matrix (net expectancy, bps / trades)")
    lines.append("")
    lines.append("| TP \\ SL | " + " | ".join(f"{sl:g}%" for sl in sl_values) + " |")
    lines.append("|" + "---|" * (len(sl_values) + 1))
    by_key = {(cell.tp, cell.sl): cell for cell in cells}
    for tp in tp_values:
        row = [f"**{tp:g}%**"]
        for sl in sl_values:
            row.append(_format_cell(by_key.get((tp, sl)), thresholds, verdict))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Plateau verdict")
    lines.append("")
    lines.append(f"- **status**: `{verdict.status}`")
    if verdict.best is not None:
        lines.append(
            f"- **best cell**: TP {verdict.best.tp:g}% / SL {verdict.best.sl:g}% — "
            f"{verdict.best.expectancy_bps:+.2f} bps, {verdict.best.trades} trades, "
            f"net {verdict.best.pnl_net:+.2f} RUB"
        )
    if verdict.threshold_bps is not None:
        lines.append(f"- **neighbour threshold**: {verdict.threshold_bps:+.2f} bps")
    if verdict.best_on_edge:
        lines.append(
            "- **warning**: the best cell lies on the grid edge — the optimum may be "
            "outside the grid; widen the grid before trusting this cell"
        )
    lines.append(
        f"- **neighbours**: {verdict.passing_neighbours}/"
        f"{verdict.evaluated_neighbours} pass"
        + (f" (share {verdict.share:.0%})" if verdict.share is not None else "")
    )
    lines.append(f"- **reason**: {verdict.reason}")
    lines.append("")
    lines.append(
        "> A plateau does not prove an edge: it only shows the result does not rest "
        "on one parameter point. The decision belongs to the T2-21 report "
        "(net, CI, OOS, null test)."
    )
    lines.append("")

    if verdict.neighbours:
        lines.append("### Neighbours of the best cell")
        lines.append("")
        lines.append("| TP % | SL % | trades | expectancy bps | passes |")
        lines.append("|---|---|---|---|---|")
        for cell in verdict.neighbours:
            eligible = cell.is_eligible(thresholds.min_trades_per_cell)
            passes = (
                eligible
                and verdict.threshold_bps is not None
                and cell.expectancy_bps > 0.0
                and cell.expectancy_bps >= verdict.threshold_bps
            )
            lines.append(
                f"| {cell.tp:g} | {cell.sl:g} | {cell.trades} | "
                f"{cell.expectancy_bps:+.2f} | {'yes' if passes else 'no'} |"
            )
        lines.append("")

    return "\n".join(lines)


def _format_cell(
    cell: CellStats | None, thresholds: HeatmapThresholds, verdict: PlateauVerdict
) -> str:
    if cell is None:
        return "—"
    marker = "" if cell.is_eligible(thresholds.min_trades_per_cell) else "*"
    best_marker = " **(best)**" if verdict.best is not None and cell == verdict.best else ""
    return f"{cell.expectancy_bps:+.2f} / {cell.trades}{marker}{best_marker}"


def write_cells_csv(
    path: str | Path,
    *,
    cells: Sequence[CellStats],
    thresholds: HeatmapThresholds,
    verdict: PlateauVerdict,
) -> Path:
    """Write one row per cell with the flags used in the decision."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    neighbour_keys = {(cell.tp, cell.sl) for cell in verdict.neighbours}
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "tp_percent",
                "sl_percent",
                "trades",
                "pnl_net_rub",
                "expectancy_bps",
                "win_rate_percent",
                "eligible",
                "is_best",
                "is_neighbour",
                "passes_threshold",
                "min_trades_per_cell",
                "plateau_margin_percent",
                "plateau_share",
                "min_neighbours_evaluated",
                "thresholds_version",
            ]
        )
        for cell in cells:
            eligible = cell.is_eligible(thresholds.min_trades_per_cell)
            passes = (
                eligible
                and verdict.threshold_bps is not None
                and cell.expectancy_bps > 0.0
                and cell.expectancy_bps >= verdict.threshold_bps
            )
            writer.writerow(
                [
                    f"{cell.tp:g}",
                    f"{cell.sl:g}",
                    cell.trades,
                    f"{cell.pnl_net:.2f}",
                    f"{cell.expectancy_bps:.4f}",
                    f"{cell.win_rate:.2f}",
                    int(eligible),
                    int(verdict.best is not None and cell == verdict.best),
                    int((cell.tp, cell.sl) in neighbour_keys),
                    int(passes),
                    thresholds.min_trades_per_cell,
                    thresholds.plateau_margin_percent,
                    thresholds.plateau_share,
                    thresholds.min_neighbours_evaluated,
                    thresholds.fingerprint,
                ]
            )
    return out
