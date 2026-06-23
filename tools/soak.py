"""24-hour soak harness for M9 (spec §6).

Subprocess-launches `python -m signal_copier` with the soak env vars
(SOAK_REPLAY, DRY_RUN, OLYMP_ACCOUNT_GROUP=demo, etc.) and runs assertions
at the end.

CLI:
  python -m tools.soak \\
    --duration 24h \\
    --restart-at 12h \\
    --fixtures tests/fixtures/soak_recordings/soak_24h.json \\
    --env-file .env \\
    --output-dir logs/soak_<timestamp>/

The 5m smoke form:
  python -m tools.soak --duration 5m --fixtures tests/fixtures/soak_recordings/soak_short.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.soak_assertions import (
    LivenessRecord,
    Report,
    RestartDrillResult,
    assert_invariants,
)


def parse_duration(s: str) -> float:
    """Parse a duration string like '5m', '24h', '90s' into seconds."""
    s = s.strip().lower()
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


def main() -> int:
    parser = argparse.ArgumentParser(description="M9 24h soak harness")
    parser.add_argument("--duration", default="24h", help="Soak duration (e.g. 24h, 5m, 30s)")
    parser.add_argument("--restart-at", default="12h", help="When to force SIGTERM (e.g. 12h, 1m)")
    parser.add_argument(
        "--fixtures",
        required=True,
        help="Path to the JSON fixture file",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for soak logs (default: logs/soak_<timestamp>/)",
    )
    args = parser.parse_args()

    duration_s = parse_duration(args.duration)
    restart_at_s = parse_duration(args.restart_at)
    fixtures_path = Path(args.fixtures)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(f"logs/soak_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    return asyncio.run(
        _run(
            duration_s=duration_s,
            restart_at_s=restart_at_s,
            fixtures_path=fixtures_path,
            env_file=Path(args.env_file),
            output_dir=output_dir,
        )
    )


def _load_env_file(path: Path) -> dict[str, str]:
    """Read a .env file into a dict. Lines are KEY=VALUE; comments (#) ignored."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


async def _run(
    *,
    duration_s: float,
    restart_at_s: float,
    fixtures_path: Path,
    env_file: Path,
    output_dir: Path,
) -> int:
    """The async entry point. Returns 0 on pass, 1 on fail."""
    base_env = _load_env_file(env_file)
    child_env = {
        **base_env,
        "SOAK_REPLAY": str(fixtures_path.resolve()),
        "DRY_RUN": "true",
        "OLYMP_ACCOUNT_GROUP": "demo",
        "LOG_PATH": str((output_dir / "app.log").resolve()),
    }
    app_log = output_dir / "app.log"
    app_err = output_dir / "app.err"
    soak_log = output_dir / "soak.log"

    boot_unix = time.time()
    print(f"[soak] starting app subprocess at {datetime.utcnow().isoformat()}Z", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "signal_copier"],
        env=child_env,
        stdout=open(app_log, "wb"),  # noqa: SIM115 - handle must outlive subprocess
        stderr=open(app_err, "wb"),  # noqa: SIM115 - handle must outlive subprocess
    )

    await asyncio.sleep(restart_at_s)
    print(f"[soak] sending SIGTERM at {datetime.utcnow().isoformat()}Z", flush=True)
    in_flight_signal_ids = _read_in_flight_signals_from_db()  # stub for Task 15
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print("[soak] subprocess did not exit within 10s of SIGTERM; killing", flush=True)
        proc.kill()
        proc.wait(timeout=5)
    restarted_at_unix = time.time()
    print(f"[soak] subprocess exited; restarting at {datetime.utcnow().isoformat()}Z", flush=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "signal_copier"],
        env=child_env,
        stdout=open(app_log, "ab"),  # noqa: SIM115 - handle must outlive subprocess
        stderr=open(app_err, "ab"),  # noqa: SIM115 - handle must outlive subprocess
    )

    elapsed = time.time() - boot_unix
    remaining = max(0.0, duration_s - elapsed)
    await asyncio.sleep(remaining)

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    completed_within_60s = _check_cascades_completed_within_60s(
        in_flight_signal_ids, restarted_at_unix
    )
    drill = RestartDrillResult(
        restart_at_unix=boot_unix + restart_at_s,
        restarted_at_unix=restarted_at_unix,
        in_flight_signal_ids=in_flight_signal_ids,
        completed_within_60s=completed_within_60s,
    )

    signals, stages = _read_signals_stages_from_db()
    fixture = _load_fixture(fixtures_path)
    liveness_records = _read_liveness_records(output_dir)

    report: Report = assert_invariants(
        app_log=app_log,
        soak_log=soak_log,
        signals=signals,
        stages=stages,
        fixture=fixture,
        liveness_records=liveness_records,
        drill=drill,
        expected_duration_seconds=duration_s,
    )

    report_path = output_dir / "report.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    print(f"[soak] report written to {report_path}", flush=True)
    print(report.to_markdown(), flush=True)

    return 0 if report.passed else 1


def _read_in_flight_signals_from_db() -> list[str]:
    """Stub for Task 15: Task 17 replaces this with a real asyncpg query."""
    return []


def _check_cascades_completed_within_60s(
    in_flight_signal_ids: list[str], restarted_at_unix: float
) -> dict[str, bool]:
    """Stub for Task 15: Task 17 replaces this with a real asyncpg query."""
    return {sid: True for sid in in_flight_signal_ids}


def _read_signals_stages_from_db() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stub for Task 15: Task 17 replaces this with a real asyncpg query."""
    return [], []


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_liveness_records(output_dir: Path) -> list[LivenessRecord]:
    """Stub for Task 16: replaced with real JSONL liveness log reader."""
    return []


if __name__ == "__main__":
    sys.exit(main())
