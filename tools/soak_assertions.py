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
    return InvariantResult("uptime", True, "stub")


def assert_no_exceptions(app_log: Path) -> InvariantResult:
    return InvariantResult("no_exceptions", True, "stub")


def assert_no_missed_triggers(
    stages: list[dict[str, Any]],
    *,
    tolerance_seconds: float = 2.0,
) -> InvariantResult:
    return InvariantResult("no_missed_triggers", True, "stub")


def assert_no_duplicate_trades(stages: list[dict[str, Any]]) -> InvariantResult:
    return InvariantResult("no_duplicate_trades", True, "stub")


def assert_no_dm_failures(app_log: Path) -> InvariantResult:
    return InvariantResult("no_dm_failures", True, "stub")


def assert_row_counts_match_expected(
    signals: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
) -> InvariantResult:
    return InvariantResult("row_counts", True, "stub")


def assert_restart_drill(drill: RestartDrillResult) -> InvariantResult:
    return InvariantResult("restart_drill", True, "stub")


def assert_telegram_liveness(
    records: list[LivenessRecord],
    *,
    soak_duration_seconds: float,
) -> InvariantResult:
    return InvariantResult("telegram_liveness", True, "stub")


def assert_per_signal_outcomes(
    signals: list[dict[str, Any]],
    fixture: list[dict[str, Any]],
) -> InvariantResult:
    return InvariantResult("per_signal_outcomes", True, "stub")


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
