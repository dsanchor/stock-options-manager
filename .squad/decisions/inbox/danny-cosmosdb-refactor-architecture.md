# Architecture Decision: CosmosDB-Centric Refactor

**Date:** 2026-03-28  
**Author:** Danny (Lead)  
**Status:** Proposed  
**Impact:** Full system — data model, scheduler, web dashboard, config, deployment  
**Requested by:** dsanchor

---

## 1. Executive Summary

Replace the current file-based data model (`.txt` symbol lists, `.jsonl` logs) with a **symbol-centric CosmosDB** backend. Every symbol becomes a first-class entity with watchlist settings, open positions, and historical decisions/signals — all queryable via a Python service layer. The only global setting remaining is the cron expression.

### Key Design Decision: Hybrid Document Model

A single-document-per-symbol approach **will exceed CosmosDB's 2MB document limit** for actively traded symbols. A symbol analyzed 5x/day × 365 days × ~1KB per decision = ~1.8MB/year in decisions alone. With signals and positions, this crosses the limit within months.

**Solution: Partition-key-based hybrid model.** Each symbol is a *partition key*, containing multiple document types:
- **`symbol_config`** — One per symbol. Small, frequently read/written. Holds metadata, watchlist flags, and open positions.
- **`decision`** — One per agent run per symbol. Append-only. Contains the full analysis output.
- **`signal`** — One per actionable signal. Subset of decisions where action ≠ WAIT.

All three document types share partition key `symbol` (e.g., `"MO"`), so cross-type queries within a symbol are single-partition and fast.

---

## 2. CosmosDB Data Model

### 2.1 Container Configuration

```
Database:       stock-options-manager
Container:      symbols
Partition key:  /symbol
RU/s:           400 (autoscale to 4000 recommended for scheduled bursts)
Default TTL:    -1 (no expiry; manual cleanup via retention policy)
```

### 2.2 Indexing Policy

```json
{
  "indexingMode": "consistent",
  "automatic": true,
  "includedPaths": [
    { "path": "/symbol/?" },
    { "path": "/doc_type/?" },
    { "path": "/timestamp/?" },
    { "path": "/watchlist/covered_call/?" },
    { "path": "/watchlist/cash_secured_put/?" },
    { "path": "/positions/*/status/?" },
    { "path": "/agent_type/?" },
    { "path": "/decision/?" }
  ],
  "excludedPaths": [
    { "path": "/reason/*" },
    { "path": "/raw_response/*" },
    { "path": "/analysis_context/*" },
    { "path": "/*" }
  ]
}
```

**Rationale:** Index only the fields we query/filter on. Exclude large text blobs (reason, raw_response) to save RUs on writes. The `/*` exclusion is a catch-all; included paths are explicitly re-added.

### 2.3 Document Schemas

#### 2.3.1 Symbol Config Document (`doc_type: "symbol_config"`)

One per symbol. ~2-5KB typical. Max ~50KB even with many positions.

```json
{
  "id": "config_MO",
  "symbol": "MO",
  "doc_type": "symbol_config",
  "exchange": "NYSE",
  "display_name": "Altria Group",

  "watchlist": {
    "covered_call": true,
    "cash_secured_put": false
  },

  "positions": [
    {
      "position_id": "pos_MO_call_72_20260424",
      "type": "call",
      "strike": 72.0,
      "expiration": "2026-04-24",
      "opened_at": "2026-03-15T14:30:00Z",
      "status": "active",
      "notes": "Sold covered call at $1.35 premium"
    },
    {
      "position_id": "pos_MO_put_60_20260515",
      "type": "put",
      "strike": 60.0,
      "expiration": "2026-05-15",
      "opened_at": "2026-04-01T10:00:00Z",
      "status": "active",
      "notes": ""
    }
  ],

  "created_at": "2026-03-28T12:00:00Z",
  "updated_at": "2026-03-28T12:00:00Z",
  "_etag": "..."
}
```

**Position lifecycle:**
- `status: "active"` → position is monitored by open call/put monitors
- `status: "closed"` → position is ignored by monitors; kept for history
- Closed positions can be purged periodically or archived

**Position ID format:** `pos_{SYMBOL}_{type}_{strike}_{expYYYYMMDD}` — deterministic, human-readable, and unique within a symbol.

#### 2.3.2 Decision Document (`doc_type: "decision"`)

One per symbol per agent per scheduler run. ~1-3KB each.

```json
{
  "id": "dec_MO_covered_call_20260328_143000",
  "symbol": "MO",
  "doc_type": "decision",
  "agent_type": "covered_call",
  "timestamp": "2026-03-28T14:30:00Z",

  "decision": "WAIT",
  "strike": null,
  "expiration": null,
  "dte": null,
  "iv": 21.0,
  "iv_rank": 33,
  "delta": null,
  "premium": null,
  "premium_pct": null,
  "underlying_price": 50.31,

  "reason": "Technicals are mixed-to-neutral...",
  "waiting_for": "post-earnings setup after 2026-04-21...",
  "confidence": "medium",
  "risk_flags": ["earnings_soon", "moderate_iv"],

  "is_signal": false,

  "position_id": null,
  "current_strike": null,
  "current_expiration": null
}
```

