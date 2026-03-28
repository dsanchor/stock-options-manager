# Rusty — Phase 2 Scheduler + Agent Runner Refactor

## Decision
Completed the CosmosDB migration of scheduler, agent runner, and all four agent wrappers. File-based symbol discovery and JSONL logging are fully replaced by CosmosDB queries and writes.

## Changes Made

### `src/agent_runner.py`
- Removed `_read_symbols()`, `_read_positions()`, and file-based logger imports
- Added `run_symbol_agent(symbol, exchange, agent_type, cosmos, context_provider, fetcher)` — single-symbol execution
- Added `run_position_monitor(symbol, exchange, position, agent_type, cosmos, context_provider, fetcher)` — single-position execution
- Context injection via `ContextProvider.get_context()` (last N decisions with embedded signal status)
- Decision persistence via `cosmos.write_decision()` + `cosmos.write_signal()` when actionable
- `is_signal=True` set on decision payload when a signal is written

### `src/main.py`
- Scheduler initializes `CosmosDBService` and `ContextProvider` during `setup()`
- `_run_all_agents_async()` passes cosmos + context_provider to all four agent wrappers
- Removed all file-path config lookups

### Agent Wrappers (4 files)
- `covered_call_agent.py` / `cash_secured_put_agent.py`: Query `cosmos.get_covered_call_symbols()` / `get_cash_secured_put_symbols()`, iterate, call `runner.run_symbol_agent()`
- `open_call_monitor_agent.py` / `open_put_monitor_agent.py`: Query `cosmos.get_symbols_with_active_positions("call"/"put")`, iterate positions, call `runner.run_position_monitor()`
- Each wrapper creates a shared `TradingViewFetcher` context manager for browser session reuse

### `web/app.py`
- Updated `_run_agent_in_background()` to pass `scheduler.cosmos` and `scheduler.context_provider`

## Design Decisions
- **Fetcher lifecycle**: TradingViewFetcher is now owned by each agent wrapper (one per agent type per run), not by the runner. This keeps browser sessions alive across symbols within the same agent type.
- **No separate signal context**: Signals are embedded in decisions via `is_signal` field per architecture spec. `ContextProvider.get_context()` handles formatting.
- **logger.py is dead code**: Not removed to avoid breaking any unknown dependents, but no longer imported by agent_runner.

## Team Impact
- Danny: Architecture section 4 implemented as designed. Ready for web dashboard refactor (section 5).
- Linus: Zero changes to instruction files — agent prompt format unchanged.
