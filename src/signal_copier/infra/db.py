from __future__ import annotations

import re


def _redact_dsn(dsn: str) -> str:
    """Replace the password component of a PostgreSQL DSN with `***`.

    Accepts both URL form (postgresql://user:pass@host:port/db) and
    keyword form (host=... user=... password=...). Query string and
    keyword-form parameters are preserved.
    """
    # URL form: postgresql://user:pass@host:port/db?sslmode=require
    url_match = re.match(
        r"^([\w+.-]+://[^:]+:)([^@]+)(@.*)$",
        dsn,
    )
    if url_match is not None:
        return f"{url_match.group(1)}***{url_match.group(3)}"
    # Keyword form: password=secret ...
    return re.sub(r"(password\s*=\s*)([^\s]+)", r"\1***", dsn, flags=re.IGNORECASE)
