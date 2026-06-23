# Graph Report - .  (2026-06-19)

## Corpus Check
- Corpus is ~37,864 words - fits in a single context window. You may not need a graph.

## Summary
- 256 nodes · 386 edges · 23 communities detected
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 51 edges (avg confidence: 0.62)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `OlympTradeClient` - 36 edges
2. `PRD v0.7 â€” Telegram OlympTrade Signal Copier` - 23 edges
3. `TradingPanel` - 21 edges
4. `Connection` - 14 edges
5. `OlympTradeClient` - 12 edges
6. `OlympTradeClient Core WebSocket Client` - 11 edges
7. `run_client entrypoint with callbacks` - 10 edges
8. `olymptrade_ws â€” Reverse-Engineered WebSocket Broker Client` - 10 edges
9. `BalanceAPI Class` - 9 edges
10. `initializeDocumentationFeatures()` - 8 edges

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
- **OlympTrade WebSocket Trading Stack** — core_client_OlympTradeClient, core_client_WebSocketProtocol, core_client_EventDispatch, api_balance_BalanceAPI, api_market_MarketAPI, api_trade_TradeAPI, api_utils_TimeUtils [EXTRACTED 0.95]
- **End-to-End Demo Trading Flow** — simple_bot_SimpleTradingBot, core_client_OlympTradeClient, api_balance_BalanceAPI, api_market_MarketAPI, api_trade_TradeAPI, core_client_CandleStrategy [EXTRACTED 0.90]
- **Event Callback Architecture (register_callback + dispatch)** — core_client_OlympTradeClient, core_client_EventDispatch, olymptrade_ws_main_CLIDemo, api_market_MarketAPI [EXTRACTED 0.90]
- **Documentation Site Interactivity Layer** — main_MainJS, animations_AnimationsJS, docs_DocsJS, trading_panel_TradingPanelJS [INFERRED 0.85]
- **Session Bootstrap (connect -> subscribe -> ping -> account info)** — core_client_InitializeSession, core_client_OlympTradeClient, test_api_methods_APIMethodsTest [EXTRACTED 0.85]
- **WebSocket message protocol (envelope {t,e,d,uuid} over JSON list)** — protocol_format_message, protocol_parse_message, client_send_request, client_dispatch_message, client_process_messages, event_codes_constants [EXTRACTED 1.00]
- **Event callback dispatch (event_code -> async callbacks)** — client_register_callback, client_dispatch_message, on_tick_callback, on_balance_update_callback, on_trade_update_callback [EXTRACTED 1.00]
- **Dual olymptrade_ws package (OlympTradeAPI/ vs src/) - near identical core/protocol/parameters files** — olympapi_connection_Connection, olympapi_protocol_module, olympapi_parameters_module, src_connection_Connection, src_protocol_module, src_parameters_module [INFERRED 0.95]
- **Client lifecycle (start -> initialize_session -> message processing -> stop)** — src_client_OlympTradeClient, src_connection_Connection, client_initialize_session, client_process_messages, client_ping_loop [EXTRACTED 1.00]
- **Balance retrieval flow (subscribe -> server push e:55 -> store -> get)** — balance_subscribe_balance_updates, client_send_request, client_dispatch_message, balance_get_balance, client_wait_for_balance [EXTRACTED 1.00]
- **Trade lifecycle (place -> accepted e:22 -> interim e:21 -> closed e:26)** — trade_place_trade, client_send_request, on_trade_update_callback, trade_results_dict, event_codes_constants [EXTRACTED 1.00]
- **Market ticks flow (subscribe e:12,e:280 -> tick push e:1)** — market_subscribe_ticks, client_send_request, client_dispatch_message, on_tick_callback, event_codes_constants [EXTRACTED 1.00]
- **API modules facade composed by OlympTradeClient** — src_client_OlympTradeClient, src_balance_BalanceAPI, src_market_MarketAPI, src_trade_TradeAPI [EXTRACTED 1.00]
- **signal_copier M0 scaffold (not implemented)** — src_signal_copier_main, src_signal_copier_init, tests_test_main [EXTRACTED 1.00]
- **M0 â€” Repo Scaffold (pyproject.toml, uv.lock, Dockerfile, railway.toml, .env.example, .pre-commit-config.yaml, .python-version, stub __main__.py)** — m0_plan, m0_design_spec, concept_uv_tooling, concept_ruff_mypy, concept_console_script [EXTRACTED 0.95]
- **Vendoring Workflow â€” Chipa/MIT olymptrade_ws copied to src/olymptrade_ws/ (excludes simple_bot.py, test files, root README/LICENSE; preserves package name for internal absolute imports)** — vendored_md, concept_olymptrade_ws, concept_chipa_upstream, concept_mit_license [EXTRACTED 0.95]
- **Gale Cascade State Machine (initial â†’ gale1 â†’ gale2, $2 â†’ $4 â†’ $8, strict time-window, signal_expired on miss)** — concept_state_machine, concept_martingale_strategy, concept_strict_time_window, concept_signal_dataclass [EXTRACTED 0.95]
- **Broker Protocol Layer (Broker Protocol + OlympTradeBroker + DryRunBroker)** —  [EXTRACTED 0.90]
- **Telegram Listener Chain (Telethon user-account â†’ anchored regex parser â†’ Signal dataclass â†’ asyncio.Queue)** — concept_telethon, concept_signal_parser, concept_signal_dataclass, tool_idea_doc [EXTRACTED 0.90]
- **Persistence Layer (PostgreSQL via asyncpg â€” signals / stages / daily_summary, idempotent CREATE TABLE IF NOT EXISTS)** — concept_postgres_asyncpg, concept_railway_hosting, concept_state_machine [EXTRACTED 0.90]
- **OlympTrade WS Frame Set (e:12, e:55, e:75, e:90, e:98, e:1068, e:1097, e:110, e:111, e:280, e:241, e:230/231, e:2223/2301)** — message_logbook, concept_websocket_events, concept_olymptrade_ws [EXTRACTED 0.95]
- **Safety Guardrails (demo-only hard guardrail + optional daily limits + dry-run default)** — concept_demo_only_guardrail, concept_daily_limits, concept_dry_run_broker [EXTRACTED 0.95]
- **Tool Origin Chain (analyst narrative â†’ PRD v0.7 â†’ M0 design â†’ M0 plan)** — tool_idea_doc, prd_v0_7, m0_design_spec, m0_plan [EXTRACTED 1.00]