**For position monitor decisions**, the extra fields are populated:

```json
{
  "id": "dec_MO_open_call_monitor_pos_MO_call_72_20260424_20260328_143000",
  "symbol": "MO",
  "doc_type": "decision",
  "agent_type": "open_call_monitor",
  "timestamp": "2026-03-28T14:30:00Z",

  "decision": "WAIT",
  "position_id": "pos_MO_call_72_20260424",
  "current_strike": 72.0,
  "current_expiration": "2026-04-24",

  "moneyness": "OTM",
  "dte_remaining": 27,
  "assignment_risk": "low",
  "delta": -0.15,
  "underlying_price": 66.48,

  "reason": "Position is safely OTM...",
  "confidence": "high",
  "risk_flags": [],

  "is_signal": false,

  "new_strike": null,
  "new_expiration": null
}
```

#### 2.3.3 Signal Document (`doc_type: "signal"`)

Created alongside a decision when the decision is actionable (SELL, ROLL_*, CLOSE). Contains a lean subset of the decision data.

```json
{
  "id": "sig_MO_covered_call_20260328_143000",
  "symbol": "MO",
  "doc_type": "signal",
  "agent_type": "covered_call",
  "timestamp": "2026-03-28T14:30:00Z",
  "decision_id": "dec_MO_covered_call_20260328_143000",

  "decision": "SELL",
  "strike": 72.0,
  "expiration": "2026-04-24",
  "underlying_price": 66.48,
  "confidence": "high",
  "risk_flags": []
}
```

**Rationale for separate signal documents (vs. just `is_signal: true` on decisions):** Signals are the primary read target for the dashboard and downstream consumers. Keeping them as a separate doc_type means a single cross-partition query `WHERE c.doc_type = 'signal'` returns only actionable items without scanning all decisions. The `is_signal` flag on decisions is kept for cross-referencing convenience.

### 2.4 Document Size Analysis

| Document type | Typical size | Growth rate | Partition size at 1 year |
|---|---|---|---|
| symbol_config | 2-5 KB | Static (updated, not appended) | 5 KB |
| decision | 1-3 KB | ~5/day × 4 agents = 20/day | ~7 MB |
| signal | 0.5-1 KB | ~1-5/week | ~250 KB |

**Total per symbol per year: ~7.3 MB** — well under CosmosDB's **20 GB logical partition limit**. Individual documents stay under 5 KB, far below the 2 MB document limit. ✅

### 2.5 Retention / Cleanup Strategy

Decisions older than 90 days can be archived or deleted. Options:
1. **CosmosDB TTL** — Set `ttlSeconds` on decision documents at write time (e.g., 90 days = 7776000 seconds)
2. **Scheduled cleanup job** — Query and delete decisions older than threshold
3. **Change Feed + Azure Function** — Archive to blob storage before TTL deletes

**Recommendation:** Use TTL on decision documents (90 days default, configurable). Keep signals indefinitely for audit trail. Symbol configs are permanent.

---

## 3. API / Service Layer Design

### 3.1 New Module: `src/cosmos_db.py`

Central service class for all CosmosDB operations.

