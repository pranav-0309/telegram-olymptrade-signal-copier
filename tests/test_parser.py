from __future__ import annotations

ALLOWED = frozenset({300})  # 5-minute only (v1 default per PRD §8)

VALID_MESSAGE = (
    "💰5-minute expiration\n"
    "EUR/JPY;10:20;PUT🟥\n"
    "🕛TIME UNTIL 10:25\n"
    "1st GALE -> TIME UNTIL 10:30\n"
    "2nd GALE - TIME UNTIL 10:35\n"
)
