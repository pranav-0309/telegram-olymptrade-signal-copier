from __future__ import annotations

import asyncio
import sys
from typing import cast

from pydantic import ValidationError
from telethon import TelegramClient as _TelethonClient
from telethon.sessions import StringSession

from signal_copier.config import Config
from signal_copier.telegram.client import TelegramConfigError


def _is_running_on_railway() -> bool:
    """Return True if this process is running on Railway.app.

    Detected by the presence of either RAILWAY_ENVIRONMENT or
    RAILWAY_PROJECT_ID env vars, which Railway always injects into
    its containers.
    """
    import os

    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))


# Interactive auth has no bound — the user may take minutes to enter
# the SMS code. We use a generous default.
_AUTH_TIMEOUT_SECONDS: int = 300


def _read_creds() -> tuple[int, str, str]:
    """Read API_ID / API_HASH / PHONE from .env via the Config validator.

    Re-uses M2's pydantic validators. On validation failure, prints a
    friendly error to stderr and raises so main() can return 2.
    """
    config = Config()
    return (
        config.telegram_api_id,
        config.telegram_api_hash,
        config.telegram_phone,
    )


async def _do_auth_and_verify(api_id: int, api_hash: str, phone: str) -> tuple[str, object]:
    """Run Telethon interactive auth + verify session, all in one event loop.

    Telethon clients are bound to the event loop they were created in;
    calling get_me() on a client from a different loop will fail. So we
    do everything in one loop: connect → interactive auth → save session
    → verify via get_me() → disconnect. Returns (session_string, user).
    """
    client = _TelethonClient(StringSession(), api_id, api_hash)
    try:
        await client.start(phone=phone)  # interactive: prompts for code + 2FA
        session_str = cast(str, client.session.save())
        user = await client.get_me()  # verify the session works
        return session_str, user
    finally:
        await client.disconnect()


def main() -> int:
    """Entry point for `python -m signal_copier.telegram.auth`.

    Reads credentials from .env, refuses to run on Railway, runs the
    Telethon interactive auth flow, verifies the session via get_me(),
    and prints the resulting StringSession to stdout with a rich banner.

    Exits 0 on success, 1 on auth/verification failure, 2 on config or
    Railway-guard error.
    """
    try:
        api_id, api_hash, phone = _read_creds()
    except (ValidationError, ValueError) as exc:
        sys.stderr.write(
            f"❌ Config validation failed; check API_ID / API_HASH / PHONE in .env:\n{exc}\n"
        )
        return 2
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2

    if api_id == 0 or not api_hash or not phone:
        sys.stderr.write(
            "❌ TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE must be set in .env\n"
            "   Get API_ID and API_HASH from https://my.telegram.org\n"
        )
        return 2

    if _is_running_on_railway():
        sys.stderr.write(
            "❌ Do not run this on Railway. Run `python -m signal_copier.telegram.auth` "
            "locally and paste the printed TELEGRAM_SESSION_STRING into your Railway "
            "Variables.\n"
        )
        return 2

    try:
        session_str, user = asyncio.run(
            asyncio.wait_for(
                _do_auth_and_verify(api_id, api_hash, phone),
                timeout=_AUTH_TIMEOUT_SECONDS,
            )
        )
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except TimeoutError:
        sys.stderr.write(
            f"❌ Auth timed out after {_AUTH_TIMEOUT_SECONDS}s; run again and "
            "respond to the prompts more quickly.\n"
        )
        return 1
    except Exception as exc:
        sys.stderr.write(f"❌ Telegram auth or verify failed: {type(exc).__name__}: {exc}\n")
        return 1

    full_name = " ".join(
        filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])
    ).strip()
    user_username = getattr(user, "username", None)
    username = f"@{user_username}" if user_username else "(no username)"
    user_id = getattr(user, "id", "?")

    print("=" * 70)
    print(f"Authenticated as: {full_name or '(no name)'} ({username})")
    print(f"User ID: {user_id}")
    print("=" * 70)
    print("Set this as TELEGRAM_SESSION_STRING in your Railway Variables:")
    print()
    print(f"TELEGRAM_SESSION_STRING={session_str}")
    print()
    print("⚠️  Treat the session string like a password. Anyone with it can read")
    print("   and send messages from your Telegram account.")
    print()
    print("Then redeploy: git commit --allow-empty -m 'rotate session' && git push")
    print("Or trigger a manual redeploy from the Railway dashboard.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