```python
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from typing import Optional
from datetime import datetime
import uuid


class CosmosDBService:
    """Service layer for CosmosDB operations."""

    def __init__(self, endpoint: str, key: str, database_name: str = "stock-options-manager"):
        self.client = CosmosClient(endpoint, credential=key)
        self.database = self.client.get_database_client(database_name)
        self.container = self.database.get_container_client("symbols")

    # ── Symbol Config CRUD ─────────────────────────────────────────────

    def create_symbol(self, symbol: str, exchange: str,
                      display_name: str = "",
                      covered_call: bool = False,
                      cash_secured_put: bool = False) -> dict:
        """Create a new symbol config document."""
        doc = {
            "id": f"config_{symbol}",
            "symbol": symbol,
            "doc_type": "symbol_config",
            "exchange": exchange,
            "display_name": display_name or symbol,
            "watchlist": {
                "covered_call": covered_call,
                "cash_secured_put": cash_secured_put,
            },
            "positions": [],
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        return self.container.create_item(doc)

    def get_symbol(self, symbol: str) -> Optional[dict]:
        """Get symbol config by ticker."""
        try:
            return self.container.read_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            return None

    def list_symbols(self) -> list[dict]:
        """List all symbol configs."""
        query = "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def update_watchlist(self, symbol: str, covered_call: Optional[bool] = None,
                         cash_secured_put: Optional[bool] = None) -> dict:
        """Update watchlist flags for a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")
        if covered_call is not None:
            doc["watchlist"]["covered_call"] = covered_call
        if cash_secured_put is not None:
            doc["watchlist"]["cash_secured_put"] = cash_secured_put
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_symbol(self, symbol: str) -> None:
        """Delete a symbol config and ALL associated decisions/signals."""
        # Delete config doc
        try:
            self.container.delete_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            pass
        # Delete all decisions and signals in this partition
        query = "SELECT c.id FROM c WHERE c.symbol = @symbol AND c.doc_type != 'symbol_config'"
        items = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@symbol", "value": symbol}],
            partition_key=symbol,
        ))
        for item in items:
            self.container.delete_item(item=item["id"], partition_key=symbol)

    # ── Watchlist Queries (used by scheduler) ──────────────────────────

    def get_covered_call_symbols(self) -> list[dict]:
        """Get all symbols enabled for covered call watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.covered_call = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_cash_secured_put_symbols(self) -> list[dict]:
        """Get all symbols enabled for cash-secured put watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.cash_secured_put = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_symbols_with_active_positions(self, position_type: str) -> list[dict]:
        """Get symbol configs that have active positions of a given type.

        Args:
            position_type: "call" or "put"

        Returns:
            List of symbol_config documents with at least one active position
            matching the type. Filters to only active positions in the result.
        """
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND ARRAY_LENGTH(c.positions) > 0"
        )
        results = list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))
        # Client-side filter for position type and status
        filtered = []
        for doc in results:
            active = [
                p for p in doc.get("positions", [])
                if p["type"] == position_type and p["status"] == "active"
            ]
            if active:
                doc["_active_positions"] = active
                filtered.append(doc)
        return filtered

    # ── Position Management ────────────────────────────────────────────

    def add_position(self, symbol: str, position_type: str,
                     strike: float, expiration: str,
                     notes: str = "") -> dict:
        """Add an open position to a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        exp_compact = expiration.replace("-", "")
        position_id = f"pos_{symbol}_{position_type}_{strike}_{exp_compact}"

        # Check for duplicate
        existing_ids = {p["position_id"] for p in doc.get("positions", [])}
        if position_id in existing_ids:
            raise ValueError(f"Position {position_id} already exists")

        position = {
            "position_id": position_id,
            "type": position_type,
            "strike": strike,
            "expiration": expiration,
            "opened_at": datetime.utcnow().isoformat() + "Z",
            "status": "active",
            "notes": notes,
        }
        doc["positions"].append(position)
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def close_position(self, symbol: str, position_id: str) -> dict:
        """Mark a position as closed."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                pos["status"] = "closed"
                pos["closed_at"] = datetime.utcnow().isoformat() + "Z"
                break
        else:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_position(self, symbol: str, position_id: str) -> dict:
        """Remove a position entirely from a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        doc["positions"] = [
            p for p in doc.get("positions", [])
            if p["position_id"] != position_id
        ]
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    # ── Decision / Signal Write ────────────────────────────────────────

    def write_decision(self, symbol: str, agent_type: str,
                       decision_data: dict,
                       timestamp: str | None = None) -> dict:
        """Write a decision document.

        Args:
            symbol: Ticker symbol (partition key).
            agent_type: One of "covered_call", "cash_secured_put",
                "open_call_monitor", "open_put_monitor".
            decision_data: Full decision dict from agent output.
            timestamp: Override timestamp (ISO format). Defaults to now.

        Returns:
            The created CosmosDB document.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        position_id = decision_data.get("position_id", "")
        id_suffix = f"_{position_id}" if position_id else ""

        doc_id = f"dec_{symbol}_{agent_type}{id_suffix}_{ts_compact}"

        doc = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "decision",
            "agent_type": agent_type,
            "timestamp": ts,
            "is_signal": False,
            **decision_data,
        }
        # Remove any accidental id override from decision_data
        doc["id"] = doc_id

        return self.container.create_item(doc)

    def write_signal(self, symbol: str, agent_type: str,
                     signal_data: dict, decision_id: str,
                     timestamp: str | None = None) -> dict:
        """Write a signal document linked to a decision."""
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        doc_id = f"sig_{symbol}_{agent_type}_{ts_compact}"

        doc = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "signal",
            "agent_type": agent_type,
            "timestamp": ts,
            "decision_id": decision_id,
            **signal_data,
        }
        doc["id"] = doc_id

        return self.container.create_item(doc)

    # ── Decision / Signal Read (context injection) ─────────────────────

    def get_recent_decisions(self, symbol: str, agent_type: str,
                             max_entries: int = 20,
                             position_id: str | None = None) -> list[dict]:
        """Get recent decisions for a symbol+agent, newest first.

        For position monitors, filter by position_id.
        """
        conditions = [
            "c.doc_type = 'decision'",
            "c.agent_type = @agent_type",
        ]
        params = [
            {"name": "@agent_type", "value": agent_type},
        ]
        if position_id:
            conditions.append("c.position_id = @position_id")
            params.append({"name": "@position_id", "value": position_id})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": max_entries})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            partition_key=symbol,
        ))

    def get_recent_signals(self, symbol: str, agent_type: str,
                           max_entries: int = 10) -> list[dict]:
        """Get recent signals for a symbol+agent, newest first."""
        query = (
            "SELECT TOP @limit * FROM c "
            "WHERE c.doc_type = 'signal' AND c.agent_type = @agent_type "
            "ORDER BY c.timestamp DESC"
        )
        return list(self.container.query_items(
            query=query,
            parameters=[
                {"name": "@agent_type", "value": agent_type},
                {"name": "@limit", "value": max_entries},
            ],
            partition_key=symbol,
        ))

    # ── Dashboard Queries ──────────────────────────────────────────────

    def get_all_signals(self, agent_type: str | None = None,
                        since: str | None = None,
                        limit: int = 100) -> list[dict]:
        """Get signals across all symbols (cross-partition query)."""
        conditions = ["c.doc_type = 'signal'"]
        params = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def get_all_decisions(self, agent_type: str | None = None,
                          since: str | None = None,
                          limit: int = 100) -> list[dict]:
        """Get decisions across all symbols (cross-partition query)."""
        conditions = ["c.doc_type = 'decision'"]
        params = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def count_signals_by_symbol(self, agent_type: str,
                                 since: str | None = None) -> dict[str, int]:
        """Count signals per symbol for dashboard aggregation."""
        conditions = ["c.doc_type = 'signal'", "c.agent_type = @agent_type"]
        params = [{"name": "@agent_type", "value": agent_type}]
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT c.symbol, COUNT(1) as count FROM c "
            f"WHERE {' AND '.join(conditions)} GROUP BY c.symbol"
        )
        results = list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))
        return {r["symbol"]: r["count"] for r in results}
```

