"""Hypothesis journal: attempts are counted from a file, not from a flag (ADR-029)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from src.backtest import hypothesis_log as journal
from src.backtest.hypothesis_log import (
    HypothesisEntry,
    HypothesisLogError,
    TrialBudgetExceeded,
    append_hypothesis,
    budget_state,
    config_sha256,
    load_hypotheses,
    mark_sealed_access,
    quarter_bounds,
    quarter_trials,
    record_attempt,
    sealed_access_count,
    trial_count,
)

UTC_TZ = UTC
NOW = datetime(2026, 11, 7, 9, 15, tzinfo=UTC_TZ)
SHA = "a" * 64


def entry(**overrides: Any) -> HypothesisEntry:
    payload: dict[str, Any] = {
        "recorded_at": NOW,
        "hypothesis": "momentum continues after a pullback",
        "strategy": "dual_ma_crossover_after_testing",
        "timeframe": "4h",
        "tickers": ("SBER", "GAZP"),
        "universe_source": "portfolio",
        "config_sha256": SHA,
        "grid_cells": 1,
    }
    payload.update(overrides)
    return HypothesisEntry(**payload)


# ═══════════════════════════════════════════════════════════════════════════
# APPEND-ONLY
# ═══════════════════════════════════════════════════════════════════════════


def test_missing_journal_is_empty(tmp_path):
    assert load_hypotheses(tmp_path / "absent.jsonl") == []


def test_append_creates_the_file_and_keeps_previous_lines(tmp_path):
    path = tmp_path / "journal" / "hypotheses.jsonl"

    append_hypothesis(entry(hypothesis="first"), path)
    first_line = path.read_text(encoding="utf-8")
    append_hypothesis(entry(hypothesis="second", grid_cells=20), path)
    second_line = path.read_text(encoding="utf-8")

    assert second_line.startswith(first_line)
    assert len(load_hypotheses(path)) == 2


def test_load_returns_entries_in_append_order(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(hypothesis="first"), path)
    append_hypothesis(entry(hypothesis="second"), path)

    assert [item.hypothesis for item in load_hypotheses(path)] == ["first", "second"]


def test_broken_line_names_its_number(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(), path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(HypothesisLogError, match=":2:"):
        load_hypotheses(path)


def test_missing_field_is_reported(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    path.write_text('{"hypothesis": "x"}\n', encoding="utf-8")

    with pytest.raises(HypothesisLogError, match="missing field"):
        load_hypotheses(path)


# ═══════════════════════════════════════════════════════════════════════════
# ВАЛИДАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"recorded_at": datetime(2026, 11, 7, 9, 15)}, "timezone-aware"),
        ({"hypothesis": "  "}, "hypothesis"),
        ({"tickers": ()}, "tickers"),
        ({"universe_source": "screener"}, "universe_source"),
        ({"config_sha256": "abc"}, "config_sha256"),
        ({"grid_cells": 0}, "grid_cells"),
    ],
)
def test_invalid_entry_is_rejected(overrides, message):
    with pytest.raises(HypothesisLogError, match=message):
        entry(**overrides)


def test_config_hash_ignores_key_order():
    left = {"a": 1, "b": [2, 3]}
    right = {"b": [2, 3], "a": 1}

    assert config_sha256(left) == config_sha256(right)
    assert config_sha256(left) != config_sha256({"a": 1, "b": [2, 4]})


# ═══════════════════════════════════════════════════════════════════════════
# СЧЁТЧИК ПОПЫТОК
# ═══════════════════════════════════════════════════════════════════════════


def test_trials_count_grid_cells_across_strategies_and_ideas():
    entries = [
        entry(strategy="dual_ma", hypothesis="a", grid_cells=20),
        entry(strategy="sma", hypothesis="b", grid_cells=1),
        entry(strategy="dual_ma", hypothesis="c", grid_cells=5),
    ]

    assert trial_count(entries) == 26


def test_trials_are_global_not_per_strategy():
    """A new strategy name must not reset the counter."""
    entries = [entry(strategy="one", grid_cells=3), entry(strategy="two", grid_cells=4)]

    assert trial_count(entries) == 7


def test_quarter_bounds_and_quarter_filter():
    assert quarter_bounds(date(2026, 11, 7)) == (date(2026, 10, 1), date(2027, 1, 1))
    assert quarter_bounds(date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 4, 1))

    entries = [
        entry(recorded_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC), grid_cells=2),
        entry(recorded_at=datetime(2026, 10, 1, 0, 1, tzinfo=UTC), grid_cells=3),
        entry(recorded_at=datetime(2026, 12, 31, 23, 59, tzinfo=UTC), grid_cells=4),
        entry(recorded_at=datetime(2027, 1, 1, 0, 1, tzinfo=UTC), grid_cells=5),
    ]

    assert quarter_trials(entries, date(2026, 11, 7)) == 7
    assert quarter_trials(entries, date(2027, 2, 1)) == 5


def test_budget_state_reports_used_and_remaining():
    entries = [entry(recorded_at=NOW, grid_cells=20)]
    state = budget_state(entries, date(2026, 11, 7), limit=20)

    assert state.used == 20
    assert state.remaining == 0
    assert state.exhausted


def test_sealed_access_is_counted():
    entries = [
        entry(sealed_access=True),
        entry(sealed_access=False),
        entry(sealed_access=True),
    ]

    assert sealed_access_count(entries) == 2


# ═══════════════════════════════════════════════════════════════════════════
# ЗАПИСЬ С ПРОВЕРКОЙ БЮДЖЕТА
# ═══════════════════════════════════════════════════════════════════════════


def test_record_attempt_writes_and_counts(tmp_path):
    path = tmp_path / "hypotheses.jsonl"

    written = record_attempt(
        hypothesis="first idea",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={"tp": 2.5, "sl": 2.0},
        grid_cells=20,
        path=path,
        recorded_at=NOW,
    )

    assert written.config_sha256 == config_sha256({"tp": 2.5, "sl": 2.0})
    assert trial_count(load_hypotheses(path)) == 20


def test_budget_is_enforced_before_the_line_is_written(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    record_attempt(
        hypothesis="first",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        grid_cells=19,
        path=path,
        recorded_at=NOW,
        limit=20,
    )

    with pytest.raises(TrialBudgetExceeded, match="19 of 20"):
        record_attempt(
            hypothesis="second",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            grid_cells=2,
            path=path,
            recorded_at=NOW,
            limit=20,
        )

    assert len(load_hypotheses(path)) == 1


def test_budget_resets_in_the_next_quarter(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    record_attempt(
        hypothesis="q4",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        grid_cells=20,
        path=path,
        recorded_at=datetime(2026, 12, 20, 12, 0, tzinfo=UTC),
        limit=20,
    )

    written = record_attempt(
        hypothesis="q1",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        grid_cells=20,
        path=path,
        recorded_at=datetime(2027, 1, 10, 12, 0, tzinfo=UTC),
        limit=20,
    )

    assert written.hypothesis == "q1"


def test_marking_sealed_access_appends_without_rewriting(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    original = append_hypothesis(entry(), path)

    marked = mark_sealed_access(original, path)

    assert original.sealed_access is False
    assert marked.sealed_access is True
    assert sealed_access_count(load_hypotheses(path)) == 1
    assert len(load_hypotheses(path)) == 2


def test_naive_timestamp_is_refused_before_writing(tmp_path):
    path = tmp_path / "hypotheses.jsonl"

    with pytest.raises(HypothesisLogError, match="timezone-aware"):
        record_attempt(
            hypothesis="naive",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            path=path,
            recorded_at=datetime(2026, 11, 7, 9, 15),
        )

    assert load_hypotheses(path) == []


def test_default_budget_is_twenty():
    assert journal.TRIAL_BUDGET_PER_QUARTER == 20


def test_entry_survives_a_json_round_trip(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(notes="note", tag="tag-1", grid_cells=3), path)

    restored = load_hypotheses(path)[0]

    assert restored.tickers == ("SBER", "GAZP")
    assert restored.grid_cells == 3
    assert restored.tag == "tag-1"
    assert restored.recorded_at == NOW
    assert restored.recorded_at.utcoffset() == timedelta(0)
