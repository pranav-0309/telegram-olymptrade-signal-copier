"""Tools package: soak harness, soak assertions, and other runnable scripts.

This package is NOT part of the running app; it imports the app for
assertions only. Soak-only dependencies (subprocess management, signal
handlers) are kept out of the production install.
"""
