# Telegram → OlympTrade Signal Copier

Personal tool that listens to a Telegram channel for forex trading signals, parses them, and automatically copies the trades to an OlympTrade **demo** account using a martingale-style strategy ($2 → $4 → $8, stop on first profit or after 2nd gale).

> **v1 is demo-only by mandate.** The app refuses to start with real-money config. See `docs/PRD.md` for the full spec, build plan, and decisions.

## Status

Pre-implementation scaffold. Spec lives in [`docs/PRD.md`](docs/PRD.md) (v0.6) and the original idea in [`docs/tool-idea.md`](docs/tool-idea.md).

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

## ⚠️ Third-party dependency (not vendored)

This project uses the **[`OlympTradeAPI`](https://github.com/)** reverse-engineered WebSocket client for broker communication. That library is **not** included in this repo and **not** installed as a Python package — you bring the source yourself and we wire it in as code.

Why excluded:
- Reverse-engineered protocol — redistribution of a third party's code muddies licensing
- Per the project decision: "using the code only, not any python package"

### Setup (local development)

```bash
# 1. Clone this repo
git clone https://github.com/<you>/telegram-olymptrade-signal-copier.git
cd telegram-olymptrade-signal-copier

# 2. Place the OlympTradeAPI source alongside this project
#    Expected path: ../OlympTradeAPI/olymptrade_ws/
git clone <OlympTradeAPI-source-url> ../OlympTradeAPI
```

The exact import path and integration contract will be pinned in `pyproject.toml` and the broker adapter once M0/M8 lands — see the PRD's build plan.

## ⚠️ Risks

- **Telegram ToS:** uses a personal user account, not a bot. Ban risk is real and accepted by the owner.
- **OlympTrade ToS:** reverse-engineered WS protocol. Token can be revoked, protocol can change.
- **Real money:** disabled in v1. Hard guardrail in config — no bypass.

## License

TBD (project license to be chosen before any public release).
