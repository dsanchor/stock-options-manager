# Decision: Runtime Telemetry Infrastructure

**Date:** 2025-07
**Author:** Rusty (Agent Dev)
**Status:** Implemented

## Context

Added a second CosmosDB container (`telemetry`) to track runtime performance stats for agent executions and TradingView data fetching.

## Key Decisions

1. **Separate container for telemetry** — `telemetry` container with partition key `/metric_type` instead of mixing telemetry docs into the `symbols` container. Keeps operational data separate from business data, allows independent TTL policies.

2. **Best-effort initialization** — If the telemetry container doesn't exist, `self.telemetry_container = None` and all writes silently skip. This means the system works without the telemetry container provisioned.

3. **30-day TTL on all telemetry docs** — Each document carries `"ttl": 2592000`. The container uses `defaultTtl: -1` (per-doc TTL enabled). No manual cleanup needed.

4. **Fetcher doesn't write to CosmosDB** — `TradingViewFetcher` stores stats in `self.last_fetch_stats`; the caller (`AgentRunner`) handles the write. This keeps the fetcher decoupled from persistence.

5. **Telemetry writes are post-execution** — Both `run_symbol_agent()` and `run_position_monitor()` write telemetry in a separate `try/except` AFTER the main try/except. Telemetry failures never mask real errors.

6. **Python-side aggregation** — `get_telemetry_stats()` fetches raw docs and aggregates in Python. CosmosDB's SQL doesn't support `AVG` with `GROUP BY` cleanly enough for our needs.

## Impact

- New container must be provisioned (`scripts/provision_cosmosdb.sh` updated, README updated)
- Settings page now shows runtime stats card
- No changes to agent logic or decision/signal flow
