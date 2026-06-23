"""Unit tests for tools.soak_assertions."""

from __future__ import annotations

from pathlib import Path

from tools.soak_assertions import (
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


def test_report_passed_is_true_when_all_invariants_pass() -> None:
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