## Communities

### Community 0 - "WebSocket Message Dispatch"
Cohesion: 0.08
Nodes (40): timestamp_to_datetime, BalanceAPI.get_balance, BalanceAPI.subscribe_balance_updates, client._dispatch_message, client.initialize_session, client._ping_loop, client._process_messages, client.register_callback (+32 more)

### Community 1 - "OlympTradeClient Core"
Cohesion: 0.07
Nodes (16): OlympTradeClient, Sends a request to the WebSocket server and optionally waits for a response., Sends the required subscription, ping, and account info requests after connectin, Waits until a balance update is received or timeout is reached.         Returns, MarketAPI, # TODO: Validate candle format [{p, t, open, low, high, close}, ...], Requests current profitability for assets (Event 182)., Selects an asset, potentially retrieving strike/payout info (Events 95, 80). (+8 more)

### Community 2 - "Trading Panel UI"
Cohesion: 0.14
Nodes (3): placeTrade(), showNotification(), TradingPanel

### Community 3 - "Documentation Site Features"
Cohesion: 0.14
Nodes (16): addLineNumbers(), adjustLayout(), createMobileSidebarToggle(), displaySearchResults(), getToastIcon(), initializeDocumentationFeatures(), performSearch(), setupCodeCopyButtons() (+8 more)

### Community 4 - "Safety & Tooling Concepts"
Cohesion: 0.17
Nodes (23): Console script signal-copier = signal_copier.__main__:main, Optional Daily Limits (DAILY_LOSS_LIMIT, DAILY_TRADE_LIMIT, DAILY_DRAWDOWN_PCT â€” 0 = disabled), Demo-only Hard Guardrail (OLYMP_ACCOUNT_GROUP=real refused when DRY_RUN=false), DryRunBroker (default for v1; logs intended trades, never connects), loguru (rotating logger, 10MB Ã— 5), Martingale Gale Cascade ($2 â†’ $4 â†’ $8, stop on first win or after 2nd gale), Build Milestones M0â€“M11 (M0 scaffold â†’ M11 Railway deploy), PostgreSQL via asyncpg (signals / stages / daily_summary schema) (+15 more)

### Community 5 - "Signal Copier Scaffold"
Cohesion: 0.11
Nodes (8): on_balance_update(), on_tick(), on_trade_update(), Callback for processing tick updates (Event 1)., Callback for processing balance updates (Event 55)., Callback for processing trade updates (Events 21, 22, 26)., Main function to run the client., run_client()

### Community 6 - "Client Lifecycle & Reconnection"
Cohesion: 0.16
Nodes (5): # TODO: Implement reconnection logic here if desired, Returns the last known balance dictionary received from the server (event 55)., Callback executed by Connection when the websocket closes unexpectedly., Connection, Internal method to handle cleanup and notify client on connection loss.

### Community 7 - "API Classes (Balance/Market/Trade)"
Cohesion: 0.36
Nodes (15): BalanceAPI Class, MarketAPI Class, TradeAPI Class (place_trade/place_order/get_open_trades), Timestamp Utility Functions, Candle-Based Direction Strategy (close>open => up, else down), Event Callback Dispatch Mechanism, Session Initialization Sequence, OlympTradeClient Core WebSocket Client (+7 more)

