# Graph Report - ./docs  (2026-06-25)

## Corpus Check
- 28 files · ~172,277 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 410 nodes · 548 edges · 52 communities detected
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 66 edges (avg confidence: 0.66)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `OlympTradeClient` - 36 edges
2. `PRD v0.7 â€” Telegram OlympTrade Signal Copier` - 23 edges
3. `TradingPanel` - 21 edges
4. `Connection` - 14 edges
5. `Build Plan / Milestones (M0â€“M11)` - 13 edges
6. `OlympTradeClient` - 12 edges
7. `OlympTradeClient Core WebSocket Client` - 11 edges
8. `run_client entrypoint with callbacks` - 10 edges
9. `olymptrade_ws â€” Reverse-Engineered WebSocket Broker Client` - 10 edges
10. `BalanceAPI Class` - 9 edges

## Surprising Connections (you probably didn't know these)
- `Callback for processing tick updates (Event 1).` --uses--> `OlympTradeClient`  [INFERRED]
  src\olymptrade_ws\main.py → src\olymptrade_ws\core\client.py
- `Callback for processing balance updates (Event 55).` --uses--> `OlympTradeClient`  [INFERRED]
  src\olymptrade_ws\main.py → src\olymptrade_ws\core\client.py
- `Callback for processing trade updates (Events 21, 22, 26).` --uses--> `OlympTradeClient`  [INFERRED]
  src\olymptrade_ws\main.py → src\olymptrade_ws\core\client.py
- `Main function to run the client.` --uses--> `OlympTradeClient`  [INFERRED]
  src\olymptrade_ws\main.py → src\olymptrade_ws\core\client.py
- `Subscribes to real-time balance updates (Event 55).         The actual balance` --uses--> `OlympTradeClient`  [INFERRED]
  src\olymptrade_ws\api\balance.py → src\olymptrade_ws\core\client.py

## Hyperedges (group relationships)
- **Martingale Gale Cascade Implementation Stack** — PRD_MartingaleCascade, PRD_GaleStateMachine, 2026-06-19-m1-parser_GaleArithmetic, 2026-06-19-m2-state-machine_GaleMath, 2026-06-19-m2-state-machine_SignalState, 2026-06-19-m2-state-machine_Transition, 2026-06-21-m6-scheduler_SignalSupervisor [EXTRACTED 0.95]
- **Broker Protocol Implementation Surface** — 2026-06-20-m3-broker-protocol_BrokerProtocol, 2026-06-20-m3-broker-protocol_DryRunBroker, 2026-06-20-m3-broker-protocol_UnsupportedPairError, 2026-06-21-m8-olymptrade-broker_OlympTradeBroker, 2026-06-21-m8-olymptrade-broker_BrokerAuthError, 2026-06-23-m10-olymptrade-reconnect-supervisor_ReconnectingOlympTradeBroker [EXTRACTED 0.95]
- **Notifier Protocol Evolution (M6â†’M7â†’M10)** — 2026-06-21-m6-scheduler_NotifierProtocol, 2026-06-21-m6-scheduler_NoOpNotifier, 2026-06-21-m7-telegram-dm-notifications_NotifierExtension, 2026-06-21-m7-telegram-dm-notifications_TelegramDMNotifier, 2026-06-23-m10-olymptrade-reconnect-supervisor_ReconnectProtocol [EXTRACTED 0.90]
- **Components implementing Broker Protocol contract** — 2026-06-20-m3-broker-protocol-design_BrokerProtocol, 2026-06-20-m3-broker-protocol-design_DryRunBroker, 2026-06-21-m8-olymptrade-broker-design_OlympTradeBroker, 2026-06-23-m10-olymptrade-reconnect-supervisor-design_ReconnectingOlympTradeBroker [EXTRACTED 0.98]
- **Components implementing Notifier Protocol (FR-7.1 events)** — 2026-06-21-m6-scheduler-design_NotifierProtocol, 2026-06-21-m6-scheduler-design_NoOpNotifier, 2026-06-21-m7-telegram-dm-notifications-design_TelegramDMNotifier, 2026-06-23-m10-olymptrade-reconnect-supervisor-design_NotifierExtension [EXTRACTED 0.95]
- **v1 deployment story: scaffold -> Docker -> Railway -> CI -> license** — 2026-06-19-m0-scaffold-design_Dockerfile, 2026-06-19-m0-scaffold-design_RailwayToml, 2026-06-23-m11-railway-deployment-design_GitHubActionsWorkflow, 2026-06-23-m11-railway-deployment-design_DockerCompose, 2026-06-23-m11-railway-deployment-design_PolyFormLicense [INFERRED 0.90]