### 3.2 Context Injection Adapter

> **Updated per user directive:** Context injection uses a single `get_context()` method that fetches the last N decisions (default 2, configurable 0–5). Each decision includes its signal status via the `is_signal` field. There is NO separate signal context — signals are embedded in their parent decisions.

Replace `src/logger.py` read functions with a single CosmosDB-backed method.

```python
# src/context.py

class ContextProvider:
    """Provides per-symbol decision context for agent prompts."""

    def __init__(self, cosmos: CosmosDBService):
        self.cosmos = cosmos

    def get_context(self, symbol: str, agent_type: str,
                    max_entries: int = 2,
                    position_id: str | None = None) -> str:
        """Return formatted context of recent decisions with embedded signal status.

        Args:
            max_entries: Number of recent decisions (0-5, default 2).
        """
        if max_entries <= 0:
            return "Context injection disabled."
        decisions = self.cosmos.get_recent_decisions(
            symbol, agent_type, max_entries, position_id
        )
        if not decisions:
            return "No previous decisions recorded."
        blocks = []
        for d in reversed(decisions):  # oldest-first
            header = f"[{d.get('timestamp', '?')}] {d.get('decision', '?')}"
            if d.get("is_signal"):
                header += " ⚡ SIGNAL"
            blocks.append(f"{header}\n{d.get('reason', '')}")
        return "\n\n".join(blocks)
```

---

## 4. Scheduler Changes

### 4.1 New Discovery Flow

**Current:** Read `.txt` files → list of symbols/positions → run agents sequentially.

**New:** Query CosmosDB → list of symbol configs with watchlist flags/positions → run agents.

```python
# src/main.py — _run_all_agents_async() rewrite sketch

async def _run_all_agents_async(self):
    cosmos = self.cosmos  # CosmosDBService instance

    # 1. Covered call analysis
    cc_symbols = cosmos.get_covered_call_symbols()
    for sym_doc in cc_symbols:
        await runner.run_symbol_agent(
            name="CoveredCallAgent",
            instructions=TV_COVERED_CALL_INSTRUCTIONS,
            symbol=sym_doc["symbol"],
            exchange=sym_doc["exchange"],
            agent_type="covered_call",
            cosmos=cosmos,
        )

    # 2. Cash-secured put analysis
    csp_symbols = cosmos.get_cash_secured_put_symbols()
    for sym_doc in csp_symbols:
        await runner.run_symbol_agent(
            name="CashSecuredPutAgent",
            instructions=TV_CASH_SECURED_PUT_INSTRUCTIONS,
            symbol=sym_doc["symbol"],
            exchange=sym_doc["exchange"],
            agent_type="cash_secured_put",
            cosmos=cosmos,
        )

    # 3. Open call monitor
    call_symbols = cosmos.get_symbols_with_active_positions("call")
    for sym_doc in call_symbols:
        for pos in sym_doc["_active_positions"]:
            await runner.run_position_monitor(
                name="OpenCallMonitor",
                instructions=TV_OPEN_CALL_INSTRUCTIONS,
                symbol=sym_doc["symbol"],
                exchange=sym_doc["exchange"],
                position=pos,
                agent_type="open_call_monitor",
                cosmos=cosmos,
            )

    # 4. Open put monitor
    put_symbols = cosmos.get_symbols_with_active_positions("put")
    for sym_doc in put_symbols:
        for pos in sym_doc["_active_positions"]:
            await runner.run_position_monitor(
                name="OpenPutMonitor",
                instructions=TV_OPEN_PUT_INSTRUCTIONS,
                symbol=sym_doc["symbol"],
                exchange=sym_doc["exchange"],
                position=pos,
                agent_type="open_put_monitor",
                cosmos=cosmos,
            )
```

