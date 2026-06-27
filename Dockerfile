FROM python:3.13

# Cache-bust 2026-06-28 00:25: force fresh rebuild for pair_unavailable
# translation (PairUnavailableError → UnsupportedPairError). Without this,
# Railway may serve a stale image that still raises BrokerAuthError on
# weekend forex signals.
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# First layer: copy only what's needed for `uv sync` (cache-friendly)
COPY pyproject.toml uv.lock README.md ./
COPY src/signal_copier/__init__.py ./src/signal_copier/__init__.py
COPY src/signal_copier/__main__.py ./src/signal_copier/__main__.py

# Install runtime deps only (--no-dev skips pytest/ruff/mypy in the image).
# --no-install-project skips the editable install of this package itself —
# we install the project in a second pass below so the cache layer doesn't
# depend on the full src/ tree being copied yet.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the rest (migrations/ now lives inside src/signal_copier/)
COPY src/ ./src/
COPY LICENSE ./LICENSE

# Install the project itself now that the full source tree is in place.
RUN uv sync --frozen --no-dev

# Run as non-root
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Use the venv's Python explicitly. The system Python doesn't have the package
# installed; the venv created by `uv sync` is at /app/.venv.
CMD ["/app/.venv/bin/python", "-m", "signal_copier"]
