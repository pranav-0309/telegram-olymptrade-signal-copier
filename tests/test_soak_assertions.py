"""Unit tests for tools.soak_assertions."""

from __future__ import annotations

from pathlib import Path

from tools.soak_assertions import (
    LivenessRecord,
    RestartDrillResult,
    assert_invariants,
)


def _empty_drill() -> RestartDrillResult:
    return RestartDrillResult(
        restart_at_unix=0.0,
        restarted_at_unix=0.0,
        in_flight_signal_ids=[],
        completed_within_60s={},
    )


def test_assert_invariants_returns_report_with_nine_invariants() -> None:
    report = assert_invariants(
        app_log=Path("/nonexistent"),
        soak_log=Path("/nonexistent"),
        signals=[],
        stages=[],
        fixture=[],
        liveness_records=[],
        drill=_empty_drill(),
        expected_duration_seconds=24 * 3600,
    )
    assert len(report.invariant_results) == 9
    names = {r.name for r in report.invariant_results}
    assert names == {
        "uptime",
        "no_exceptions",
        "no_missed_triggers",
        "no_duplicate_trades",
        "no_dm_failures",
        "row_counts",
        "restart_drill",
        "telegram_liveness",
        "per_signal_outcomes",
    }


def test_report_passed_is_true_when_all_invariants_pass(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("[2026-06-22 10:00:00] notify: event=bot_started\n")
    now = 1_700_000_000.0
    records = [LivenessRecord(timestamp=now + i * 3600, connected=True) for i in range(25)]
    report = assert_invariants(
        app_log=log,
        soak_log=tmp_path / "soak.log",
        signals=[],
        stages=[],
        fixture=[],
        liveness_records=records,
        drill=_empty_drill(),
        expected_duration_seconds=24 * 3600,
    )
    assert report.passed is True


def test_report_to_markdown_includes_all_invariants() -> None:
    report = assert_invariants(
        app_log=Path("/nonexistent"),
        soak_log=Path("/nonexistent"),
        signals=[],
        stages=[],
        fixture=[],
        liveness_records=[],
        drill=_empty_drill(),
        expected_duration_seconds=24 * 3600,
    )
    md = report.to_markdown()
    assert "M9 Soak Report" in md
    for r in report.invariant_results:
        assert f"`{r.name}`" in md


def test_invariant_1_uptime_passes_when_bot_started_present(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("[2026-06-22 10:00:00] notify: event=bot_started\n")
    from tools.soak_assertions import assert_uptime

    r = assert_uptime(log, expected_duration_seconds=24 * 3600)
    assert r.passed
    assert "bot_started" in r.detail.lower() or "started" in r.detail.lower()


def test_invariant_1_uptime_fails_when_bot_started_missing(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("[2026-06-22 10:00:00] some other line\n")
    from tools.soak_assertions import assert_uptime

    r = assert_uptime(log, expected_duration_seconds=24 * 3600)
    assert not r.passed


def test_invariant_2_no_exceptions_passes_when_no_traceback(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("[2026-06-22 10:00:00] notify: event=bot_started\n")
    from tools.soak_assertions import assert_no_exceptions

    r = assert_no_exceptions(log)
    assert r.passed


def test_invariant_2_no_exceptions_fails_when_traceback_present(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text(
        "[2026-06-22 10:00:00] notify: event=bot_started\n"
        "[2026-06-22 10:01:00] Traceback (most recent call last):\n"
        '  File "/x.py", line 1, in <module>\n'
        "    raise ValueError\n"
    )
    from tools.soak_assertions import assert_no_exceptions

    r = assert_no_exceptions(log)
    assert not r.passed


def test_invariant_3_no_missed_triggers_passes_when_all_within_tolerance() -> None:
    stages = [
        {
            "signal_id": "s1",
            "stage": "initial",
            "trigger_ts_unix": 1000.0,
            "placed_at_unix": 1000.5,
        },
        {
            "signal_id": "s2",
            "stage": "initial",
            "trigger_ts_unix": 2000.0,
            "placed_at_unix": 2001.0,
        },
    ]
    from tools.soak_assertions import assert_no_missed_triggers

    r = assert_no_missed_triggers(stages, tolerance_seconds=2.0)
    assert r.passed


def test_invariant_3_no_missed_triggers_fails_when_skew_exceeds_tolerance() -> None:
    stages = [
        {
            "signal_id": "s1",
            "stage": "initial",
            "trigger_ts_unix": 1000.0,
            "placed_at_unix": 1010.0,
        },
    ]
    from tools.soak_assertions import assert_no_missed_triggers

    r = assert_no_missed_triggers(stages, tolerance_seconds=2.0)
    assert not r.passed


def test_invariant_4_no_duplicate_trades_passes_when_unique() -> None:
    stages = [
        {"signal_id": "s1", "stage": "initial"},
        {"signal_id": "s1", "stage": "gale1"},
        {"signal_id": "s2", "stage": "initial"},
    ]
    from tools.soak_assertions import assert_no_duplicate_trades

    r = assert_no_duplicate_trades(stages)
    assert r.passed


def test_invariant_4_no_duplicate_trades_fails_when_duplicate() -> None:
    stages = [
        {"signal_id": "s1", "stage": "initial"},
        {"signal_id": "s1", "stage": "initial"},
    ]
    from tools.soak_assertions import assert_no_duplicate_trades

    r = assert_no_duplicate_trades(stages)
    assert not r.passed
    assert "s1" in r.detail


def test_invariant_5_no_dm_failures_passes_when_clean(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("[2026-06-22 10:00:00] notify: event=bot_started\n")
    from tools.soak_assertions import assert_no_dm_failures

    r = assert_no_dm_failures(log)
    assert r.passed


def test_invariant_5_no_dm_failures_fails_when_present(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text(
        "[2026-06-22 10:00:00] notify: event=bot_started\n"
        "[2026-06-22 10:05:00] DM send failed: rate limit\n"
    )
    from tools.soak_assertions import assert_no_dm_failures

    r = assert_no_dm_failures(log)
    assert not r.passed
    assert "DM send failed" in r.detail


def test_invariant_6_row_counts_match_expected_when_correct() -> None:
    signals = [
        {"signal_id": "s1", "status": "done_win"},
        {"signal_id": "s2", "status": "done_loss"},
    ]
    stages = [
        {"signal_id": "s1", "stage": "initial", "result": "win"},
        {"signal_id": "s2", "stage": "initial", "result": "loss"},
        {"signal_id": "s2", "stage": "gale1", "result": "loss"},
        {"signal_id": "s2", "stage": "gale2", "result": "loss"},
    ]
    fixture = [
        {"id": "f1", "expected_outcome": "win_at_initial"},
        {"id": "f2", "expected_outcome": "full_loss"},
    ]
    from tools.soak_assertions import assert_row_counts_match_expected

    r = assert_row_counts_match_expected(signals, stages, fixture)
    assert r.passed


def test_invariant_6_row_counts_match_expected_fails_when_mismatch() -> None:
    signals = [{"signal_id": "s1", "status": "done_win"}]
    stages = [{"signal_id": "s1", "stage": "initial", "result": "win"}]
    fixture = [{"id": "f1", "expected_outcome": "full_loss"}]
    from tools.soak_assertions import assert_row_counts_match_expected

    r = assert_row_counts_match_expected(signals, stages, fixture)
    assert not r.passed


def test_invariant_7_restart_drill_passes_when_all_completed_in_60s() -> None:
    from tools.soak_assertions import RestartDrillResult, assert_restart_drill

    drill = RestartDrillResult(
        restart_at_unix=1000.0,
        restarted_at_unix=1010.0,
        in_flight_signal_ids=["s1", "s2"],
        completed_within_60s={"s1": True, "s2": True},
    )
    r = assert_restart_drill(drill)
    assert r.passed


def test_invariant_7_restart_drill_fails_when_cascade_did_not_complete() -> None:
    from tools.soak_assertions import RestartDrillResult, assert_restart_drill

    drill = RestartDrillResult(
        restart_at_unix=1000.0,
        restarted_at_unix=1010.0,
        in_flight_signal_ids=["s1", "s2"],
        completed_within_60s={"s1": True, "s2": False},
    )
    r = assert_restart_drill(drill)
    assert not r.passed
    assert "s2" in r.detail


def test_invariant_8_telegram_liveness_passes_with_one_per_hour() -> None:
    from tools.soak_assertions import LivenessRecord, assert_telegram_liveness

    now = 1_700_000_000.0
    records = [LivenessRecord(timestamp=now + i * 3600, connected=True) for i in range(25)]
    r = assert_telegram_liveness(records, soak_duration_seconds=24 * 3600)
    assert r.passed


def test_invariant_8_telegram_liveness_fails_with_too_few() -> None:
    from tools.soak_assertions import LivenessRecord, assert_telegram_liveness

    now = 1_700_000_000.0
    records = [LivenessRecord(timestamp=now + i * 3600, connected=True) for i in range(5)]
    r = assert_telegram_liveness(records, soak_duration_seconds=24 * 3600)
    assert not r.passed


def test_invariant_9_per_signal_outcomes_with_signal_id_match() -> None:
    from tools.soak_assertions import assert_per_signal_outcomes

    signals = [{"signal_id": "s1", "status": "done_win"}]
    fixture = [{"id": "f1", "signal_id": "s1", "expected_outcome": "win_at_initial"}]
    r = assert_per_signal_outcomes(signals, fixture)
    assert r.passed


def test_invariant_9_per_signal_outcomes_mismatch_fails() -> None:
    from tools.soak_assertions import assert_per_signal_outcomes

    signals = [{"signal_id": "s1", "status": "done_win"}]
    fixture = [{"id": "f1", "signal_id": "s1", "expected_outcome": "full_loss"}]
    r = assert_per_signal_outcomes(signals, fixture)
    assert not r.passed