### 4.2 Agent Runner Changes

`AgentRunner` methods change from file-based to CosmosDB-based:

| Current method | New method | Key change |
|---|---|---|
| `run_agent(symbols_file=...)` | `run_symbol_agent(symbol=..., cosmos=...)` | Single symbol per call, reads context from CosmosDB |
| `run_position_monitor_agent(positions_file=...)` | `run_position_monitor(symbol=..., position=..., cosmos=...)` | Single position per call |
| `_read_symbols()` | **Removed** — scheduler queries CosmosDB directly |
| `_read_positions()` | **Removed** — scheduler queries CosmosDB directly |
| `append_decision() / append_signal()` | `cosmos.write_decision() / cosmos.write_signal()` | Write to CosmosDB instead of JSONL |

The agent runner **no longer owns discovery** — it only handles single-symbol execution and result persistence.

---

## 5. Web Dashboard Changes

### 5.1 New API Endpoints

The web dashboard needs full CRUD for symbol management plus the existing read-only views.

#### Symbol Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/symbols` | List all symbols with configs |
| `POST` | `/api/symbols` | Create new symbol |
| `GET` | `/api/symbols/{symbol}` | Get symbol detail |
| `PUT` | `/api/symbols/{symbol}` | Update symbol (watchlist flags, display name) |
| `DELETE` | `/api/symbols/{symbol}` | Delete symbol + all data |

#### Position Management

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/symbols/{symbol}/positions` | Add new position |
| `PUT` | `/api/symbols/{symbol}/positions/{position_id}/close` | Close a position |
| `DELETE` | `/api/symbols/{symbol}/positions/{position_id}` | Delete a position |

#### Data Views (dashboard reads)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/signals` | List signals (query params: agent_type, since, limit) |
| `GET` | `/api/decisions` | List decisions (query params: agent_type, symbol, since, limit) |
| `GET` | `/api/dashboard` | Aggregated dashboard data |

### 5.2 UI Flows

**New "Symbols" page** (replaces current Settings file editors):
1. Table of all symbols: ticker, exchange, CC enabled ✓/✗, CSP enabled ✓/✗, # active positions
2. Inline toggle switches for watchlist flags
3. "Add Symbol" form: ticker, exchange, optional display name
4. Click into symbol → detail page showing positions + recent decisions
5. "Add Position" form within symbol detail: type (call/put), strike, expiration, notes

**Updated Settings page**:
- Only cron expression remains
- CosmosDB connection info shown (read-only) for diagnostics

**Dashboard changes**:
- Current dashboard structure stays the same (agent tables, signal counts)
- Data source changes from JSONL reads to CosmosDB queries
- `_build_agent_table()` refactored to call `CosmosDBService` methods
- Remove `DATA_FILES`, `AGENT_TYPES` file-path mappings (replaced by CosmosDB queries)

### 5.3 Web App Dependency Injection

```python
# web/app.py — startup

from src.cosmos_db import CosmosDBService

@app.on_event("startup")
async def startup():
    config = _load_config()
    cosmos_cfg = config["cosmosdb"]
    app.state.cosmos = CosmosDBService(
        endpoint=cosmos_cfg["endpoint"],
        key=cosmos_cfg["key"],
        database_name=cosmos_cfg.get("database", "stock-options-manager"),
    )
```

---

## 6. Config Changes

### 6.1 New `config.yaml`

```yaml
azure:
  project_endpoint: "${AZURE_AI_PROJECT_ENDPOINT}"
  model_deployment: "${MODEL_DEPLOYMENT}"
  api_key: "${AZURE_OPENAI_API_KEY}"

cosmosdb:
  endpoint: "${COSMOSDB_ENDPOINT}"
  key: "${COSMOSDB_KEY}"
  database: "stock-options-manager"

context:
  max_decision_entries: 2   # Recent decisions to inject as agent context (0=none, max 5). Each includes its signal if actionable.
  decision_ttl_days: 90

scheduler:
  cron: "0 14-21/2 * * 1-5"

web:
  host: "0.0.0.0"
  port: 8000
```

### 6.2 Removed Config Sections

- `covered_call.symbols_file` / `decision_log` / `signal_log` — **removed**
- `cash_secured_put.*` — **removed**
- `open_call_monitor.*` — **removed**
- `open_put_monitor.*` — **removed**

All per-agent file paths are gone. The agent type is a string parameter, not a config section.