## Communities

### Community 0 - "BalanceAPI & Account Methods"
Cohesion: 0.04
Nodes (26): BalanceAPI, Subscribes to real-time balance updates (Event 55).         The actual balance, Returns the most recently received balance information.         Requires balanc, Explicitly requests current balance state (if possible).         NOTE: The exac, Ensures session initialization, subscribes, and waits for a balance update, then, OlympTradeClient, # TODO: Implement reconnection logic here if desired, Sends a request to the WebSocket server and optionally waits for a response. (+18 more)

### Community 1 - "Deployment & Milestone Infrastructure"
Cohesion: 0.07
Nodes (44): Dockerfile (python:3.13 + uv), M0 Project Scaffold, railway.toml + .dockerignore, Stub __main__.py, pyproject.toml (PEP 621 + hatchling), M1 Signal Parser, M2 State Machine + Config Layer, M3 Broker Protocol (+36 more)

### Community 2 - "Signal Copier Config & Domain Model"
Cohesion: 0.07
Nodes (43): M0 Demo-Only Guardrail reference (OLYMP_ACCOUNT_GROUP=demo), .env.example (Telegram/OlympTrade/DB/Trading/Limits env vars), M0 Project Scaffold (signal_copier package), derive_signal_id() SHA1[:12] helper for idempotency, ParsedSignal dataclass (pair, direction, trigger_hhmm, expiration_seconds, gale1/2_hhmm), Signal dataclass (PRD FR-2.5 full envelope, M5-constructed), parse_signal() pure parser (regex + BOM stripping), M2 pydantic-settings Config (13 fields + demo guardrail) (+35 more)

### Community 3 - "WebSocket Client Session"
Cohesion: 0.08
Nodes (40): timestamp_to_datetime, BalanceAPI.get_balance, BalanceAPI.subscribe_balance_updates, client._dispatch_message, client.initialize_session, client._ping_loop, client._process_messages, client.register_callback (+32 more)

### Community 4 - "Cross-Cutting Components & References"
Cohesion: 0.11
Nodes (37): BalanceAPI (client.balance), Chipa (upstream author of olymptrade_ws, MIT, 2025), Console script signal-copier = signal_copier.__main__:main, Optional Daily Limits (DAILY_LOSS_LIMIT, DAILY_TRADE_LIMIT, DAILY_DRAWDOWN_PCT â€” 0 = disabled), Demo-only Hard Guardrail (OLYMP_ACCOUNT_GROUP=real refused when DRY_RUN=false), DryRunBroker (default for v1; logs intended trades, never connects), loguru (rotating logger, 10MB Ã— 5), MarketAPI (client.market â€” candles, ticks) (+29 more)

### Community 5 - "Trading Panel UI (JS)"
Cohesion: 0.14
Nodes (3): placeTrade(), showNotification(), TradingPanel

### Community 6 - "Signal State Machine + Database"
Cohesion: 0.09
Nodes (24): derive_signal_id (SHA-1, 12-char), Config (pydantic-settings, 13 fields), domain/gale.py (amount_for_stage, compute_gale_triggers), SignalState frozen dataclass + from_signal(), Time-Window Check at FireEvent + ResultEvent(loss), transition() pure function, Signal.trigger_unix_* fields (D-5), Database class (asyncpg pool + migration) (+16 more)

### Community 7 - "Documentation Page UI (JS)"
Cohesion: 0.14
Nodes (16): addLineNumbers(), adjustLayout(), createMobileSidebarToggle(), displaySearchResults(), getToastIcon(), initializeDocumentationFeatures(), performSearch(), setupCodeCopyButtons() (+8 more)

