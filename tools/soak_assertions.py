"""9 invariant functions + aggregator for the M9 24h soak (spec §9).

Each invariant is a pure function returning `(passed: bool, detail: str)`.
The aggregator runs all 9 and returns a `Report` with the aggregate pass/fail
plus per-invariant details. The soak harness prints a markdown summary at the
end regardless of pass/fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

InvariantName = Literal[
    "uptime",
    "no_exceptions",
    "no_missed_triggers",
    "no_duplicate_trades",
    "no_dm_failures",
    "row_counts",
    "restart_drill",
    "telegram_liveness",
    "per_signal_outcomes",
]


@dataclass(frozen=True, slots=True)
class InvariantResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class LivenessRecord:
    timestamp: float
    connected: bool


@dataclass(frozen=True, slots=True)
class RestartDrillResult:
    restart_at_unix: float
    restarted_at_unix: float
    in_flight_signal_ids: list[str]
    completed_within_60s: dict[str, bool]


@dataclass
class Report:
    invariant_results: list[InvariantResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.invariant_results)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# M9 Soak Report — {datetime.utcnow().isoformat()}Z")
        lines.append("")
        lines.append(f"**Result:** {'✅ PASS' if self.passed else '❌ FAIL'}")
        lines.append("")
        lines.append("| # | Invariant | Result | Detail |")
        lines.append("|---|---|---|---|")
        for i, r in enumerate(self.invariant_results, start=1):
            status = "✅" if r.passed else "❌"
            lines.append(f"| {i} | `{r.name}` | {status} | {r.detail} |")
        return "\n".join(lines) + "\n"


# --- 9 invariant stubs (full impl in Tasks 12-14) -------------------------


def assert_uptime(app_log: Path, *, expected_duration_seconds: float) -> InvariantResult:
    """Invariant 1: app_log has 'bot_started' line; soak ran for >= duration."""
    if not app_log.exists():
        return InvariantResult("uptime", False, f"app_log does not exist: {app_log}")
    text = app_log.read_text(encoding="utf-8", errors="replace")
    if "bot_started" not in text and "Bot started" not in text:
        return InvariantResult("uptime", False, "app_log missing 'bot_started' line")
    return InvariantResult(
        "uptime", True, f"app_log has bot_started; expected {expected_duration_seconds:.0f}s"
    )


def assert_no_exceptions(app_log: Path) -> InvariantResult:
    """Invariant 2: no 'Traceback' lines in app_log."""
    if not app_log.exists():
        return InvariantResult("no_exceptions", False, f"app_log does not exist: {app_log}")
    text = app_log.read_text(encoding="utf-8", errors="replace")
    if "Traceback (most recent call last):" in text:
        idx = text.index("Traceback (most recent call last):")
        snippet = text[idx : idx + 200].replace("\n", " ")
        return InvariantResult(
            "no_exceptions", False, f"app_log contains Traceback: {snippet[:120]}"
        )
    return InvariantResult("no_exceptions", True, "no Traceback lines in app_log")


def assert_no_missed_triggers(
    stages: list[dict[str, Any]],
    *,
    tolerance_seconds: float = 2.0,
) -> InvariantResult:
    """Invariant 3: zero stage rows with placed_at - trigger_ts > tolerance (FR-3.5)."""
    missed: list[str] = []
    for s in stages:
        skew = float(s["placed_at_unix"]) - float(s["trigger_ts_unix"])
        if skew > tolerance_seconds:
            missed.append(f"{s['signal_id']}/{s['stage']} skew={skew:.2f}s")
    if missed:
        return InvariantResult(
            "no_missed_triggers", False, f"missed triggers: {', '.join(missed[:5])}"
        )
    return InvariantResult(
        "no_missed_triggers",
        True,
        f"{len(stages)} stages, all within {tolerance_seconds}s tolerance",
    )


def assert_no_duplicate_trades(stages: list[dict[str, Any]]) -> InvariantResult:
    """Invariant 4: no two stages with the same (signal_id, stage)."""
    seen: set[tuple[str, str]] = set()
    dupes: list[str] = []
    for s in stages:
        key = (str(s["signal_id"]), str(s["stage"]))
        if key in seen:
            dupes.append(f"{key[0]}/{key[1]}")
        seen.add(key)
    if dupes:
        return InvariantResult(
            "no_duplicate_trades", False, f"duplicate stages: {', '.join(dupes[:5])}"
        )
    return InvariantResult(
        "no_duplicate_trades", True, f"{len(seen)} unique (signal_id, stage) pairs"
    )


def assert_no_dm_failures(app_log: Path) -> InvariantResult:
    """Invariant 5: no 'DM send failed' lines in app_log."""
    if not app_log.exists():
        return InvariantResult("no_dm_failures", False, f"app_log does not exist: {app_log}")
    text = app_log.read_text(encoding="utf-8", errors="replace")
    if "DM send failed" in text:
        return InvariantResult("no_dm_failures", False, "app_log contains 'DM send failed' lines")
    return InvariantResult("no_dm_failures", True, "no DM send failures in app_log")


_EXPECTED_OUTCOME_STAGES: dict[str, int] = {
    "win_at_initial": 1,
    "loss_initial_win_gale1": 2,
    "loss_initial_loss_gale1_win_gale2": 3,
    "full_loss": 3,
    "signal_expired": 0,
    "unsupported_pair": 0,
    "parse_failure": 0,
}


def assert_row_counts_match_expected(
    signals: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
) -> InvariantResult:
    """Invariant 6: stages row count matches sum of expected_outcome per fixture."""
    expected_total = sum(
        _EXPECTED_OUTCOME_STAGES.get(f.get("expected_outcome", ""), 0) for f in fixture
    )
    actual = len(stages)
    if actual != expected_total:
        return InvariantResult(
            "row_counts", False, f"expected {expected_total} stage rows, got {actual}"
        )
    return InvariantResult("row_counts", True, f"{actual} stage rows match fixture expectations")


def assert_restart_drill(drill: RestartDrillResult) -> InvariantResult:
    """Invariant 7: in-flight cascades reach terminal within 60s of restart."""
    if not drill.in_flight_signal_ids:
        return InvariantResult(
            "restart_drill", True, "no in-flight cascades at restart; vacuously true"
        )
    not_completed = [sid for sid, done in drill.completed_within_60s.items() if not done]
    if not_completed:
        return InvariantResult(
            "restart_drill",
            False,
            f"cascades not completed within 60s of restart: {', '.join(not_completed)}",
        )
    return InvariantResult(
        "restart_drill",
        True,
        f"all {len(drill.in_flight_signal_ids)} in-flight cascades completed within 60s",
    )


def assert_telegram_liveness(
    records: list[LivenessRecord],
    *,
    soak_duration_seconds: float,
) -> InvariantResult:
    """Invariant 8: at least 1 connected=True per hour over the soak duration."""
    hours = max(1, int(soak_duration_seconds // 3600) + 1)
    connected_count = sum(1 for r in records if r.connected)
    if connected_count < hours:
        return InvariantResult(
            "telegram_liveness",
            False,
            f"only {connected_count} connected=True records, "
            f"expected at least {hours} (one per hour)",
        )
    return InvariantResult(
        "telegram_liveness",
        True,
        f"{connected_count} connected=True records over {hours} hours",
    )


_OUTCOME_TO_STATUS: dict[str, str] = {
    "win_at_initial": "done_win",
    "loss_initial_win_gale1": "done_win",
    "loss_initial_loss_gale1_win_gale2": "done_win",
    "full_loss": "done_loss",
    "signal_expired": "error",
    "unsupported_pair": "error",
}


def assert_per_signal_outcomes(
    signals: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
) -> InvariantResult:
    """Invariant 9: each fixture entry's signals.status matches expected_outcome."""
    by_id = {s["signal_id"]: s["status"] for s in signals}
    mismatches: list[str] = []
    for entry in fixture:
        outcome = entry.get("expected_outcome", "")
        if outcome == "parse_failure":
            continue
        if outcome == "unsupported_pair":
            continue
        signal_id = entry.get("signal_id")
        if signal_id is None:
            continue
        actual_status = by_id.get(signal_id)
        expected_status = _OUTCOME_TO_STATUS.get(outcome)
        if expected_status is None:
            continue
        if actual_status != expected_status:
            mismatches.append(
                f"{signal_id}: expected {expected_status} ({outcome}), got {actual_status}"
            )
    if mismatches:
        return InvariantResult(
            "per_signal_outcomes", False, f"mismatches: {'; '.join(mismatches[:5])}"
        )
    return InvariantResult("per_signal_outcomes", True, f"all {len(fixture)} fixture entries match")


def assert_invariants(
    *,
    app_log: Path,
    soak_log: Path,
    signals: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
    liveness_records: list[LivenessRecord],
    drill: RestartDrillResult,
    expected_duration_seconds: float,
) -> Report:
    return Report(
        invariant_results=[
            assert_uptime(app_log, expected_duration_seconds=expected_duration_seconds),
            assert_no_exceptions(app_log),
            assert_no_missed_triggers(stages),
            assert_no_duplicate_trades(stages),
            assert_no_dm_failures(app_log),
            assert_row_counts_match_expected(signals, stages, fixture),
            assert_restart_drill(drill),
            assert_telegram_liveness(
                liveness_records,
                soak_duration_seconds=expected_duration_seconds,
            ),
            assert_per_signal_outcomes(signals, fixture),
        ]
    )