### 6.3 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | Azure AI Foundry endpoint |
| `MODEL_DEPLOYMENT` | Yes | Model name (e.g., gpt-5.1) |
| `AZURE_OPENAI_API_KEY` | Yes | Azure OpenAI key |
| `COSMOSDB_ENDPOINT` | **New** | CosmosDB account endpoint |
| `COSMOSDB_KEY` | **New** | CosmosDB primary key |

### 6.4 Config Validation Update

`Config._validate()` adds required fields: `('cosmosdb', 'endpoint')` and `('cosmosdb', 'key')`.

---

## 7. Migration Path

### 7.1 Migration Script: `scripts/migrate_to_cosmosdb.py`

```python
"""Migrate existing file-based data to CosmosDB.

Usage:
    python scripts/migrate_to_cosmosdb.py

Reads:
    - data/covered_call_symbols.txt
    - data/cash_secured_put_symbols.txt
    - data/opened_calls.txt
    - data/opened_puts.txt
    - logs/*.jsonl

Writes:
    - CosmosDB symbol_config documents
    - CosmosDB decision documents
    - CosmosDB signal documents
"""

import json
import os
from src.cosmos_db import CosmosDBService


def migrate():
    cosmos = CosmosDBService(
        endpoint=os.environ["COSMOSDB_ENDPOINT"],
        key=os.environ["COSMOSDB_KEY"],
    )

    # Track all symbols we've seen
    symbols_seen: dict[str, dict] = {}

    # 1. Parse symbol files → build symbol_config documents
    for line in open("data/covered_call_symbols.txt"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        exchange, ticker = line.split("-", 1) if "-" in line else ("", line)
        symbols_seen.setdefault(ticker, {
            "exchange": exchange, "cc": False, "csp": False, "positions": [],
        })
        symbols_seen[ticker]["cc"] = True

    for line in open("data/cash_secured_put_symbols.txt"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        exchange, ticker = line.split("-", 1) if "-" in line else ("", line)
        symbols_seen.setdefault(ticker, {
            "exchange": exchange, "cc": False, "csp": False, "positions": [],
        })
        symbols_seen[ticker]["csp"] = True

    # 2. Parse position files → add to symbol configs
    for pos_file, pos_type in [
        ("data/opened_calls.txt", "call"),
        ("data/opened_puts.txt", "put"),
    ]:
        for line in open(pos_file):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if len(parts) != 3:
                continue
            sym_str, strike, exp = parts[0].strip(), parts[1].strip(), parts[2].strip()
            exchange, ticker = sym_str.split("-", 1) if "-" in sym_str else ("", sym_str)
            symbols_seen.setdefault(ticker, {
                "exchange": exchange, "cc": False, "csp": False, "positions": [],
            })
            symbols_seen[ticker]["positions"].append({
                "type": pos_type,
                "strike": float(strike),
                "expiration": exp,
            })

    # 3. Create symbol_config documents
    for ticker, info in symbols_seen.items():
        cosmos.create_symbol(
            symbol=ticker,
            exchange=info["exchange"],
            covered_call=info["cc"],
            cash_secured_put=info["csp"],
        )
        for pos in info["positions"]:
            cosmos.add_position(
                symbol=ticker,
                position_type=pos["type"],
                strike=pos["strike"],
                expiration=pos["expiration"],
            )
        print(f"Migrated symbol: {ticker}")

    # 4. Migrate JSONL logs
    agent_type_map = {
        "logs/covered_call_decisions.jsonl": ("decision", "covered_call"),
        "logs/covered_call_signals.jsonl": ("signal", "covered_call"),
        "logs/cash_secured_put_decisions.jsonl": ("decision", "cash_secured_put"),
        "logs/cash_secured_put_signals.jsonl": ("signal", "cash_secured_put"),
        "logs/open_call_monitor_decisions.jsonl": ("decision", "open_call_monitor"),
        "logs/open_call_monitor_signals.jsonl": ("signal", "open_call_monitor"),
        "logs/open_put_monitor_decisions.jsonl": ("decision", "open_put_monitor"),
        "logs/open_put_monitor_signals.jsonl": ("signal", "open_put_monitor"),
    }

    for log_file, (doc_kind, agent_type) in agent_type_map.items():
        if not os.path.exists(log_file):
            continue
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                symbol = entry.get("symbol", "UNKNOWN")
                ts = entry.get("timestamp")

                if doc_kind == "decision":
                    cosmos.write_decision(symbol, agent_type, entry, timestamp=ts)
                else:
                    cosmos.write_signal(
                        symbol, agent_type, entry,
                        decision_id="migrated",
                        timestamp=ts,
                    )

        print(f"Migrated {log_file}")

    print("Migration complete!")


if __name__ == "__main__":
    migrate()
```

### 7.2 Backward Compatibility