### Community 8 - "Main Page UI (JS)"
Cohesion: 0.11
Nodes (8): on_balance_update(), on_tick(), on_trade_update(), Callback for processing tick updates (Event 1)., Callback for processing balance updates (Event 55)., Callback for processing trade updates (Events 21, 22, 26)., Main function to run the client., run_client()

### Community 9 - "OlympTradeAPI Reference Classes"
Cohesion: 0.36
Nodes (15): BalanceAPI Class, MarketAPI Class, TradeAPI Class (place_trade/place_order/get_open_trades), Timestamp Utility Functions, Candle-Based Direction Strategy (close>open => up, else down), Event Callback Dispatch Mechanism, Session Initialization Sequence, OlympTradeClient Core WebSocket Client (+7 more)

### Community 10 - "GSAP Animation Library"
Cohesion: 0.29
Nodes (0): 

### Community 11 - "Timestamp Utilities"
Cohesion: 0.4
Nodes (4): ms_timestamp_to_datetime(), Converts a second timestamp to a timezone-aware datetime object (UTC)., Converts a millisecond timestamp to a timezone-aware datetime object (UTC)., timestamp_to_datetime()

### Community 12 - "Signal Parser (Regex)"
Cohesion: 0.5
Nodes (4): Anchored Line Regex Strategy (FR-2.2), FailureReason enum, _add_minutes() with Midnight Wrap, parse_signal() + ParsedSignal

