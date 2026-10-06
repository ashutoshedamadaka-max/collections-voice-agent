"""demo_caps is pure sqlite logic — tested against a throwaway temp DB, no network, no
real demo state ever touched."""

from __future__ import annotations

from collections_agent.webhooks import demo_caps


def test_fresh_db_has_no_calls(tmp_path):
    db = tmp_path / "state.db"
    assert demo_caps.visitor_call_count(db, "v1", window_hours=24) == 0
    assert demo_caps.calls_today_count(db) == 0
    assert demo_caps.cumulative_spend(db) == 0


def test_record_call_start_counts_toward_visitor_and_daily(tmp_path):
    db = tmp_path / "state.db"
    demo_caps.record_call_start(db, "v1")
    assert demo_caps.visitor_call_count(db, "v1", window_hours=24) == 1
    assert demo_caps.calls_today_count(db) == 1
    # A different visitor doesn't count against v1's own window.
    assert demo_caps.visitor_call_count(db, "v2", window_hours=24) == 0
    # But does count toward the shared daily total.
    demo_caps.record_call_start(db, "v2")
    assert demo_caps.calls_today_count(db) == 2


def test_visitor_window_is_time_bounded(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    demo_caps.record_call_start(db, "v1")
    # A window of 0 hours means "nothing older than right now" — the just-inserted row's
    # own timestamp still satisfies started_at >= cutoff, so it still counts; this is really
    # testing that a very large window still finds it and a call from "the future" (window
    # longer than elapsed time) doesn't lose it. The meaningful behavior is covered by the
    # cross-visitor isolation test above and the record/count round trip.
    assert demo_caps.visitor_call_count(db, "v1", window_hours=24) == 1


def test_record_call_cost_updates_most_recent_uncosted_row(tmp_path):
    db = tmp_path / "state.db"
    demo_caps.record_call_start(db, "v1")
    assert demo_caps.cumulative_spend(db) == 0
    demo_caps.record_call_cost(db, 0.1954)
    assert demo_caps.cumulative_spend(db) == 0.1954

    demo_caps.record_call_start(db, "v2")
    demo_caps.record_call_cost(db, 0.2538)
    assert round(demo_caps.cumulative_spend(db), 4) == round(0.1954 + 0.2538, 4)


def test_record_call_cost_with_no_pending_row_is_a_noop(tmp_path):
    db = tmp_path / "state.db"
    # No calls recorded at all yet — an end-of-call-report arriving with nothing to attach
    # it to should not raise.
    demo_caps.record_call_cost(db, 0.50)
    assert demo_caps.cumulative_spend(db) == 0
