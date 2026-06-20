# Empty. Callers import from submodules:
#   from signal_copier.telegram.client import TelegramClient, TelegramConfigError
#   from signal_copier.telegram.listener import Listener
#   from signal_copier.telegram.auth import main
#
# No top-level re-exports — the package is a namespace, not a facade.
# Matches the M4 convention in src/signal_copier/infra/__init__.py.
