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
import contextlib
import json
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from tools.soak_assertions import (
    LivenessRecord,
    Report,
    RestartDrillResult,
    assert_invariants,
)

LIVENESS_INTERVAL_GETME_SECONDS: float = 30 * 60
LIVENESS_INTERVAL_ISCONNECTED_SECONDS: float = 60


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
    cancel_liveness = asyncio.Event()
    liveness_task = asyncio.create_task(
        _liveness_probe(
            env=child_env, output_dir=output_dir, duration_s=duration_s, cancel=cancel_liveness
        )  # noqa: E501
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "signal_copier"],
        env=child_env,
        stdout=open(app_log, "wb"),  # noqa: SIM115 - handle must outlive subprocess
        stderr=open(app_err, "wb"),  # noqa: SIM115 - handle must outlive subprocess
    )

    await asyncio.sleep(restart_at_s)
    print(f"[soak] sending SIGTERM at {datetime.utcnow().isoformat()}Z", flush=True)
    in_flight_signal_ids = _read_in_flight_signals_from_db(env=child_env)
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
        in_flight_signal_ids, restarted_at_unix, env=child_env
    )
    drill = RestartDrillResult(
        restart_at_unix=boot_unix + restart_at_s,
        restarted_at_unix=restarted_at_unix,
        in_flight_signal_ids=in_flight_signal_ids,
        completed_within_60s=completed_within_60s,
    )

    cancel_liveness.set()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await liveness_task

    signals, stages = _read_signals_stages_from_db(env=child_env)
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


def _read_in_flight_signals_from_db(env: dict[str, str]) -> list[str]:
    """Query the configured PG for signals in placed_* states."""
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        return []
    return asyncio.run(_async_read_in_flight(dsn))


async def _async_read_in_flight(dsn: str) -> list[str]:
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT signal_id FROM signals "
            "WHERE status IN ('placed_initial','placed_gale1','placed_gale2')"
        )
        return [r["signal_id"] for r in rows]
    finally:
        await conn.close()


def _check_cascades_completed_within_60s(
    in_flight_signal_ids: list[str], restarted_at_unix: float, env: dict[str, str]
) -> dict[str, bool]:
    if not in_flight_signal_ids:
        return {}
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        return {sid: False for sid in in_flight_signal_ids}
    return asyncio.run(_async_check_completion(dsn, in_flight_signal_ids, restarted_at_unix))


async def _async_check_completion(
    dsn: str, signal_ids: list[str], restarted_at_unix: float
) -> dict[str, bool]:
    import asyncpg

    deadline = restarted_at_unix + 60.0
    result: dict[str, bool] = {}
    conn = await asyncpg.connect(dsn)
    try:
        for sid in signal_ids:
            terminal = False
            while time.time() < deadline:
                row = await conn.fetchrow(
                    "SELECT status, updated_at_unix FROM signals WHERE signal_id = $1",
                    sid,
                )
                if row is None:
                    terminal = True
                    break
                if row["status"] in {"done_win", "done_loss", "done_tie", "error"}:
                    terminal = True
                    break
                await asyncio.sleep(1)
            result[sid] = terminal
    finally:
        await conn.close()
    return result


def _read_signals_stages_from_db(
    env: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:  # noqa: E501
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        return [], []
    return asyncio.run(_async_read_signals_stages(dsn))


async def _async_read_signals_stages(dsn: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        signal_rows = await conn.fetch("SELECT * FROM signals")
        stage_rows = await conn.fetch("SELECT * FROM stages")
        return ([dict(r) for r in signal_rows], [dict(r) for r in stage_rows])
    finally:
        await conn.close()


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], data)


def _read_liveness_records(output_dir: Path) -> list[LivenessRecord]:
    """Read the liveness JSONL log into LivenessRecord objects."""
    path = output_dir / "liveness.jsonl"
    if not path.exists():
        return []
    records: list[LivenessRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            records.append(
                LivenessRecord(timestamp=float(obj["timestamp"]), connected=bool(obj["connected"]))
            )  # noqa: E501
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return records


async def _liveness_probe(
    *,
    env: dict[str, str],
    output_dir: Path,
    duration_s: float,
    cancel: asyncio.Event,
) -> None:
    """Owns a separate Telethon client; pings get_me() every 30 min, is_connected every 1 min."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    liveness_path = output_dir / "liveness.jsonl"
    api_id = int(env.get("TELEGRAM_API_ID", "0"))
    api_hash = env.get("TELEGRAM_API_HASH", "")
    session_string = env.get("TELEGRAM_SESSION_STRING", "")

    if not (api_id and api_hash and session_string):
        with liveness_path.open("w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "connected": False,
                        "skipped": True,
                        "reason": "missing TELEGRAM_API_ID/HASH/SESSION_STRING",
                    }
                )
                + "\n"
            )
        return

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        with liveness_path.open("w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "connected": False,
                        "skipped": True,
                        "reason": "session not authorized",
                    }
                )
                + "\n"
            )
        await client.disconnect()
        return

    start = time.time()
    with liveness_path.open("w", encoding="utf-8") as f:
        last_getme = 0.0
        last_isconnected = 0.0
        while not cancel.is_set() and (time.time() - start) < duration_s:
            now = time.time()
            record: dict[str, Any]
            if now - last_getme >= LIVENESS_INTERVAL_GETME_SECONDS:
                try:
                    await client.get_me()
                    record = {"timestamp": now, "connected": True, "method": "get_me"}
                    last_getme = now
                except Exception as exc:
                    record = {
                        "timestamp": now,
                        "connected": False,
                        "method": "get_me",
                        "error": str(exc),
                    }  # noqa: E501
            elif now - last_isconnected >= LIVENESS_INTERVAL_ISCONNECTED_SECONDS:
                try:
                    connected = bool(await client.is_connected())
                    record = {"timestamp": now, "connected": connected, "method": "is_connected"}
                    last_isconnected = now
                except Exception as exc:
                    record = {
                        "timestamp": now,
                        "connected": False,
                        "method": "is_connected",
                        "error": str(exc),
                    }  # noqa: E501
            else:
                await asyncio.sleep(5)
                continue
            f.write(json.dumps(record) + "\n")
            f.flush()
            await asyncio.sleep(5)
    await client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
