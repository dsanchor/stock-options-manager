# Project Context

- **Project:** options-agent
- **Created:** 2026-03-26

## Core Context

Agent Scribe maintains squad administrative work: orchestration logs, session logs, decision merging, history summarization, git commits.

## Recent Updates

📌 2026-03-31: Spawned Rusty (alert visibility fix) — orchestration log, session log, decision merge, history summarization to <12KB
📌 Team initialized on 2026-03-26

## Learnings

- History files >12KB need summarization with Core Context section
- Use ISO 8601 UTC timestamps (YYYY-MM-DDTHH:MM:SSZ) for all logs
- Decision inbox items must be merged to decisions.md with deduplication
- Affected agent history files should be updated with cross-team work summaries