- **JSONL files are preserved** — they remain on disk, just no longer read or written. Can be deleted after migration verification.
- **Data files (`data/*.txt`) are preserved** — they become the migration source and can be removed after verifying CosmosDB state.
- **Agent instructions are unchanged** — they receive the same formatted context strings.
- **Config.yaml** — old sections are ignored (not validated). Add new `cosmosdb` section.

### 7.3 Rollback Strategy

If CosmosDB migration fails:
1. Revert code to the pre-migration branch
2. JSONL files and `.txt` data files are still intact on disk
3. No data is lost — the migration is additive, not destructive

---

## 8. Azure Provisioning

### 8.1 az CLI Commands

```bash
# Variables
RESOURCE_GROUP="rg-stock-options-manager"
LOCATION="eastus"
COSMOSDB_ACCOUNT="cosmos-stock-options"
DATABASE_NAME="stock-options-manager"
CONTAINER_NAME="symbols"

# 1. Create Resource Group (if not exists)
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION

# 2. Create CosmosDB Account (serverless for low-traffic, or provisioned for predictable workloads)
# Option A: Serverless (pay-per-request, best for dev/low-traffic)
az cosmosdb create \
  --name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --kind GlobalDocumentDB \
  --capabilities EnableServerless \
  --default-consistency-level Session \
  --locations regionName=$LOCATION failoverPriority=0 isZoneRedundant=false

# Option B: Provisioned (predictable cost, autoscale)
# az cosmosdb create \
#   --name $COSMOSDB_ACCOUNT \
#   --resource-group $RESOURCE_GROUP \
#   --kind GlobalDocumentDB \
#   --default-consistency-level Session \
#   --locations regionName=$LOCATION failoverPriority=0 isZoneRedundant=false \
#   --enable-automatic-failover false

# 3. Create Database
az cosmosdb sql database create \
  --account-name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --name $DATABASE_NAME

# 4. Create Container with partition key
# For Serverless:
az cosmosdb sql container create \
  --account-name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --database-name $DATABASE_NAME \
  --name $CONTAINER_NAME \
  --partition-key-path "/symbol" \
  --partition-key-version 2

# For Provisioned (with autoscale):
# az cosmosdb sql container create \
#   --account-name $COSMOSDB_ACCOUNT \
#   --resource-group $RESOURCE_GROUP \
#   --database-name $DATABASE_NAME \
#   --name $CONTAINER_NAME \
#   --partition-key-path "/symbol" \
#   --partition-key-version 2 \
#   --max-throughput 4000

# 5. Set custom indexing policy
az cosmosdb sql container update \
  --account-name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --database-name $DATABASE_NAME \
  --name $CONTAINER_NAME \
  --idx '{
    "indexingMode": "consistent",
    "automatic": true,
    "includedPaths": [
      {"path": "/symbol/?"},
      {"path": "/doc_type/?"},
      {"path": "/timestamp/?"},
      {"path": "/watchlist/covered_call/?"},
      {"path": "/watchlist/cash_secured_put/?"},
      {"path": "/agent_type/?"},
      {"path": "/decision/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }'

# 6. Get connection details
az cosmosdb keys list \
  --name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --type keys

COSMOSDB_ENDPOINT=$(az cosmosdb show \
  --name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --query documentEndpoint \
  --output tsv)

COSMOSDB_KEY=$(az cosmosdb keys list \
  --name $COSMOSDB_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --query primaryMasterKey \
  --output tsv)

echo "COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "COSMOSDB_KEY=$COSMOSDB_KEY"
```

### 8.2 Environment Setup

Add to `.env` (or Docker env):
```bash
COSMOSDB_ENDPOINT=https://cosmos-stock-options.documents.azure.com:443/
COSMOSDB_KEY=<primary-key-from-step-6>
```

### 8.3 Python Dependency

Add to `requirements.txt`:
```
azure-cosmos>=4.7.0
```

---

## 9. File / Module Structure

### 9.1 Proposed New Structure

