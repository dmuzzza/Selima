"""Hypothesis journal: attempts are counted from a file, not from a flag (ADR-029)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from src.backtest import hypothesis_log as journal
from src.backtest.hypothesis_log import (
    GENESIS_HASH,
    HypothesisEntry,
    HypothesisLogError,
    TrialBudgetExceeded,
    append_hypothesis,
    budget_state,
    config_sha256,
    entry_hash,
    load_hypotheses,
    mark_sealed_access,
    quarter_bounds,
    quarter_ideas,
    quarter_trials,
    record_attempt,
    sealed_access_count,
    trial_count,
    verify_chain,
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
        entry(recorded_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC_TZ), grid_cells=2),
        entry(recorded_at=datetime(2026, 10, 1, 0, 1, tzinfo=UTC_TZ), grid_cells=3),
        entry(recorded_at=datetime(2026, 12, 31, 23, 59, tzinfo=UTC_TZ), grid_cells=4),
        entry(recorded_at=datetime(2027, 1, 1, 0, 1, tzinfo=UTC_TZ), grid_cells=5),
    ]

    assert quarter_trials(entries, date(2026, 11, 7)) == 7
    assert quarter_trials(entries, date(2027, 2, 1)) == 5
    assert quarter_ideas(entries, date(2026, 11, 7)) == 2


def test_budget_counts_ideas_not_grid_cells():
    """One heatmap of 20 cells is one idea: it must not burn the quarter."""
    entries = [entry(recorded_at=NOW, grid_cells=20)]
    state = budget_state(entries, date(2026, 11, 7), limit=20)

    assert state.used == 1
    assert state.remaining == 19
    assert not state.exhausted
    assert trial_count(entries) == 20


def test_budget_is_exhausted_after_twenty_ideas():
    entries = [entry(recorded_at=NOW) for _ in range(20)]
    state = budget_state(entries, date(2026, 11, 7), limit=20)

    assert state.used == 20
    assert state.remaining == 0
    assert state.exhausted


def test_sealed_marks_do_not_spend_the_idea_budget():
    entries = [entry(recorded_at=NOW), entry(recorded_at=NOW, kind="sealed", sealed_access=True)]
    state = budget_state(entries, date(2026, 11, 7), limit=20)

    assert state.used == 1
    assert sealed_access_count(entries) == 1


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
    assert budget_state(load_hypotheses(path), NOW.date()).used == 1


def test_budget_is_enforced_before_the_line_is_written(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    for number in range(20):
        record_attempt(
            hypothesis=f"idea {number}",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            path=path,
            recorded_at=NOW,
            limit=20,
        )

    with pytest.raises(TrialBudgetExceeded, match="20 of 20"):
        record_attempt(
            hypothesis="twenty first",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            path=path,
            recorded_at=NOW,
            limit=20,
        )

    assert len(load_hypotheses(path)) == 20


def test_a_wide_grid_still_costs_one_idea(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    record_attempt(
        hypothesis="wide grid",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        grid_cells=20,
        path=path,
        recorded_at=NOW,
        limit=20,
    )

    record_attempt(
        hypothesis="next idea",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        path=path,
        recorded_at=NOW,
        limit=20,
    )

    assert len(load_hypotheses(path)) == 2


def test_legacy_line_does_not_spend_the_budget(tmp_path):
    path = tmp_path / "hypotheses.jsonl"

    written = journal.record_legacy_baseline(
        variants=79,
        notes="ADR-015: 4, ADR-021: 42, ADR-022: 1, ADR-023: 25, ADR-028: 2",
        path=path,
        recorded_at=NOW,
    )

    assert written.kind == "legacy"
    assert budget_state(load_hypotheses(path), NOW.date()).used == 0
    assert trial_count(load_hypotheses(path)) == 79


def test_legacy_line_keeps_the_hash_chain(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    journal.record_legacy_baseline(
        variants=79, notes="pre-journal attempts", path=path, recorded_at=NOW
    )
    record_attempt(
        hypothesis="first real idea",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        path=path,
        recorded_at=NOW,
    )

    verify_chain(load_hypotheses(path))


def test_legacy_line_can_be_written_after_the_budget_is_spent(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    for number in range(20):
        record_attempt(
            hypothesis=f"idea {number}",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            path=path,
            recorded_at=NOW,
            limit=20,
        )

    written = journal.record_legacy_baseline(
        variants=10, notes="pre-journal attempts", path=path, recorded_at=NOW
    )

    assert written.kind == "legacy"
    assert len(load_hypotheses(path)) == 21


def test_legacy_line_requires_notes(tmp_path):
    with pytest.raises(journal.HypothesisLogError, match="notes"):
        journal.record_legacy_baseline(
            variants=10, notes="  ", path=tmp_path / "hypotheses.jsonl", recorded_at=NOW
        )


def test_legacy_variants_must_be_positive(tmp_path):
    with pytest.raises(journal.HypothesisLogError, match="variants"):
        journal.record_legacy_baseline(
            variants=0, notes="sources", path=tmp_path / "hypotheses.jsonl", recorded_at=NOW
        )


def test_budget_resets_in_the_next_quarter(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    for number in range(20):
        record_attempt(
            hypothesis=f"q4 {number}",
            strategy="dual_ma",
            timeframe="4h",
            tickers=["SBER"],
            universe_source="portfolio",
            config={},
            path=path,
            recorded_at=datetime(2026, 12, 20, 12, 0, tzinfo=UTC_TZ),
            limit=20,
        )

    written = record_attempt(
        hypothesis="q1",
        strategy="dual_ma",
        timeframe="4h",
        tickers=["SBER"],
        universe_source="portfolio",
        config={},
        path=path,
        recorded_at=datetime(2027, 1, 10, 12, 0, tzinfo=UTC_TZ),
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


# ═══════════════════════════════════════════════════════════════════════════
# ХЕШ-ЦЕПОЧКА
# ═══════════════════════════════════════════════════════════════════════════


def test_first_line_starts_from_the_genesis_hash(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(), path)

    assert load_hypotheses(path)[0].prev_hash == GENESIS_HASH


def test_each_line_points_at_the_previous_hash(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    first = append_hypothesis(entry(hypothesis="one"), path)
    second = append_hypothesis(entry(hypothesis="two"), path)

    assert second.prev_hash == entry_hash(first)
    assert load_hypotheses(path)[1].prev_hash == entry_hash(first)


def test_chain_accepts_a_valid_journal(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    for number in range(3):
        append_hypothesis(entry(hypothesis=f"idea {number}"), path)

    verify_chain(load_hypotheses(path))


def test_rewriting_a_line_breaks_the_chain(tmp_path):
    """A silent edit in git must not pass as evidence."""
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(hypothesis="one"), path)
    append_hypothesis(entry(hypothesis="two"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"hypothesis":"one"', '"hypothesis":"better"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(HypothesisLogError, match="broken hash chain"):
        load_hypotheses(path)


def test_deleting_a_line_breaks_the_chain(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(hypothesis="one"), path)
    append_hypothesis(entry(hypothesis="two"), path)
    append_hypothesis(entry(hypothesis="three"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    with pytest.raises(HypothesisLogError, match="broken hash chain"):
        load_hypotheses(path)


def test_reordering_lines_breaks_the_chain(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(hypothesis="one"), path)
    append_hypothesis(entry(hypothesis="two"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[1], lines[0]]) + "\n", encoding="utf-8")

    with pytest.raises(HypothesisLogError, match="broken hash chain"):
        load_hypotheses(path)


def test_chain_can_be_skipped_for_a_forensic_read(tmp_path):
    """`verify=False` exists for inspecting a broken file, not for normal use."""
    path = tmp_path / "hypotheses.jsonl"
    append_hypothesis(entry(hypothesis="one"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"hypothesis":"one"', '"hypothesis":"better"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert load_hypotheses(path, verify=False)[0].hypothesis == "better"


def test_entry_hash_changes_with_the_content():
    assert entry_hash(entry(hypothesis="one")) != entry_hash(entry(hypothesis="two"))


def test_entry_hash_is_independent_of_dict_key_order():
    assert entry_hash(entry()) == entry_hash(entry())


def test_new_line_links_to_the_last_one_after_a_sealed_mark(tmp_path):
    path = tmp_path / "hypotheses.jsonl"
    first = append_hypothesis(entry(), path)
    mark = mark_sealed_access(first, path)
    third = append_hypothesis(entry(hypothesis="after the seal"), path)

    assert mark.prev_hash == entry_hash(first)
    assert third.prev_hash == entry_hash(mark)
    verify_chain(load_hypotheses(path))
