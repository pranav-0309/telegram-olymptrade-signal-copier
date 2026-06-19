# Telegram → OlympTrade Signal Copier

Personal tool that listens to a Telegram channel for forex trading signals, parses them, and automatically copies the trades to an OlympTrade **demo** account using a martingale-style strategy ($2 → $4 → $8, stop on first profit or after 2nd gale).

> **v1 is demo-only by mandate.** The app refuses to start with real-money config. See `docs/PRD.md` for the full spec, build plan, and decisions.

## Status

Pre-implementation scaffold. Spec lives in [`docs/PRD.md`](docs/PRD.md) (v0.7) and the original idea in [`docs/tool-idea.md`](docs/tool-idea.md).

## How it works (TL;DR)

1. Connect to a personal Telegram account (Telethon, MTProto)
2. Watch one admin-only channel for signals in a strict format
3. Parse `PUT🟥` / `CALL🟩` signals with pair, trigger time, expiration
4. At the trigger HH:MM, place a CALL or PUT on OlympTrade with the configured amount
5. On loss → schedule 1st gale (2×) at trigger + 5 min
6. On loss again → schedule 2nd gale (3× stage amount = 4× initial) at trigger + 10 min
7. Stop on first win or after 2nd gale
8. DM the user at every state transition

Full details: [`docs/PRD.md`](docs/PRD.md).

## Third-party dependency — vendored

This project uses a reverse-engineered WebSocket client for the broker (originally by **Chipa, 2025, MIT-licensed**). The `olymptrade_ws/` source is **vendored** at `src/olymptrade_ws/`:

- It is **not** installed as a Python package
- It is **not** a git submodule
- It is committed in-tree so deployment is a single `COPY . .` and local patches are obvious

See [`src/olymptrade_ws/VENDORED.md`](src/olymptrade_ws/VENDORED.md) for the upstream source, license, import contract, and re-vendoring instructions.

## ⚠️ Risks

- **Telegram ToS:** uses a personal user account, not a bot. Ban risk is real and accepted by the owner.
- **OlympTrade ToS:** reverse-engineered WS protocol. Token can be revoked, protocol can change.
- **Real money:** disabled in v1. Hard guardrail in config — no bypass.

## License

Project license TBD. The vendored `olymptrade_ws/` retains its original MIT license — see [`src/olymptrade_ws/LICENSE`](src/olymptrade_ws/LICENSE).
