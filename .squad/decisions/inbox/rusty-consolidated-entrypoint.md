# Decision: Consolidated Entry Point (`run.py`)

**Date:** 2025-07
**Author:** Rusty (Agent Dev)

## Context
The project had two separate entry points — `python -m src.main` for the scheduler and `python run_web.py` for the web dashboard. Users had to start them independently in separate terminals.

## Decision
Consolidate into a single `python run.py` that runs both web dashboard and scheduler. The scheduler runs as a daemon thread managed by FastAPI's lifespan context. CLI flags (`--web-only`, `--scheduler-only`, `--port`) provide fine-grained control.

## Key details
- Lifespan attached via `app.router.lifespan_context` — avoids modifying `web/app.py`.
- `OptionsAgentScheduler.run(install_signals=False)` when threaded — signal handlers are main-thread-only.
- `run_web.py` kept as backwards-compat shim delegating to `run.py --web-only`.
- Host/port read from `config.yaml` `web:` section; `--port` flag overrides.

## Files changed
- `run.py` (new) — unified entry point
- `src/main.py` — `run()` accepts `install_signals` param; `__main__` block suggests `run.py`
- `run_web.py` — now delegates to `run.py --web-only`
- `README.md` — updated Running section
