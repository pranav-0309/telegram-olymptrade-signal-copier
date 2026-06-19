FROM python:3.13

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# First layer: copy only what's needed for `uv sync` (cache-friendly)
COPY pyproject.toml uv.lock ./
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

CMD ["python", "-m", "signal_copier"]
