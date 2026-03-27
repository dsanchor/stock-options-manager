# Decision: Scheduler ↔ Web Communication via app.state

**Date:** 2025-07-22
**Author:** Rusty (Agent Dev)
**Impact:** Architecture (scheduler + web coupling)

## Context
Needed the web layer to signal the scheduler (reschedule cron, trigger individual agents) without import cycles or module-level globals.

## Decision
Store `_scheduler_instance` on FastAPI `app.state.scheduler` during lifespan startup. Web routes access via `request.app.state.scheduler`. In web-only mode (`--web-only`), the attribute is absent — code uses `getattr(..., None)` and degrades gracefully (trigger returns 503, cron saves to YAML but doesn't reschedule).

## Alternatives Considered
- Module-level variable in `run.py` imported by `web/app.py` — creates circular import risk.
- Shared queue / event bus — overengineered for a single-process app.

## Trade-offs
- `app.state` is simple but undocumented contract — anyone changing lifespan must know to set it.
- Trigger endpoint shares the scheduler's config/runner; concurrent triggers won't block each other (separate daemon threads) but the agent runner itself may not be thread-safe for simultaneous runs.