```
src/
├── __init__.py
├── config.py                    # MODIFIED — add cosmosdb config, remove per-agent file paths
├── cosmos_db.py                 # NEW — CosmosDB service layer (Section 3.1)
├── context.py                   # NEW — context injection adapter (Section 3.2)
├── agent_runner.py              # MODIFIED — remove file I/O, accept CosmosDB service
├── main.py                      # MODIFIED — scheduler queries CosmosDB for discovery
├── covered_call_agent.py        # MODIFIED — pass cosmos service instead of file paths
├── cash_secured_put_agent.py    # MODIFIED — same
├── open_call_monitor_agent.py   # MODIFIED — same
├── open_put_monitor_agent.py    # MODIFIED — same
├── logger.py                    # DEPRECATED — kept for migration, no longer imported
├── tv_data_fetcher.py           # UNCHANGED
├── tv_covered_call_instructions.py    # UNCHANGED
├── tv_cash_secured_put_instructions.py # UNCHANGED
├── tv_open_call_instructions.py        # UNCHANGED
├── tv_open_put_instructions.py         # UNCHANGED

web/
├── app.py                       # MODIFIED — use CosmosDB, add CRUD endpoints
├── static/                      # MODIFIED — add JS for symbol management UI
├── templates/
│   ├── dashboard.html           # MODIFIED — data from CosmosDB
│   ├── symbols.html             # NEW — symbol management page
│   ├── symbol_detail.html       # NEW — single symbol view with positions
│   ├── signals.html             # MODIFIED — data from CosmosDB
│   ├── signal_detail.html       # MODIFIED
│   ├── decision_detail.html     # MODIFIED
│   ├── settings.html            # SIMPLIFIED — cron only
│   └── chat.html                # MODIFIED — context from CosmosDB

scripts/
├── migrate_to_cosmosdb.py       # NEW — migration script
├── provision_cosmosdb.sh        # NEW — az CLI provisioning script

config.yaml                      # MODIFIED — Section 6.1
requirements.txt                 # MODIFIED — add azure-cosmos
Dockerfile                       # MODIFIED — remove data/logs volume mounts
README.md                        # MODIFIED — new setup instructions
```

### 9.2 Files Removed (after migration verified)

```
data/covered_call_symbols.txt     # Migrated to CosmosDB watchlist flags
data/cash_secured_put_symbols.txt # Migrated to CosmosDB watchlist flags
data/opened_calls.txt             # Migrated to CosmosDB positions
data/opened_puts.txt              # Migrated to CosmosDB positions
logs/*.jsonl                      # Migrated to CosmosDB decisions/signals
```

### 9.3 Dockerfile Changes

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY config.yaml run.py run_web.py ./
COPY src/ src/
COPY web/ web/

# No more data/ or logs/ volume mounts needed

EXPOSE 8000

ENTRYPOINT ["python", "run.py"]
```

---

## 10. Implementation Plan (Sequencing)

### Phase 1: Foundation (Rusty)
1. Add `azure-cosmos` to requirements.txt
2. Create `src/cosmos_db.py` with full service layer
3. Create `src/context.py` adapter
4. Update `config.yaml` and `src/config.py`
5. Write unit tests for CosmosDB service (mock container)

### Phase 2: Scheduler + Agent Runner (Rusty)
1. Refactor `src/agent_runner.py` to accept CosmosDB service
2. Refactor `src/main.py` scheduler discovery
3. Update all 4 agent modules
4. Integration test with real CosmosDB

### Phase 3: Web Dashboard (Rusty)
1. Add CRUD API endpoints to `web/app.py`
2. Create `symbols.html` and `symbol_detail.html` templates
3. Refactor dashboard to read from CosmosDB
4. Simplify settings page

### Phase 4: Migration + Deployment (Basher)
1. Create `scripts/provision_cosmosdb.sh`
2. Create `scripts/migrate_to_cosmosdb.py`
3. Update Dockerfile
4. Update README.md
5. End-to-end validation

---

## 11. Key Trade-offs & Rationale

| Decision | Alternatives considered | Rationale |
|---|---|---|
| Single container, multiple doc_types | Separate containers per type | Same partition key enables efficient single-partition queries; simpler to manage |
| Partition key = symbol ticker | Composite key, random ID | Most queries are per-symbol; natural grouping; good distribution across tickers |
| Serverless CosmosDB (default) | Provisioned throughput | Low traffic (5 runs/day × 10 symbols = 50 operations). Serverless costs pennies. Autoscale provisioned for production. |
| TTL-based cleanup for decisions | Manual cleanup, Change Feed archive | Simplest; no extra infrastructure. Signal documents kept indefinitely for audit. |
| Positions embedded in symbol_config | Separate position documents | Positions per symbol are few (<20). Embedding avoids extra queries. Close/delete is rare. |
| Cross-partition queries for dashboard | Dedicated dashboard materialized view | Dashboard is read by humans (low QPS). Cross-partition query cost is acceptable. If needed later, add Change Feed materialization. |
| Keep agent instructions unchanged | Rewrite for CosmosDB-aware instructions | Instructions are LLM prompts — they don't know about storage. Context injection adapter preserves interface. |

---

## 12. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CosmosDB latency adds to scheduler run time | Low | Medium | Single-partition reads are <10ms. Cross-partition for discovery is once per run (~50ms). Negligible vs. TradingView fetch + LLM inference. |
| Document ID collisions | Low | High | Deterministic ID format includes symbol + agent_type + timestamp. Sub-second collisions impossible with sequential agent execution. |
| Cross-partition query cost in dashboard | Medium | Low | Dashboard queries are human-triggered (low QPS). Monitor RU consumption; add indexes or materialized views if needed. |
| Migration data inconsistency | Low | Medium | Migration script is idempotent — re-run safely. Verify counts post-migration. Keep original files as backup. |
| CosmosDB unavailability | Very Low | High | Session consistency + single region is sufficient. No multi-region failover needed for personal trading tool. |
