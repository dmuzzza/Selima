"""`evaluate <strategy>`: one command from an idea to a verdict (T2-25).

Reuses the verdict pipeline (`scripts/verdict.py`) and adds what the design
requires (docs/EVALUATE_DESIGN.md, ADR-032):

- the number of attempts comes from `journal/hypotheses.jsonl`, not from a flag;
- MDE with an explicit power and the `underpowered` outcome;
- the sealed period is cut by the loader: candles at or after `sealed_start` are
  never read unless the period is opened on purpose;
- `--dry-run` runs everything and prints the report without touching the journal
  or the budget, so a smoke run cannot become the first recorded idea.

    uv run python scripts/evaluate.py --strategy dual_ma_crossover_after_testing \\
        --hypothesis "..." --tag my-idea-1
    uv run python scripts/evaluate.py --strategy dual_ma_crossover_after_testing \\
        --hypothesis "..." --limit 3 --dry-run

The command never writes to git: it reports uncommitted journal rows and the
user commits the journal together with the report.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from config.commissions import describe_commissions  # noqa: E402
from scripts.verdict import (  # noqa: E402
    COSTS_PATH,
    DEFAULT_OUT_DIR,
    INSTRUMENTS_PATH,
    PORTFOLIO_PATH,
    THRESHOLDS_PATH,
    doubled_costs,
    grid_around,
    interval,
    load_instruments,
    pool_heatmap,
    run_pool,
    slice_window,
    tp_penalty_costs,
)
from src.backtest.costs import load_costs  # noqa: E402
from src.backtest.dividends import (  # noqa: E402
    applied_ex_bars,
    build_coverage,
    load_dividend_thresholds,
    load_dividends,
    ordered_candles,
    resolve_events,
    years_from_candles,
)
from src.backtest.evaluate import (  # noqa: E402
    EvaluateReport,
    IccReport,
    IccScenario,
    PowerReport,
    build_power_row,
    decide_outcome,
    icc_ceiling,
    icc_scenarios,
    load_evaluate_thresholds,
    mde_from_sigma,
    mde_with_trials,
    minimum_detectable_effect,
    project_power,
    render_power_report,
    render_report,
    sealed_rows,
    sigma_bps,
    target_for_trades,
    trades_needed,
)
from src.backtest.event_study import (  # noqa: E402
    CLUSTER_BY_MONTH,
    CLUSTER_BY_QUARTER,
    CLUSTER_BY_YEAR,
    ICC_LEVELS,
    ClusterLevel,
    basket_totals,
    choose_cluster_level,
    cluster_count,
    cluster_groups,
    cluster_means,
    icc_bootstrap_ci,
    icc_oneway,
    lag1_autocorrelation,
    load_power_universe,
    median_sigma_bps,
    placebo_windows,
    scan_event_windows,
    unconditional_excess_sigma,
    unconditional_sigma,
    window_excess_bps,
    window_return_bps,
)
from src.backtest.heatmap import (  # noqa: E402
    load_thresholds as load_heatmap_thresholds,
)
from src.backtest.hypothesis_log import (  # noqa: E402
    DEFAULT_JOURNAL_PATH,
    TrialBudgetExceeded,
    budget_state,
    config_sha256,
    load_hypotheses,
    record_attempt,
    record_legacy_baseline,
    trial_count,
)
from src.backtest.null_test import null_test, synthetic_candles  # noqa: E402
from src.backtest.portfolio import (  # noqa: E402
    WITHOUT_BEST_TICKER,
    block_bootstrap,
    gap_series,
    leave_one_out_variants,
    load_multi_instrument_thresholds,
    load_portfolio,
    summarize,
)
from src.backtest.verdict import (  # noqa: E402
    IntervalResult,
    VerdictInputs,
    evaluate_verdict,
    load_verdict_thresholds,
)
from src.market_data.history_manager import HistoryManager  # noqa: E402
from src.market_data.timeframe import Timeframe  # noqa: E402

logger = logging.getLogger("evaluate")

# Lower bound of the attempts made before the journal existed, read from the ADRs:
# walk-forward grid 4 (ADR-015), heatmap 42 cells (ADR-021), portfolio rule 1
# (ADR-022), heatmap around the traded cell 25 (ADR-023), entry filter 2
# (ADR-028), ex-dividend bar rule 3 (ADR-030), weekend criterion 2 (ADR-031).
LEGACY_VARIANTS = 79
LEGACY_NOTES = (
    "lower bound of pre-journal variants, not a count of ideas: "
    "ADR-015 walk-forward grid TP{1,3} x SL{1,2} = 4; "
    "ADR-021 heatmap TP 1-4 x SL 0.5-4 = 42 cells; "
    "ADR-022 one portfolio rule on 20 tickers = 1; "
    "ADR-023 heatmap around the traded cell 5x5 = 25; "
    "ADR-028 entry-filter variants = 2; "
    "ADR-030 ex-dividend bar rule = 3; "
    "ADR-031 weekend criterion = 2. Timeframes tried: 4h and 1d "
    "(SBER 1d gave no eligible cells, ADR-021). "
    "A variant that is not listed in an ADR is not counted."
)


# ── journal git state ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class JournalGitState:
    """Whether the journal has rows that are not committed yet."""

    available: bool
    uncommitted_rows: int
    detail: str = ""

    def warning(self) -> str | None:
        if not self.available:
            return "git unavailable, check the journal manually"
        if self.uncommitted_rows == 0:
            return None
        return (
            f"journal: {self.uncommitted_rows} uncommitted row(s) — commit it, "
            "an uncommitted journal loses the trial counter on checkout"
        )


def parse_numstat_added(text: str) -> int:
    """Added lines from `git diff --numstat`; binary rows (`-`) are skipped."""
    total = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            total += int(parts[0])
    return total


def _git(args: list[str], root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git %s failed: %s", " ".join(args), exc)
        return None
    if completed.returncode != 0:
        logger.warning("git %s failed: %s", " ".join(args), completed.stderr.strip())
        return None
    return completed.stdout


def journal_git_state(journal_path: Path, *, root: Path | None = None) -> JournalGitState:
    """Count journal rows that are not committed.

    `git status --porcelain journal/` shows one line per file, not per row, so the
    count is read from `--numstat` (tracked file) or from the file itself
    (untracked). Git unavailable is not an error: the report says so instead.
    """
    repository = (root or journal_path.resolve().parent.parent).resolve()
    try:
        relative = journal_path.resolve().relative_to(repository).as_posix()
    except ValueError:
        return JournalGitState(False, 0, "journal is outside the repository")

    status = _git(["status", "--porcelain", "--", relative], repository)
    if status is None:
        return JournalGitState(False, 0, "git unavailable")
    if not status.strip():
        return JournalGitState(True, 0)

    if any(line.startswith("??") for line in status.splitlines()):
        try:
            rows = sum(
                1
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError as exc:
            logger.warning("cannot read journal %s: %s", journal_path, exc)
            return JournalGitState(True, 1, "untracked journal, rows unknown")
        return JournalGitState(True, rows)

    added = 0
    for args in (
        ["diff", "--numstat", "--", relative],
        ["diff", "--cached", "--numstat", "--", relative],
    ):
        output = _git(args, repository)
        if output:
            added += parse_numstat_added(output)
    return JournalGitState(True, added)


# ── pipeline ───────────────────────────────────────────────────────────────


def interval_at(pooled, thresholds, alpha: float) -> IntervalResult:
    """Pooled expectancy with an interval at the requested level."""
    if not pooled:
        return IntervalResult(trades=0, net_bps=0.0, ci_low=0.0, ci_high=0.0)
    bootstrap = block_bootstrap(
        pooled,
        block_days=thresholds.block_days,
        iterations=thresholds.bootstrap_iterations,
        alpha=alpha,
        seed=thresholds.seed,
    )
    return IntervalResult(
        trades=bootstrap.trades,
        net_bps=bootstrap.net_bps,
        ci_low=bootstrap.ci_low,
        ci_high=bootstrap.ci_high,
    )


def parse_tickers(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    tickers = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not tickers:
        raise SystemExit("--tickers is empty")
    if len(set(tickers)) != len(tickers):
        raise SystemExit("--tickers contains duplicates")
    return tickers


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idea -> verdict with power, multiplicity and a sealed period (T2-25)"
    )
    parser.add_argument("--strategy", required=False, help="strategy code from the registry")
    parser.add_argument(
        "--hypothesis", required=False, help="the idea, stated before the run"
    )
    parser.add_argument("--timeframe", default=None, help="default: config/portfolio.json")
    parser.add_argument("--universe", default=str(PORTFOLIO_PATH), help="portfolio file")
    parser.add_argument("--tickers", default=None, help="manual set, counts as a new idea")
    parser.add_argument("--tag", default=None, help="report name")
    parser.add_argument(
        "--power-only",
        action="store_true",
        help=(
            "pre-check an event idea: count eligible dividend events and print "
            "sigma and MDE; runs no strategy and writes no journal row"
        ),
    )
    parser.add_argument(
        "--window-sessions",
        type=int,
        default=10,
        help="traded window for --power-only, in sessions (default 10)",
    )
    parser.add_argument(
        "--windows",
        default="5,15",
        help="extra window lengths for robustness in --power-only (default 5,15)",
    )
    parser.add_argument(
        "--excess",
        action="store_true",
        help=(
            "in --power-only, measure sigma on the window return net of the "
            "equal-weight basket of the other tickers instead of the raw return"
        ),
    )
    parser.add_argument(
        "--cluster-by",
        default=CLUSTER_BY_QUARTER,
        choices=[CLUSTER_BY_QUARTER, CLUSTER_BY_MONTH, CLUSTER_BY_YEAR],
        help="season bucket for the clustered MDE in --power-only (default quarter)",
    )
    parser.add_argument(
        "--placebo-icc",
        action="store_true",
        help=(
            "in --power-only, measure the intra-cluster correlation on placebo "
            "windows (same rules, dates shifted off the real ones) and project "
            "the MDE on a wider universe; no result of the idea is used"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed for the placebo shifts and the ICC bootstrap (default from config)",
    )
    parser.add_argument(
        "--icc-bootstraps",
        type=int,
        default=None,
        help="cluster bootstrap resamples for the ICC interval (default from config)",
    )
    parser.add_argument(
        "--ticker-cap",
        type=int,
        default=None,
        help=(
            "upper bound of the projection: issuers whose daily candles can be "
            "obtained; default from config, else measured from the database"
        ),
    )
    parser.add_argument(
        "--universe-file",
        default=None,
        help=(
            "frozen ticker list with its sha256 (config/power_universe.json); an "
            "edited list that was not re-hashed is refused"
        ),
    )
    parser.add_argument(
        "--record-legacy",
        action="store_true",
        help="write the pre-journal attempt count as one legacy line and exit",
    )
    parser.add_argument(
        "--grid-cells",
        type=int,
        default=None,
        help=(
            "variants looked at inside the idea; default 1 for a verdict run and "
            "the number of windows for --power-only"
        ),
    )
    parser.add_argument("--limit", type=int, help="first N tickers, for a smoke run")
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL_PATH))
    parser.add_argument("--thresholds-file", default=str(THRESHOLDS_PATH))
    parser.add_argument("--costs-file", default=str(COSTS_PATH))
    parser.add_argument("--dividends-file", default=str(ROOT / "config" / "dividends.json"))
    parser.add_argument("--warmup-bars", type=int, default=250)
    parser.add_argument("--grid-step", type=float, default=0.5)
    parser.add_argument("--grid-steps", type=int, default=2)
    parser.add_argument("--null-seeds", type=int, default=30)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def parse_windows(value: str) -> tuple[int, ...]:
    """Window lengths from `--windows 5,15`."""
    windows: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        window = int(part)
        if window < 1:
            raise SystemExit("--windows must list positive session counts")
        windows.append(window)
    if not windows:
        raise SystemExit("--windows is empty")
    return tuple(windows)


DAILY_HISTORY_CUTOFF = date(2020, 12, 31)


def available_daily_tickers(history, timeframe: str, cutoff: date) -> int | None:
    """Issuers whose daily series starts on or before `cutoff`.

    The projection must not promise a universe larger than the one whose daily
    candles can be obtained: an issuer listed later has no 2020 bars, and an event
    idea needs the history to place its events. The count is a bound, not an
    estimate of dividend payers.
    """
    from sqlalchemy import text  # local: the script runs without the ORM layer

    query = text(
        "SELECT count(*) FROM ("
        "SELECT figi FROM market_data WHERE timeframe = :tf "
        "GROUP BY figi HAVING min(open_time) <= :cutoff"
        ") AS usable"
    )
    try:
        with history.engine.connect() as connection:
            value = connection.execute(
                query, {"tf": timeframe, "cutoff": cutoff}
            ).scalar_one()
    except Exception as error:  # the cap is optional and the report says so
        logging.warning("ticker cap not measured: %s", error)
        return None
    return int(value)


def build_icc_report(
    *,
    args: argparse.Namespace,
    raw,
    windows,
    sigma: float | None,
    events: int,
    tickers,
    idea_target: float | None,
    evaluate_thresholds,
    n_trials: int,
    history,
    timeframe_name: str,
) -> IccReport:
    """Placebo ICC, the gate level and the projection (ADR-033).

    The placebo windows carry the same entry rule on shifted dates, so nothing
    here depends on the hypothesis result: the check answers "how dependent is a
    sample of this design", which is the same question on any event idea.
    """
    power_icc = evaluate_thresholds.power_icc
    seed = args.seed if args.seed is not None else power_icc.seed
    bootstraps = (
        args.icc_bootstraps if args.icc_bootstraps is not None else power_icc.bootstraps
    )

    placebo = placebo_windows(
        raw,
        windows,
        window_sessions=args.window_sessions,
        sealed_start=evaluate_thresholds.sealed_start,
        min_shift=power_icc.min_shift_sessions,
        max_shift=power_icc.max_shift_sessions,
        seed=seed,
    )

    totals = basket_totals(raw)
    ordered = {ticker.upper(): ordered_candles(candles) for ticker, candles in raw.items()}
    placed = []
    outcomes: list[float] = []
    for window in placebo.windows:
        series = ordered.get(window.ticker)
        if series is None:
            continue
        value = (
            window_excess_bps(series, window, totals)
            if args.excess
            else window_return_bps(series, window)
        )
        if value is None:
            continue
        placed.append(window)
        outcomes.append(value)

    levels = []
    for by in ICC_LEVELS:
        groups = cluster_groups(placed, outcomes, by)
        estimate = icc_oneway(groups)
        low, high = icc_bootstrap_ci(groups, samples=bootstraps, seed=seed)
        estimate = replace(estimate, ci_low=low, ci_high=high, bootstraps=bootstraps)
        levels.append(
            ClusterLevel(
                by=by, icc=estimate, lag1=lag1_autocorrelation(cluster_means(groups))
            )
        )

    decision = choose_cluster_level(levels, merge_lag1=power_icc.merge_lag1)
    chosen = next(level for level in levels if level.by == decision.level)
    current_mde = mde_from_sigma(
        sigma,
        events / chosen.icc.design_effect_value,
        alpha=evaluate_thresholds.confirm_alpha,
        power=evaluate_thresholds.power,
        n_trials=n_trials,
    )

    available_now = available_daily_tickers(history, timeframe_name, DAILY_HISTORY_CUTOFF)
    if args.ticker_cap is not None:
        cap, cap_source = args.ticker_cap, "set by --ticker-cap"
    elif power_icc.ticker_cap is not None:
        cap, cap_source = power_icc.ticker_cap, "set in config/validation.json"
    else:
        cap = available_now
        cap_source = (
            f"measured: daily series starting by {DAILY_HISTORY_CUTOFF.isoformat()}"
        )

    projection = project_power(
        sigma_bps=sigma,
        events_per_ticker=events / len(tickers) if tickers else None,
        clusters=chosen.clusters,
        icc=chosen.icc.icc,
        ticker_counts=power_icc.ticker_counts,
        cap=cap,
        target_idea_bps=idea_target,
        alpha=evaluate_thresholds.confirm_alpha,
        power=evaluate_thresholds.power,
        n_trials=n_trials,
    )

    # The gate answers "load tickers or close the idea", so it uses the widest
    # universe the cap allows, not the current sample: a design that only works
    # at 20 tickers and stops improving is not worth the loading time.
    if projection:
        gate_mde = projection[-1].mde_bps
        gate_tickers = projection[-1].tickers
    else:
        gate_mde = current_mde
        gate_tickers = len(tickers)
    gate_measurable = (
        None if gate_mde is None or idea_target is None else gate_mde <= idea_target
    )

    events_per_ticker = events / len(tickers) if tickers else None
    scenarios: list[IccScenario] = []
    ceilings: dict[str, float | None] = {}
    for level in levels:
        scenarios.extend(
            icc_scenarios(
                level=level,
                tickers=gate_tickers or len(tickers),
                events_per_ticker=events_per_ticker,
                sigma_bps=sigma,
                target_idea_bps=idea_target,
                alpha=evaluate_thresholds.confirm_alpha,
                power=evaluate_thresholds.power,
                n_trials=n_trials,
            )
        )
        projected_events = (
            None if events_per_ticker is None else events_per_ticker * (gate_tickers or 0)
        )
        ceilings[level.by] = icc_ceiling(
            sigma_bps=sigma,
            events=projected_events,
            clusters=level.clusters,
            target_idea_bps=idea_target,
            alpha=evaluate_thresholds.confirm_alpha,
            power=evaluate_thresholds.power,
            n_trials=n_trials,
        )

    return IccReport(
        seed=seed,
        min_shift=power_icc.min_shift_sessions,
        max_shift=power_icc.max_shift_sessions,
        beyond_max_shift=placebo.beyond_max_shift,
        placebo_events=len(placed),
        placebo_skipped=placebo.skipped,
        levels=tuple(levels),
        decision=decision,
        gate_mde_bps=gate_mde,
        gate_measurable=gate_measurable,
        target_idea_bps=idea_target,
        gate_tickers=gate_tickers,
        current_mde_bps=current_mde,
        current_tickers=len(tickers),
        projection=projection,
        scenarios=tuple(scenarios),
        ceilings=ceilings,
        ticker_cap=cap,
        ticker_cap_source=cap_source,
        available_now=available_now,
    )


def run_power_only(args: argparse.Namespace) -> int:
    """Pre-check an event idea without a strategy, a PnL or a journal row.

    Reads the dividend calendar and the candles, counts eligible event windows,
    measures the unconditional dispersion of the outcome variable and prints the
    MDE. A design whose MDE exceeds the target is changed before a slot is spent.
    """
    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    if args.window_sessions < 1:
        raise SystemExit("--window-sessions must be >= 1")

    portfolio = load_portfolio(args.universe)
    timeframe_name = args.timeframe or "1d"
    evaluate_thresholds = load_evaluate_thresholds(args.thresholds_file)
    dividend_thresholds = load_dividend_thresholds(args.thresholds_file)
    instruments = load_instruments(INSTRUMENTS_PATH)

    manual = parse_tickers(args.tickers)
    universe_sha: str | None = None
    if manual:
        tickers = list(manual)
    elif args.universe_file:
        frozen = load_power_universe(args.universe_file)
        tickers = list(frozen.tickers)
        universe_sha = frozen.sha256
    else:
        tickers = list(portfolio.tickers)
    if args.limit:
        tickers = tickers[: args.limit]
    unknown = [ticker for ticker in tickers if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")

    windows = tuple(sorted({args.window_sessions, *parse_windows(args.windows)}))
    journal_path = Path(args.journal)
    entries = load_hypotheses(journal_path)
    grid_cells = args.grid_cells if args.grid_cells is not None else len(windows)
    n_trials = trial_count(entries) + grid_cells

    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    timeframe = Timeframe(timeframe_name)
    raw = {
        ticker: history.get_candles(instruments[ticker]["figi"], timeframe)
        for ticker in tickers
    }
    empty = [ticker for ticker, candles in raw.items() if len(candles) < 2]
    if empty:
        raise SystemExit(f"No candles for: {', '.join(empty)}")

    # The same technical guarantee as the verdict path: the sealed period is cut
    # before anything is counted.
    raw = sealed_rows(raw, evaluate_thresholds.sealed_start)

    calendar = load_dividends(args.dividends_file)
    wanted = set(tickers)
    events = [event for event in calendar.events if event.ticker in wanted]

    rows = []
    scans = {}
    sigma_of = unconditional_excess_sigma if args.excess else unconditional_sigma
    target_model = evaluate_thresholds.target_model

    # Trades a year are needed for the idea target, so the main window is scanned
    # first and the other windows are only robustness.
    main_scan = scan_event_windows(
        raw,
        events,
        window_sessions=args.window_sessions,
        sealed_start=evaluate_thresholds.sealed_start,
        t1_settlement_switch=dividend_thresholds.t1_settlement_switch,
    )
    main_sigma = sigma_of(raw, args.window_sessions)

    span_years = events_per_year = None
    if main_scan.windows:
        first = min(window.entry_time for window in main_scan.windows)
        last = max(window.exit_time for window in main_scan.windows)
        span_years = max((last - first).days / 365.25, 1e-9)
        events_per_year = main_scan.events / span_years

    idea_target = target_for_trades(
        sigma_bps=main_sigma.sigma_bps,
        trades_per_year=events_per_year,
        sharpe_target=target_model.sharpe_target,
        live_retention=target_model.live_retention,
        cost_floor_bps=target_model.cost_floor_bps,
    )

    icc_report: IccReport | None = None
    if args.placebo_icc:
        icc_report = build_icc_report(
            args=args,
            raw=raw,
            windows=main_scan.windows,
            sigma=main_sigma.sigma_bps,
            events=main_scan.events,
            tickers=tickers,
            idea_target=idea_target,
            evaluate_thresholds=evaluate_thresholds,
            n_trials=n_trials,
            history=history,
            timeframe_name=timeframe_name,
        )

    for window in windows:
        if window == args.window_sessions:
            scan, sigma = main_scan, main_sigma
        else:
            scan = scan_event_windows(
                raw,
                events,
                window_sessions=window,
                sealed_start=evaluate_thresholds.sealed_start,
                t1_settlement_switch=dividend_thresholds.t1_settlement_switch,
            )
            sigma = sigma_of(raw, window)
        clusters = cluster_count(scan.windows, args.cluster_by)
        rows.append(
            build_power_row(
                window_sessions=window,
                scan=scan,
                sigma=sigma,
                clusters=clusters,
                target_edge_bps=evaluate_thresholds.target_edge_bps,
                target_idea_bps=idea_target,
                trades_per_year=events_per_year,
                alpha=evaluate_thresholds.confirm_alpha,
                power=evaluate_thresholds.power,
                n_trials=n_trials,
            )
        )
        scans[window] = (scan, sigma, clusters)

    tag = args.tag or f"power-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    report = PowerReport(
        label=args.hypothesis or "power pre-check",
        timeframe=timeframe_name,
        tickers=tuple(tickers),
        total_events=main_scan.total_events,
        events=main_scan.events,
        per_ticker=main_scan.per_ticker(),
        sigma_bps=main_sigma.sigma_bps,
        median_sigma_bps=median_sigma_bps(main_sigma),
        sigma_windows=main_sigma.windows,
        target_edge_bps=evaluate_thresholds.target_edge_bps,
        power=evaluate_thresholds.power,
        alpha=evaluate_thresholds.confirm_alpha,
        n_trials=n_trials,
        grid_cells=grid_cells,
        main_window=args.window_sessions,
        sealed_start=evaluate_thresholds.sealed_start,
        sigma_mode="excess" if args.excess else "raw",
        cluster_by=args.cluster_by,
        rows=tuple(rows),
        out_of_history=main_scan.out_of_history,
        future=main_scan.future,
        sealed=main_scan.sealed,
        declared_after_entry=main_scan.declared_after_entry,
        missing_declared_date=main_scan.missing_declared_date,
        journal_warning=journal_git_state(journal_path, root=ROOT).warning(),
        evaluate_fingerprint=evaluate_thresholds.fingerprint,
        span_years=span_years,
        events_per_year=events_per_year,
        sharpe_target=target_model.sharpe_target,
        live_retention=target_model.live_retention,
        cost_floor_bps=target_model.cost_floor_bps,
        icc=icc_report,
        universe_sha256=universe_sha,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"power_{tag}.md"
    json_path = out_dir / f"power_{tag}.json"
    md_path.write_text(render_power_report(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "mode": "power_only",
                "label": report.label,
                "timeframe": timeframe_name,
                "tickers": list(tickers),
                "universe_sha256": universe_sha,
                "window_sessions": args.window_sessions,
                "windows": list(windows),
                "rows": [row.describe() for row in rows],
                "events": {
                    "total": main_scan.total_events,
                    "eligible": main_scan.events,
                    "per_ticker": main_scan.per_ticker(),
                    "out_of_history": main_scan.out_of_history,
                    "future": main_scan.future,
                    "sealed": main_scan.sealed,
                    "declared_after_entry": main_scan.declared_after_entry,
                    "missing_declared_date": main_scan.missing_declared_date,
                },
                "sigma": main_sigma.describe(),
                "sigma_mode": "excess" if args.excess else "raw",
                "median_sigma_bps": report.median_sigma_bps,
                "target_edge_bps": evaluate_thresholds.target_edge_bps,
                "power": evaluate_thresholds.power,
                "alpha": evaluate_thresholds.confirm_alpha,
                "n_trials": n_trials,
                "grid_cells": grid_cells,
                "measurable": report.measurable,
                "cluster_by": args.cluster_by,
                "icc": icc_report.describe() if icc_report is not None else None,
                "economic_scale": {
                    "span_years": span_years,
                    "events_per_year": events_per_year,
                    "target_idea_bps": idea_target,
                    "sharpe_target": target_model.sharpe_target,
                    "live_retention": target_model.live_retention,
                    "cost_floor_bps": target_model.cost_floor_bps,
                },
                "dividends": f"{calendar.version}+{calendar.sha256[:12]}",
                "evaluate": evaluate_thresholds.fingerprint,
                "sealed_start": evaluate_thresholds.sealed_start.isoformat(),
                "journal": {"path": str(journal_path), "touched": False},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"power-only: {report.label} / {timeframe_name} tickers={len(tickers)}")
    print(
        f"events: eligible={main_scan.events} of {main_scan.total_events} "
        f"(out_of_history={main_scan.out_of_history} future={main_scan.future} "
        f"sealed={main_scan.sealed} declared_after_entry={main_scan.declared_after_entry} "
        f"missing_declared_date={main_scan.missing_declared_date})"
    )
    for row in rows:
        print(
            f"window {row.window_sessions:>2}: events={row.events:>4} "
            f"clusters={row.clusters:>3} sigma={_fmt(row.sigma_bps)} "
            f"MDE={_fmt(row.mde_bps)} MDE@{n_trials}={_fmt(row.mde_adjusted_bps)} "
            f"MDEcluster={_fmt(row.mde_cluster_bps)} "
            f"target_idea={_fmt(row.target_idea_bps)}"
        )
    print(
        f"config target={evaluate_thresholds.target_edge_bps:g} bps | "
        f"power={evaluate_thresholds.power:.2f} alpha={evaluate_thresholds.confirm_alpha:g} "
        f"| clusters by {args.cluster_by}"
    )
    if events_per_year is not None:
        print(
            f"economic scale: {events_per_year:.1f} trades/year, "
            f"idea target = {target_model.sharpe_target:g} * sigma / sqrt(trades a year) "
            f"/ {target_model.live_retention:g} = {_fmt(idea_target)}"
        )
    if icc_report is not None:
        for level in icc_report.levels:
            print(
                f"icc {level.by:>7}: clusters={level.clusters:>3} "
                f"icc={_num(level.icc.icc)} "
                f"ci=[{_num(level.icc.ci_low)}; {_num(level.icc.ci_high)}] "
                f"lag1={_num(level.lag1)}"
            )
        print(f"gate level: {icc_report.gate_level} — {icc_report.decision.reason}")
        print(
            f"gate MDE={_fmt(icc_report.gate_mde_bps)} "
            f"at {icc_report.gate_tickers} tickers "
            f"(current {_fmt(icc_report.current_mde_bps)} at {len(tickers)}) "
            f"target={_fmt(icc_report.target_idea_bps)}"
        )
        for row in icc_report.projection:
            marker = "*" if row.capped else " "
            print(
                f"projection {row.tickers:>4}{marker} events={row.events:>6.0f} "
                f"clusters={row.clusters:>3} deff={row.design_effect:>5.2f} "
                f"n_eff={row.effective_events:>6.1f} MDE={_fmt(row.mde_bps)} "
                f"{'below' if row.below_target else 'above'} target"
            )
        print(
            f"ticker cap: {icc_report.ticker_cap} ({icc_report.ticker_cap_source}); "
            f"available now: {icc_report.available_now}"
        )
    measurable = report.measurable
    verdict = "n/a" if measurable is None else ("measurable" if measurable else "not measurable")
    print(f"POWER: {verdict}")
    print("journal: not touched (pre-check, no idea slot spent)")
    print(f"report: {md_path}")
    print(f"snapshot: {json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.grid_cells is not None and args.grid_cells < 1:
        raise SystemExit("--grid-cells must be >= 1")

    if args.record_legacy:
        journal_path = Path(args.journal)
        written = record_legacy_baseline(
            variants=LEGACY_VARIANTS, notes=LEGACY_NOTES, path=journal_path
        )
        entries = load_hypotheses(journal_path)
        thresholds = load_evaluate_thresholds(args.thresholds_file)
        state = budget_state(entries, datetime.now(UTC).date(), thresholds.budget)
        print(f"legacy recorded: kind={written.kind} grid_cells={written.grid_cells}")
        print(f"trial_count={trial_count(entries)} ideas_used={state.used}/{state.limit}")
        print(f"journal: {journal_path}")
        return 0

    if args.power_only:
        return run_power_only(args)

    if not args.strategy or not args.hypothesis:
        raise SystemExit(
            "--strategy and --hypothesis are required unless --record-legacy is set"
        )

    grid_cells = args.grid_cells if args.grid_cells is not None else 1

    load_dotenv(ROOT / ".env")
    required = ("PG_HOST", "PG_USER", "PG_PASSWORD", "PG_DBNAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    portfolio = load_portfolio(args.universe)
    timeframe_name = args.timeframe or portfolio.timeframe
    verdict_thresholds = load_verdict_thresholds(args.thresholds_file)
    pool_thresholds = load_multi_instrument_thresholds(args.thresholds_file)
    heatmap_thresholds = load_heatmap_thresholds(args.thresholds_file)
    dividend_thresholds = load_dividend_thresholds(args.thresholds_file)
    evaluate_thresholds = load_evaluate_thresholds(args.thresholds_file)

    manual = parse_tickers(args.tickers)
    tickers = list(manual) if manual else list(portfolio.tickers)
    universe_source = "manual" if manual else "portfolio"
    if args.limit:
        tickers = tickers[: args.limit]

    instruments = load_instruments(INSTRUMENTS_PATH)
    unknown = [ticker for ticker in tickers if ticker not in instruments]
    if unknown:
        raise SystemExit(f"Tickers missing from config/instruments.json: {', '.join(unknown)}")

    journal_path = Path(args.journal)
    entries = load_hypotheses(journal_path)
    today = datetime.now(UTC).date()
    state = budget_state(entries, today, evaluate_thresholds.budget)
    if not args.dry_run and state.exhausted:
        raise SystemExit(
            f"quarterly idea budget exhausted: {state.used} of {state.limit} ideas used"
        )

    config_payload = {
        "strategy": args.strategy,
        "timeframe": timeframe_name,
        "tickers": list(tickers),
        "universe_source": universe_source,
        "portfolio": portfolio.fingerprint,
        "parameters": portfolio.parameters.describe(),
        "validation": verdict_thresholds.fingerprint,
        "evaluate": evaluate_thresholds.fingerprint,
        "grid_cells": grid_cells,
        "model_revision": dividend_thresholds.model_revision,
    }
    config_digest = config_sha256(config_payload)

    costs_config = load_costs(args.costs_file)
    history = HistoryManager(broker=None)  # type: ignore[arg-type]
    timeframe = Timeframe(timeframe_name)

    raw = {
        ticker: history.get_candles(instruments[ticker]["figi"], timeframe)
        for ticker in tickers
    }
    empty = [ticker for ticker, candles in raw.items() if len(candles) < 2]
    if empty:
        raise SystemExit(f"No candles for: {', '.join(empty)}")

    # Technical guarantee: the sealed period is never read by default.
    sealed_bars_cut = sum(len(candles) for candles in raw.values())
    raw = sealed_rows(raw, evaluate_thresholds.sealed_start)
    sealed_bars_cut -= sum(len(candles) for candles in raw.values())

    if verdict_thresholds.holdout_start is None or verdict_thresholds.holdout_end is None:
        raise SystemExit("verdict.holdout_start / holdout_end must be set in the config")

    holdout_start = datetime.combine(
        verdict_thresholds.holdout_start,
        datetime.min.time(),
        tzinfo=next(candles[0].open_time.tzinfo for candles in raw.values() if candles),
    )
    holdout_end = datetime.combine(
        verdict_thresholds.holdout_end,
        datetime.min.time(),
        tzinfo=holdout_start.tzinfo,
    )
    warmup = timedelta(seconds=timeframe.to_timedelta.total_seconds() * args.warmup_bars)

    full = raw
    fit = {ticker: slice_window(candles, None, holdout_start, 0) for ticker, candles in raw.items()}
    holdout = {
        ticker: slice_window(candles, holdout_start - warmup, holdout_end, 0)
        for ticker, candles in raw.items()
    }

    calendar = load_dividends(args.dividends_file)
    resolved = resolve_events(
        calendar,
        raw,
        gap_match_ratio=dividend_thresholds.gap_match_ratio,
        t1_settlement_switch=dividend_thresholds.t1_settlement_switch,
    )
    ex_bars = applied_ex_bars(resolved)
    coverage = build_coverage(
        calendar,
        resolved,
        {ticker: gap_series(candles) for ticker, candles in raw.items()},
        tickers=tickers,
        years=years_from_candles(raw),
        gap_threshold_percent=pool_thresholds.gap_threshold_percent,
    ).describe()

    logger.info("Pool: fit window")
    pooled_fit = run_pool(
        fit,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_for=costs_config.for_ticker,
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    fit_interval = interval_at(pooled_fit, pool_thresholds, evaluate_thresholds.screening_alpha)

    logger.info("Pool: full history before the sealed period")
    pooled_full = run_pool(
        full,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_for=costs_config.for_ticker,
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    pool_interval = interval(pooled_full, pool_thresholds)
    pool_summary = summarize(pooled_full, pool_thresholds)

    logger.info("Pool: development holdout")
    pooled_holdout = run_pool(
        holdout,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_for=costs_config.for_ticker,
        thresholds=pool_thresholds,
        trade_start=holdout_start,
        trade_end=holdout_end,
        ex_bars_by_ticker=ex_bars,
    )
    holdout_interval = interval(pooled_holdout, pool_thresholds)

    logger.info("Pool: doubled costs")
    pooled_doubled = run_pool(
        full,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_for=doubled_costs(costs_config, verdict_thresholds.cost_multiplier),
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    doubled_interval = interval(pooled_doubled, pool_thresholds)

    logger.info("Pool: TP penalty")
    pooled_penalty = run_pool(
        full,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_for=tp_penalty_costs(costs_config, verdict_thresholds.tp_penalty_bps),
        thresholds=pool_thresholds,
        ex_bars_by_ticker=ex_bars,
    )
    penalty_interval = interval(pooled_penalty, pool_thresholds)

    variants = leave_one_out_variants(
        pooled_full, thresholds=pool_thresholds, tickers=tuple(tickers)
    )
    without_best = next(
        (variant for variant in variants if variant.name == WITHOUT_BEST_TICKER), None
    )
    without_best_interval = (
        IntervalResult(
            trades=without_best.summary.trades,
            net_bps=without_best.summary.net_bps,
            ci_low=without_best.bootstrap.ci_low,
            ci_high=without_best.bootstrap.ci_high,
        )
        if without_best is not None
        else None
    )

    logger.info("Heatmap around the traded cell")
    tp_values = grid_around(portfolio.parameters.tp_percent, args.grid_step, args.grid_steps)
    sl_values = grid_around(portfolio.parameters.sl_percent, args.grid_step, args.grid_steps)
    _, heatmap_verdict = pool_heatmap(
        fit,
        strategy=args.strategy,
        timeframe=timeframe_name,
        config=portfolio,
        costs_config=costs_config,
        tp_values=tp_values,
        sl_values=sl_values,
        heatmap_thresholds=heatmap_thresholds,
    )

    logger.info("Null test")
    null_result = null_test(
        synthetic_candles(),
        seeds=args.null_seeds,
        tp_percent=portfolio.parameters.tp_percent,
        sl_percent=portfolio.parameters.sl_percent,
        costs=costs_config.for_ticker(None),
        timeframe=timeframe_name,
    )

    n_trials_before = trial_count(entries)
    n_trials = n_trials_before + grid_cells
    mde = minimum_detectable_effect(
        pool_interval, alpha=evaluate_thresholds.confirm_alpha, power=evaluate_thresholds.power
    )
    mde_adjusted = mde_with_trials(
        pool_interval,
        alpha=evaluate_thresholds.confirm_alpha,
        power=evaluate_thresholds.power,
        n_trials=n_trials,
    )
    sigma = sigma_bps(pool_interval, alpha=evaluate_thresholds.confirm_alpha)
    needed = trades_needed(
        target_bps=evaluate_thresholds.target_edge_bps,
        sigma=sigma,
        alpha=evaluate_thresholds.confirm_alpha,
        power=evaluate_thresholds.power,
        n_trials=n_trials,
    )

    verdict_inputs = VerdictInputs(
        pool=pool_interval,
        oos=holdout_interval,
        pool_without_best=without_best_interval,
        costs_doubled=doubled_interval,
        tp_penalty=penalty_interval,
        null_test=null_result.verdict,
        heatmap_status=heatmap_verdict.status,
        heatmap_plateau=heatmap_verdict.is_plateau,
        tradable=all(costs_config.for_ticker(ticker).is_tradable for ticker in tickers),
        best_ticker=pool_summary.best_ticker,
        best_contribution_percent=pool_summary.best_contribution_percent,
        n_trials=n_trials,
        extra_meta={
            "strategy": args.strategy,
            "timeframe": timeframe_name,
            "portfolio_version": portfolio.fingerprint,
            "tickers": len(tickers),
            "tp_percent": portfolio.parameters.tp_percent,
            "sl_percent": portfolio.parameters.sl_percent,
            "costs_version": f"{costs_config.version}+{costs_config.sha256[:12]}",
            "commissions": describe_commissions(),
            "dividends": f"{calendar.version}+{calendar.sha256[:12]}",
            "model_revision": dividend_thresholds.model_revision,
            "dividend_coverage": coverage,
            "sealed_start": evaluate_thresholds.sealed_start.isoformat(),
            "sealed_accessed": False,
        },
    )
    base = evaluate_verdict(verdict_inputs, verdict_thresholds)

    outcome = decide_outcome(
        pool=pool_interval,
        base=base,
        mde_adjusted=mde_adjusted,
        target_edge_bps=evaluate_thresholds.target_edge_bps,
        confirm=None,
        sealed_confirmed=False,
        sigma=sigma,
        mde=mde,
        trades_needed_for_target=needed,
    )

    tag = args.tag or f"evaluate-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    recorded = False
    journal_warning: str | None = None
    if args.dry_run:
        logger.info("Dry run: journal not written, budget not spent")
    else:
        try:
            record_attempt(
                hypothesis=args.hypothesis,
                strategy=args.strategy,
                timeframe=timeframe_name,
                tickers=tickers,
                universe_source=universe_source,
                config=config_payload,
                grid_cells=grid_cells,
                tag=tag,
                notes=f"config_sha256={config_digest[:12]}",
                path=journal_path,
                limit=evaluate_thresholds.budget,
            )
            recorded = True
        except TrialBudgetExceeded as exc:
            raise SystemExit(str(exc)) from exc
        journal_warning = journal_git_state(journal_path, root=ROOT).warning()

    report = EvaluateReport(
        strategy=args.strategy,
        timeframe=timeframe_name,
        hypothesis=args.hypothesis,
        outcome=outcome,
        fit=fit_interval,
        pool=pool_interval,
        development=holdout_interval,
        costs_doubled=doubled_interval,
        tp_penalty=penalty_interval,
        without_best=without_best_interval,
        base_status=base.status,
        null_test=null_result.verdict,
        heatmap_status=heatmap_verdict.status,
        heatmap_plateau=heatmap_verdict.is_plateau,
        tradable=verdict_inputs.tradable,
        dividend_coverage=coverage,
        ideas_used=state.used,
        ideas_limit=evaluate_thresholds.budget,
        grid_cells=grid_cells,
        n_trials=n_trials,
        target_edge_bps=evaluate_thresholds.target_edge_bps,
        power=evaluate_thresholds.power,
        confirm_alpha=evaluate_thresholds.confirm_alpha,
        screening_alpha=evaluate_thresholds.screening_alpha,
        sealed_start=evaluate_thresholds.sealed_start,
        sealed_accessed=False,
        recorded=recorded,
        sealed_bars_cut=sealed_bars_cut,
        journal_warning=journal_warning,
        portfolio_fingerprint=portfolio.fingerprint,
        evaluate_fingerprint=evaluate_thresholds.fingerprint,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"evaluate_{tag}.md"
    json_path = out_dir / f"evaluate_{tag}.json"
    md_path.write_text(render_report(report), encoding="utf-8")
    payload = {
        **evaluate_thresholds.describe(),
        **outcome.describe(),
        "strategy": args.strategy,
        "timeframe": timeframe_name,
        "hypothesis": args.hypothesis,
        "recorded": recorded,
        "tag": tag,
        "config_sha256": config_digest,
        "n_trials": n_trials,
        "ideas_used": state.used,
        "base_verdict": base.status,
        "intervals": {
            "fit": fit_interval.describe(),
            "pool": pool_interval.describe(),
            "development": holdout_interval.describe(),
            "costs_doubled": doubled_interval.describe(),
            "tp_penalty": penalty_interval.describe(),
            "without_best": (
                without_best_interval.describe() if without_best_interval is not None else None
            ),
        },
        "heatmap": {
            "anchor_tp": portfolio.parameters.tp_percent,
            "anchor_sl": portfolio.parameters.sl_percent,
            "verdict": heatmap_verdict.describe(),
        },
        "null_test": null_result.to_dict(),
        "dividend_coverage": coverage,
        "sealed": {
            "start": evaluate_thresholds.sealed_start.isoformat(),
            "accessed": False,
        },
        "journal": {
            "path": str(journal_path),
            "recorded": recorded,
            "warning": journal_warning,
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"strategy={args.strategy} {timeframe_name} tickers={len(tickers)}")
    print(
        f"pool: trades={pool_interval.trades} net={pool_interval.net_bps:+.2f} bps "
        f"CI95 [{pool_interval.ci_low:+.2f}; {pool_interval.ci_high:+.2f}]"
    )
    print(
        f"development: trades={holdout_interval.trades} "
        f"net={holdout_interval.net_bps:+.2f} bps "
        f"CI95 [{holdout_interval.ci_low:+.2f}; {holdout_interval.ci_high:+.2f}]"
    )
    print(
        f"power: sigma={_fmt(sigma)} MDE={_fmt(mde)} "
        f"MDE@{n_trials}={_fmt(mde_adjusted)} target={evaluate_thresholds.target_edge_bps:g}"
    )
    print(f"base verdict: {base.status}")
    print(f"VERDICT: {outcome.status}")
    for reason in outcome.reasons:
        print(f"  {reason}")
    if not recorded:
        print("NOT RECORDED (dry run)")
    if journal_warning:
        print(f"WARNING: {journal_warning}")
    print(f"report: {md_path}")
    print(f"snapshot: {json_path}")
    return 0


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} bps"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
