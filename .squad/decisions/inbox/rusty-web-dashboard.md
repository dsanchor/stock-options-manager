# Decision: Web Dashboard Architecture

**Date:** 2025-07-28
**Author:** Rusty (Agent Dev)
**Status:** Completed

## Context
Added a web dashboard for the options agent system — a separate entry point (`run_web.py`) using FastAPI + Jinja2 templates with a dark trading theme.

## Key Decisions

1. **Separate entry point, shared data files**: Web dashboard (`run_web.py`) and scheduler (`python -m src.main`) run independently. Both read the same JSONL logs and data files — no database layer needed.

2. **Raw YAML config loading**: The web app reads `config.yaml` directly via `yaml.safe_load()` instead of using `src.config.Config`, which requires MCP environment variables. The web app only needs the Azure endpoint (for chat) and scheduler cron expression.

3. **No build step**: Vanilla HTML/CSS/JS with custom dark-theme CSS. No npm, no bundler, no CSS framework dependency.

4. **JSONL as the database**: All dashboard data comes from reading JSONL log files and `data/*.txt` files on every request. Acceptable for the current log sizes; would need indexing if logs grow to millions of lines.

5. **Chat uses direct OpenAI API**: The chat endpoint uses `openai.AzureOpenAI` with `AzureCliCredential` — same auth pattern as the agent runner but without the agent framework overhead. Context is the last 20 decisions per log file.

6. **Hot-reload confirmed**: `_read_symbols()` and `_read_positions()` in `agent_runner.py` read from disk on every call inside `run_agent()` / `run_position_monitor_agent()`. No caching — edits via the settings page take effect on the next scheduler tick with zero code changes.

## Trade-offs
- Reading JSONL on every request is fine for current scale but won't scale to huge logs. If needed, add a lightweight caching layer or SQLite index later.
- No authentication on the web dashboard — acceptable for local/internal use. Add auth middleware if exposing to the internet.
