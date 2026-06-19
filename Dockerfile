FROM python:3.13

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# First layer: copy only what's needed for `uv sync` (cache-friendly)
COPY pyproject.toml uv.lock README.md ./
COPY src/signal_copier/__init__.py ./src/signal_copier/__init__.py
COPY src/signal_copier/__main__.py ./src/signal_copier/__main__.py

# Install runtime deps only (--no-dev skips pytest/ruff/mypy in the image)
RUN uv sync --frozen --no-dev

# Now copy the rest
COPY src/ ./src/
COPY migrations/ ./migrations/

# Run as non-root
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Use the venv's Python explicitly. The system Python doesn't have the package
# installed; the venv created by `uv sync` is at /app/.venv.
CMD ["/app/.venv/bin/python", "-m", "signal_copier"]
