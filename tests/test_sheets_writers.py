"""Regression coverage for the schema-evolution bug found while demoing Step 6 against the
live Sheet: TAB_SCHEMAS gained `promise_to_pay_kept_rate_by_value` for a tab (Metrics) whose
header row already existed, and the real backend's upsert_rows only ever writes columns
present in the sheet's current header row — so the new field was computed correctly and then
silently discarded on every write, with no error. See docs/FAILURES.md."""

from __future__ import annotations

from datetime import date

from collections_agent.models.domain import MetricsRow
from collections_agent.sheets.writers import upsert_models


def _metrics_row() -> MetricsRow:
    return MetricsRow(
        date=date(2026, 9, 17),
        calls_made=1,
        calls_suppressed=0,
        contact_rate=1.0,
        structured_outcome_rate=1.0,
        promise_to_pay_kept_rate=0.5,
        promise_to_pay_kept_rate_by_value=0.09,
        disputes_surfaced=0,
        human_review_rate=0.0,
    )


def test_new_schema_column_backfills_into_an_existing_header_row(fake_sheets_backend):
    """Simulates a tab whose header row predates a TAB_SCHEMAS change: ensure_worksheet is
    called once with the old (narrower) header set, then upsert_models is called with the
    current (wider) schema — the missing column must be backfilled, not silently dropped."""
    stale_headers = [
        "date",
        "calls_made",
        "calls_suppressed",
        "contact_rate",
        "structured_outcome_rate",
        "promise_to_pay_kept_rate",
        "disputes_surfaced",
        "human_review_rate",
    ]
    fake_sheets_backend.ensure_worksheet("Metrics", stale_headers)

    upsert_models(fake_sheets_backend, "Metrics", [_metrics_row()])

    row = fake_sheets_backend.read_all("Metrics")[0]
    assert row["promise_to_pay_kept_rate_by_value"] == "0.09"


def test_rerunning_upsert_after_backfill_does_not_duplicate_the_row(fake_sheets_backend):
    stale_headers = ["date", "calls_made"]
    fake_sheets_backend.ensure_worksheet("Metrics", stale_headers)

    upsert_models(fake_sheets_backend, "Metrics", [_metrics_row()])
    upsert_models(fake_sheets_backend, "Metrics", [_metrics_row()])

    assert len(fake_sheets_backend.read_all("Metrics")) == 1
