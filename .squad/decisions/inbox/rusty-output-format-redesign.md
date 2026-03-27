# Decision: Structured JSON Output Format for Decisions

**Date:** 2025-07-25  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Team-wide (changes agent output parsing, logging, and instruction format)

## Context

Replaced the pipe-delimited human-readable output format with a machine-parseable JSON schema + SUMMARY line across all 8 instruction files and the agent runner infrastructure.

## Decision

1. **JSON decision block**: Agents output a fenced ```json block with a standardized schema containing all decision fields (symbol, decision, strike, expiration, IV metrics, premium, confidence, risk_flags, etc.)
2. **SUMMARY line**: A one-line human-readable summary immediately after the JSON block
3. **Dual logging**: JSON → `.jsonl` files, SUMMARY → existing `.log` files
4. **Backward compatibility**: agent_runner tries JSON first, falls back to legacy pipe format

## Schema Differences

- Covered call: `"agent": "covered_call"` — standard fields
- Cash-secured put: `"agent": "cash_secured_put"` — adds `"support_level"` field

## Trade-offs

- **Pro**: Machine-parseable output enables downstream automation, dashboards, analytics
- **Pro**: SUMMARY line preserves human readability
- **Pro**: `.jsonl` format enables easy batch processing (one JSON per line)
- **Con**: Larger instruction text (~2KB more per file) due to JSON examples
- **Con**: Agent may occasionally produce malformed JSON (fallback handles this)

## Implications for Team

- **Linus**: Instruction files now specify JSON output format — any new instruction files must follow the same schema
- **Basher**: Test cases should verify JSON extraction from agent responses
- **Danny**: Downstream systems can now consume `.jsonl` files for structured decision data
- **Scribe**: README may need updating to document the new output format
