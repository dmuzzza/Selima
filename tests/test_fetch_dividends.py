"""Dividend fetch: deterministic merge of payouts that share one ex-date."""

from __future__ import annotations

from scripts.fetch_dividends import merge_payouts, raw_fingerprint


def event(ticker: str, day: str, amount: float, record: str | None = None) -> dict:
    return {
        "ticker": ticker,
        "record_date": record,
        "last_buy_date": day,
        "dividend_gross": amount,
        "source": "api",
    }


def test_two_payouts_on_one_ex_date_are_summed():
    """CHMF 2024-06-17: 191.51 for 2023 plus 38.30 for Q1 2024."""
    merged = merge_payouts(
        [
            event("CHMF", "2024-06-17", 191.51, "2024-06-18"),
            event("CHMF", "2024-06-17", 38.3, "2024-06-18"),
        ]
    )
    assert len(merged) == 1
    assert merged[0]["dividend_gross"] == 229.81
    assert [item["dividend_gross"] for item in merged[0]["payouts"]] == [191.51, 38.3]


def test_lukoi_payouts_sum_to_793():
    merged = merge_payouts(
        [
            event("LKOH", "2022-12-20", 537.0, "2022-12-21"),
            event("LKOH", "2022-12-20", 256.0, "2022-12-21"),
        ]
    )
    assert merged[0]["dividend_gross"] == 793.0


def test_single_event_keeps_no_payouts_split():
    merged = merge_payouts([event("SBER", "2024-07-10", 33.3, "2024-07-11")])
    assert merged[0]["dividend_gross"] == 33.3
    assert "payouts" not in merged[0]


def test_merge_is_deterministic_regardless_of_input_order():
    first = merge_payouts(
        [
            event("CHMF", "2024-06-17", 38.3, "2024-06-18"),
            event("CHMF", "2024-06-17", 191.51, "2024-06-18"),
            event("SBER", "2024-07-10", 33.3, "2024-07-11"),
        ]
    )
    second = merge_payouts(
        [
            event("SBER", "2024-07-10", 33.3, "2024-07-11"),
            event("CHMF", "2024-06-17", 191.51, "2024-06-18"),
            event("CHMF", "2024-06-17", 38.3, "2024-06-18"),
        ]
    )
    assert first == second


def test_fingerprint_is_stable_and_counts_raw_events():
    raw = [
        event("SBER", "2024-07-10", 33.3, "2024-07-11"),
        event("SBER", "2024-07-10", 1.0, "2024-07-11"),
    ]
    first = raw_fingerprint(raw)
    second = raw_fingerprint(list(reversed(raw)))
    assert first == second
    assert first["count"] == 2
    assert first["per_ticker"] == {"SBER": 2}
    assert len(first["sha256"]) == 64
