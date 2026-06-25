FROM python:3.13

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# First layer: copy only what's needed for `uv sync` (cache-friendly)
COPY pyproject.toml uv.lock README.md ./
COPY src/signal_copier/__init__.py ./src/signal_copier/__init__.py
COPY src/signal_copier/__main__.py ./src/signal_copier/__main__.py

# Install runtime deps only (--no-dev skips pytest/ruff/mypy in the image).
# --no-install-project skips the editable install of this package itself —
# hatchling validates force-include paths (migrations/) at build time, but
# the migrations/ COPY happens below. We install the project separately
# after the rest of the source is in place.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the rest
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY LICENSE ./LICENSE

# Install the project itself now that force-include paths exist
RUN uv sync --frozen --no-dev

# Run as non-root
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Use the venv's Python explicitly. The system Python doesn't have the package
# installed; the venv created by `uv sync` is at /app/.venv.
CMD ["/app/.venv/bin/python", "-m", "signal_copier"]