### Community 13 - "Python Tooling (Ruff/Mypy)"
Cohesion: 0.5
Nodes (4): pyproject.toml (PEP 621, uv, ruff, mypy strict), M11 .github/workflows/ci.yml (lint/format/typecheck/test/deploy jobs), M12 removed dead [[tool.mypy.overrides]] block (bare names didn't match), M12 test type annotations (229 mypy errors -> 0; tests fully strict-checked)

### Community 14 - "Scaffold Tests"
Cohesion: 0.67
Nodes (0): 

### Community 15 - "JS Module Bundles"
Cohesion: 1.0
Nodes (3): GSAP Animation Library Module, Documentation Page Functionality, Main Page JavaScript (Navigation, Hero Chart, Price Ticker)

### Community 16 - "M12 Type Cleanup"
Cohesion: 0.67
Nodes (3): GitHub Actions CI/CD (5 jobs), Mypy overrides cleanup (drop test module skip list), M12 Type & Format Cleanup

### Community 17 - "Gale Cascade Arithmetic"
Cohesion: 0.67
Nodes (3): _add_minutes() gale arithmetic with midnight wrap, amount_for_stage() (config-driven, R-2 stage amounts), Gale Cascade (initial $2, 1st gale $4, 2nd gale $8)

### Community 18 - "Simple Bot Demo"
Cohesion: 1.0
Nodes (0): 

### Community 19 - "API Method Tests"
Cohesion: 1.0
Nodes (0): 

### Community 20 - "Signal Copier Scaffold"
Cohesion: 1.0
Nodes (2): signal_copier scaffold main, test_main.py scaffold test

### Community 21 - "Product Requirements (PRD + Idea)"
Cohesion: 1.0
Nodes (2): PRD â€” Signal Copier, Telegram â†’ OlympTrade Signal Copier

### Community 22 - "Telegram Auth"
Cohesion: 1.0
Nodes (2): telegram.auth (interactive StringSession), telegram.auth enhancements (Railway guard, get_me verify)

### Community 23 - "Daily Trading Limits"
Cohesion: 1.0
Nodes (2): ErrorReason 'daily_limit_hit' (M6 D-2), Daily Loss/Trade/Drawdown Limits

### Community 24 - "Database Migrations & Compose"
Cohesion: 1.0
Nodes (2): M4 Database (asyncpg pool + migration runner), M11 docker-compose.yml (postgres:16-alpine local dev)

### Community 25 - "parameters.py"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "api/utils.py Timestamp Helpers"
Cohesion: 1.0
Nodes (1): api/utils.py timestamp helpers

### Community 27 - "signal_copier __init__"
Cohesion: 1.0
Nodes (1): signal_copier package __init__ (empty)

### Community 28 - "Connection class"
Cohesion: 1.0
Nodes (1): Connection class

### Community 29 - "on_balance_update callback"
Cohesion: 1.0
Nodes (1): on_balance_update callback (event 55)

### Community 30 - "BalanceAPI.request_balance"
Cohesion: 1.0
Nodes (1): BalanceAPI.request_balance

### Community 31 - ".pre-commit-config.yaml"
Cohesion: 1.0
Nodes (1): .pre-commit-config.yaml (ruff)

### Community 32 - "Testcontainers Postgres"
Cohesion: 1.0
Nodes (1): testcontainers[postgresql] for integration tests

### Community 33 - "Telegram→OlympTrade Tool Concept"
Cohesion: 1.0
Nodes (1): Telegram to OlympTrade Signal Copier (Tool Concept)

### Community 34 - "Dockerfile (uv)"
Cohesion: 1.0
Nodes (1): Dockerfile (python:3.13 + uv sync frozen)

### Community 35 - "railway.toml"
Cohesion: 1.0
Nodes (1): railway.toml (DOCKERFILE builder, ON_FAILURE restart)

### Community 36 - "ParseFailure dataclass"
Cohesion: 1.0
Nodes (1): ParseFailure dataclass (FailureReason + raw_text)

### Community 37 - "FailureReason enum"
Cohesion: 1.0
Nodes (1): FailureReason enum (7 values: missing/bad/expiration)

### Community 38 - "SignalState frozen dataclass"
Cohesion: 1.0
Nodes (1): SignalState frozen dataclass (SignalState.from_signal)

### Community 39 - "FireEvent dataclass"
Cohesion: 1.0
Nodes (1): FireEvent dataclass (now_unix for trigger fire)

### Community 40 - "ResultEvent dataclass"
Cohesion: 1.0
Nodes (1): ResultEvent dataclass (result + now_unix for stage outcome)

### Community 41 - "TransitionResult dataclass"
Cohesion: 1.0
Nodes (1): TransitionResult dataclass (success + new_state + reason)

### Community 42 - "compute_gale_triggers()"
Cohesion: 1.0
Nodes (1): compute_gale_triggers() (gale1/gale2 from initial+expiration)

### Community 43 - "SignalRow frozen dataclass"
Cohesion: 1.0
Nodes (1): SignalRow frozen dataclass (signals table mapper)

### Community 44 - "StageRow frozen dataclass"
Cohesion: 1.0
Nodes (1): StageRow frozen dataclass (stages table mapper)

### Community 45 - "migrations/001_initial.sql"
Cohesion: 1.0
Nodes (1): migrations/001_initial.sql (DDL for signals/stages/daily_summary)

### Community 46 - "StageAlreadyExistsError"
Cohesion: 1.0
Nodes (1): StageAlreadyExistsError (programmer bug signal)

### Community 47 - "DatabaseConnectionError"
Cohesion: 1.0
Nodes (1): DatabaseConnectionError (with DSN redaction)

### Community 48 - "TelegramConfigError"
Cohesion: 1.0
Nodes (1): TelegramConfigError (misconfiguration at startup)

### Community 49 - "NoOpNotifier"
Cohesion: 1.0
Nodes (1): NoOpNotifier (logs every event at INFO, default for v1)

### Community 50 - "M9 Soak Assertions"
Cohesion: 1.0
Nodes (1): M9 tools/soak_assertions.py (9 pass/fail invariants)

### Community 51 - "M11 PolyForm Strict License"
Cohesion: 1.0
Nodes (1): M11 PolyForm Strict 1.0.0 LICENSE

## Knowledge Gaps
- **96 isolated node(s):** `Converts a millisecond timestamp to a timezone-aware datetime object (UTC).`, `Converts a second timestamp to a timezone-aware datetime object (UTC).`, `Returns the last known balance dictionary received from the server (event 55).`, `Internal method to handle cleanup and notify client on connection loss.`, `Generates a unique request identifier.` (+91 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Simple Bot Demo`** (2 nodes): `simple_bot.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `API Method Tests`** (2 nodes): `test_api_methods.py`, `test_all_methods()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Signal Copier Scaffold`** (2 nodes): `signal_copier scaffold main`, `test_main.py scaffold test`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Product Requirements (PRD + Idea)`** (2 nodes): `PRD â€” Signal Copier`, `Telegram â†’ OlympTrade Signal Copier`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Telegram Auth`** (2 nodes): `telegram.auth (interactive StringSession)`, `telegram.auth enhancements (Railway guard, get_me verify)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Daily Trading Limits`** (2 nodes): `ErrorReason 'daily_limit_hit' (M6 D-2)`, `Daily Loss/Trade/Drawdown Limits`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Database Migrations & Compose`** (2 nodes): `M4 Database (asyncpg pool + migration runner)`, `M11 docker-compose.yml (postgres:16-alpine local dev)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `parameters.py`** (1 nodes): `parameters.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `api/utils.py Timestamp Helpers`** (1 nodes): `api/utils.py timestamp helpers`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `signal_copier __init__`** (1 nodes): `signal_copier package __init__ (empty)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Connection class`** (1 nodes): `Connection class`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `on_balance_update callback`** (1 nodes): `on_balance_update callback (event 55)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `BalanceAPI.request_balance`** (1 nodes): `BalanceAPI.request_balance`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `.pre-commit-config.yaml`** (1 nodes): `.pre-commit-config.yaml (ruff)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Testcontainers Postgres`** (1 nodes): `testcontainers[postgresql] for integration tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Telegram→OlympTrade Tool Concept`** (1 nodes): `Telegram to OlympTrade Signal Copier (Tool Concept)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dockerfile (uv)`** (1 nodes): `Dockerfile (python:3.13 + uv sync frozen)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `railway.toml`** (1 nodes): `railway.toml (DOCKERFILE builder, ON_FAILURE restart)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `ParseFailure dataclass`** (1 nodes): `ParseFailure dataclass (FailureReason + raw_text)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `FailureReason enum`** (1 nodes): `FailureReason enum (7 values: missing/bad/expiration)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `SignalState frozen dataclass`** (1 nodes): `SignalState frozen dataclass (SignalState.from_signal)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `FireEvent dataclass`** (1 nodes): `FireEvent dataclass (now_unix for trigger fire)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `ResultEvent dataclass`** (1 nodes): `ResultEvent dataclass (result + now_unix for stage outcome)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `TransitionResult dataclass`** (1 nodes): `TransitionResult dataclass (success + new_state + reason)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `compute_gale_triggers()`** (1 nodes): `compute_gale_triggers() (gale1/gale2 from initial+expiration)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `SignalRow frozen dataclass`** (1 nodes): `SignalRow frozen dataclass (signals table mapper)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `StageRow frozen dataclass`** (1 nodes): `StageRow frozen dataclass (stages table mapper)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `migrations/001_initial.sql`** (1 nodes): `migrations/001_initial.sql (DDL for signals/stages/daily_summary)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `StageAlreadyExistsError`** (1 nodes): `StageAlreadyExistsError (programmer bug signal)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `DatabaseConnectionError`** (1 nodes): `DatabaseConnectionError (with DSN redaction)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `TelegramConfigError`** (1 nodes): `TelegramConfigError (misconfiguration at startup)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `NoOpNotifier`** (1 nodes): `NoOpNotifier (logs every event at INFO, default for v1)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `M9 Soak Assertions`** (1 nodes): `M9 tools/soak_assertions.py (9 pass/fail invariants)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `M11 PolyForm Strict License`** (1 nodes): `M11 PolyForm Strict 1.0.0 LICENSE`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OlympTradeClient` connect `BalanceAPI & Account Methods` to `Main Page UI (JS)`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `OlympTradeClient` (e.g. with `Callback for processing tick updates (Event 1).` and `Callback for processing balance updates (Event 55).`) actually correct?**
  _`OlympTradeClient` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Connection` (e.g. with `OlympTradeClient` and `Callback executed by Connection when the websocket closes unexpectedly.`) actually correct?**
  _`Connection` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Converts a millisecond timestamp to a timezone-aware datetime object (UTC).`, `Converts a second timestamp to a timezone-aware datetime object (UTC).`, `Returns the last known balance dictionary received from the server (event 55).` to the rest of the system?**
  _96 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `BalanceAPI & Account Methods` be split into smaller, more focused modules?**
  _Cohesion score 0.04 - nodes in this community are weakly interconnected._
- **Should `Deployment & Milestone Infrastructure` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._
- **Should `Signal Copier Config & Domain Model` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._