### Community 8 - "Vendored Library Concepts"
Cohesion: 0.23
Nodes (14): BalanceAPI (client.balance), Chipa (upstream author of olymptrade_ws, MIT, 2025), MarketAPI (client.market â€” candles, ticks), MIT License (Copyright (c) 2025 Chipa), OlympTradeClient (async API class), olymptrade_ws â€” Reverse-Engineered WebSocket Broker Client, Pair Mapping (EUR/JPY slash â†’ broker-internal pair via auto-discover on e:1068), TradeAPI (client.trade â€” place_order, get_open_trades) (+6 more)

### Community 9 - "Balance API Implementation"
Cohesion: 0.2
Nodes (5): BalanceAPI, Subscribes to real-time balance updates (Event 55).         The actual balance, Returns the most recently received balance information.         Requires balanc, Explicitly requests current balance state (if possible).         NOTE: The exac, Ensures session initialization, subscribes, and waits for a balance update, then

### Community 10 - "GSAP Animations"
Cohesion: 0.29
Nodes (0): 

### Community 11 - "Timestamp Utilities"
Cohesion: 0.4
Nodes (4): ms_timestamp_to_datetime(), Converts a second timestamp to a timezone-aware datetime object (UTC)., Converts a millisecond timestamp to a timezone-aware datetime object (UTC)., timestamp_to_datetime()

### Community 12 - "Basic Client Tests"
Cohesion: 0.67
Nodes (0): 

### Community 13 - "Docs Site JS Modules"
Cohesion: 1.0
Nodes (3): GSAP Animation Library Module, Documentation Page Functionality, Main Page JavaScript (Navigation, Hero Chart, Price Ticker)

### Community 14 - "Simple Trading Bot Demo"
Cohesion: 1.0
Nodes (0): 

### Community 15 - "Comprehensive API Tests"
Cohesion: 1.0
Nodes (0): 

### Community 16 - "M0 Scaffold Tests"
Cohesion: 1.0
Nodes (2): signal_copier scaffold main, test_main.py scaffold test

### Community 17 - "OlympTrade Parameters Config"
Cohesion: 1.0
Nodes (0): 

### Community 18 - "Timestamp Helpers"
Cohesion: 1.0
Nodes (1): api/utils.py timestamp helpers

### Community 19 - "Signal Copier Package Init"
Cohesion: 1.0
Nodes (1): signal_copier package __init__ (empty)

### Community 20 - "WebSocket Connection"
Cohesion: 1.0
Nodes (1): Connection class

### Community 21 - "Balance Update Callback"
Cohesion: 1.0
Nodes (1): on_balance_update callback (event 55)

### Community 22 - "Balance Request Method"
Cohesion: 1.0
Nodes (1): BalanceAPI.request_balance

## Knowledge Gaps
- **24 isolated node(s):** `Converts a millisecond timestamp to a timezone-aware datetime object (UTC).`, `Converts a second timestamp to a timezone-aware datetime object (UTC).`, `Returns the last known balance dictionary received from the server (event 55).`, `Internal method to handle cleanup and notify client on connection loss.`, `Generates a unique request identifier.` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Simple Trading Bot Demo`** (2 nodes): `simple_bot.py`, `main()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Comprehensive API Tests`** (2 nodes): `test_api_methods.py`, `test_all_methods()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `M0 Scaffold Tests`** (2 nodes): `signal_copier scaffold main`, `test_main.py scaffold test`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `OlympTrade Parameters Config`** (1 nodes): `parameters.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Timestamp Helpers`** (1 nodes): `api/utils.py timestamp helpers`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Signal Copier Package Init`** (1 nodes): `signal_copier package __init__ (empty)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `WebSocket Connection`** (1 nodes): `Connection class`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Balance Update Callback`** (1 nodes): `on_balance_update callback (event 55)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Balance Request Method`** (1 nodes): `BalanceAPI.request_balance`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OlympTradeClient` connect `OlympTradeClient Core` to `Balance API Implementation`, `Signal Copier Scaffold`, `Client Lifecycle & Reconnection`?**
  _High betweenness centrality (0.187) - this node is a cross-community bridge._
- **Why does `Connection` connect `Client Lifecycle & Reconnection` to `OlympTradeClient Core`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `OlympTradeClient` (e.g. with `Callback for processing tick updates (Event 1).` and `Callback for processing balance updates (Event 55).`) actually correct?**
  _`OlympTradeClient` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Connection` (e.g. with `OlympTradeClient` and `Callback executed by Connection when the websocket closes unexpectedly.`) actually correct?**
  _`Connection` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Converts a millisecond timestamp to a timezone-aware datetime object (UTC).`, `Converts a second timestamp to a timezone-aware datetime object (UTC).`, `Returns the last known balance dictionary received from the server (event 55).` to the rest of the system?**
  _24 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `WebSocket Message Dispatch` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `OlympTradeClient Core` be split into smaller, more focused modules?**
  _Cohesion score 0.07 - nodes in this community are weakly interconnected._