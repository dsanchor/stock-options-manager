import asyncio
import json
import logging
import os
import re
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient

from .cosmos_db import CosmosDBService
from .context import ContextProvider

# Canonical timestamp format — used for ALL activity and alert log entries.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Debug logging setup – console only
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_fmt)

logger.addHandler(_console_handler)


class AgentRunner:
    """Manages agent execution using Microsoft Agent Framework with TradingView pre-fetch."""
    
    def __init__(self, project_endpoint: str, model: str, api_key: str):
        """Initialize the agent runner.
        
        Args:
            project_endpoint: Azure AI Foundry project endpoint URL
            model: Model deployment name
            api_key: Azure OpenAI API key
        """
        self.client = AzureOpenAIChatClient(
            endpoint=project_endpoint,
            deployment_name=model,
            api_key=api_key,
        )
    
    # ── JSON / SUMMARY extraction ──────────────────────────────────────

    @staticmethod
    def _try_extract_json(response_text: str) -> Optional[Dict]:
        """Try to parse a JSON activity block from the agent response.

        Looks for fenced ```json blocks first, then falls back to finding a
        raw JSON object that contains an ``"activity"`` key.
        """
        # 1. Fenced code block: ```json ... ```
        fenced = re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL)
        for block in fenced:
            block = block.strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "activity" in data:
                    return data
            except json.JSONDecodeError:
                continue

        # 2. Raw JSON object containing "activity"
        for match in re.finditer(r'\{[^{}]*"activity"\s*:', response_text):
            start = match.start()
            depth = 0
            for i in range(start, len(response_text)):
                if response_text[i] == '{':
                    depth += 1
                elif response_text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = response_text[start:i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict) and "activity" in data:
                                return data
                        except json.JSONDecodeError:
                            break

        return None

    @staticmethod
    def _extract_summary_line(response_text: str) -> Optional[str]:
        """Extract the SUMMARY: line from the agent response."""
        for line in response_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith("SUMMARY:"):
                return stripped
        return None

    def _extract_activity_line(self, symbol: str, response_text: str) -> Tuple[str, Optional[Dict]]:
        """Extract a concise activity line and optional JSON from the response.

        Returns:
            (summary_line, json_data) — json_data is None when the agent used
            the legacy pipe-delimited format.
        """
        ticker = symbol.split('-', 1)[1] if '-' in symbol else symbol

        # Try structured JSON format first
        json_data = self._try_extract_json(response_text)
        if json_data is not None:
            summary = self._extract_summary_line(response_text)
            if summary:
                return summary, json_data
            # Build a SUMMARY from the JSON fields
            activity = json_data.get("activity", "WAIT")
            agent_type = json_data.get("agent", "covered_call").replace("_", " ")
            if activity == "SELL":
                strike = json_data.get("strike", "?")
                exp = json_data.get("expiration", "?")
                iv = json_data.get("iv", "?")
                iv_rank = json_data.get("iv_rank", "?")
                premium = json_data.get("premium", "?")
                premium_pct = json_data.get("premium_pct", "?")
                summary = (
                    f"SUMMARY: {ticker} | SELL {agent_type} | "
                    f"Strike ${strike} exp {exp} | IV {iv}% (Rank {iv_rank}) | "
                    f"Premium ${premium} ({premium_pct}%)"
                )
            else:
                iv = json_data.get("iv", "?")
                iv_rank = json_data.get("iv_rank", "?")
                reason_short = (json_data.get("reason") or "")[:80]
                waiting = json_data.get("waiting_for") or ""
                summary = (
                    f"SUMMARY: {ticker} | WAIT | IV {iv}% (Rank {iv_rank}) "
                    f"{reason_short} | Waiting for: {waiting}"
                )
            return summary, json_data

        # Fallback: legacy pipe-delimited line
        for line in response_text.split('\n'):
            if ticker in line and ('SELL' in line.upper() or 'WAIT' in line.upper()):
                return line.strip(), None

        # Last resort: synthesise a summary
        activity = "SELL" if "SELL" in response_text.upper() and "CLEAR SELL ALERT" in response_text.upper() else "WAIT"
        reason = response_text[:100].replace('\n', ' ').strip()
        return f"{ticker} | ACTIVITY: {activity} | Reason: {reason}", None

    # Fields allowed in the alert log (lean, machine-parseable)
    _ALERT_FIELDS = (
        "timestamp", "symbol", "exchange", "activity",
        "strike", "expiration", "underlying_price",
        "confidence", "risk_flags",
    )

    # Fields for roll alert log (position monitors)
    _ROLL_ALERT_FIELDS = (
        "timestamp", "symbol", "exchange", "action",
        "current_strike", "current_expiration",
        "new_strike", "new_expiration",
        "underlying_price", "confidence", "risk_flags",
    )

    _ROLL_ACTIVITIES = frozenset({
        "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
        "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE",
    })

    def _is_roll_alert(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates a roll or close alert."""
        if json_data is not None:
            activity = json_data.get("activity", "").upper()
            if activity in self._ROLL_ACTIVITIES:
                return True
        # Fallback text check
        upper = response_text.upper()
        return any(f"ACTIVITY: {rd}" in upper or f'"activity": "{rd}"' in upper.replace(" ", "")
                   for rd in self._ROLL_ACTIVITIES)

    def _build_roll_alert_data(self, symbol: str, json_data: Optional[Dict],
                                timestamp: str) -> Dict:
        """Build a roll alert entry with the allowed fields."""
        exchange, ticker = (symbol.split('-', 1) if '-' in symbol
                            else ("", symbol))
        base: Dict = {
            "timestamp": timestamp,
            "symbol": ticker,
            "exchange": exchange,
            "action": json_data.get("activity", "ROLL") if json_data else "ROLL",
        }
        if json_data is not None:
            for key in self._ROLL_ALERT_FIELDS:
                if key in json_data and key not in base:
                    base[key] = json_data[key]
        for key in self._ROLL_ALERT_FIELDS:
            base.setdefault(key, None)
        # Normalize field names so templates/APIs can use standard names
        base["activity"] = base["action"]
        base["strike"] = base.get("new_strike") or base.get("current_strike")
        base["expiration"] = base.get("new_expiration") or base.get("current_expiration")
        return base

    def _build_alert_data(self, symbol: str, json_data: Optional[Dict],
                            timestamp: str) -> Dict:
        """Build an alert entry with only the allowed fields."""
        exchange, ticker = (symbol.split('-', 1) if '-' in symbol
                            else ("", symbol))
        base: Dict = {
            "timestamp": timestamp,
            "symbol": ticker,
            "exchange": exchange,
            "activity": "SELL",
        }
        if json_data is not None:
            for key in self._ALERT_FIELDS:
                if key in json_data and key not in base:
                    base[key] = json_data[key]
        # Ensure every allowed field is present (null if missing)
        for key in self._ALERT_FIELDS:
            base.setdefault(key, None)
        return base

    def _is_sell_alert(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates a clear sell alert.

        Uses structured JSON ``activity`` field when available, with fallback
        to text-based keyword matching for backward compatibility.
        """
        # Structured check
        if json_data is not None:
            if json_data.get("activity", "").upper() == "SELL":
                return True

        # Legacy text-based checks
        upper = response_text.upper()
        return "CLEAR SELL ALERT" in upper or "🚨" in response_text or "ALERT: SELL" in upper
    
    async def run_symbol_agent(
        self,
        name: str,
        instructions: str,
        symbol: str,
        exchange: str,
        agent_type: str,
        cosmos: CosmosDBService,
        context_provider: ContextProvider,
        max_activity_entries: int = 2,
        fetcher=None,
    ):
        """Run agent analysis for a single symbol.

        Args:
            name: Agent name (e.g. "CoveredCallAgent")
            instructions: Base instructions for the agent
            symbol: Ticker symbol (e.g. "AAPL")
            exchange: Exchange code (e.g. "NASDAQ")
            agent_type: Agent type key (e.g. "covered_call")
            cosmos: CosmosDBService instance for persistence
            context_provider: ContextProvider for activity history injection
            max_activity_entries: Max recent activities for context (0–5)
            fetcher: TradingViewFetcher instance (shared across symbols)
        """
        full_symbol = f"{exchange}-{symbol}" if exchange else symbol

        print(f"\n--- Analyzing {full_symbol} ---")
        logger.info("Starting pre-fetch + agent.run() for symbol=%s", full_symbol)

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        run_start = time.time()

        try:
            # Context injection from CosmosDB
            previous_context = context_provider.get_context(
                symbol, agent_type, max_entries=max_activity_entries,
            )

            # Pre-fetch all TradingView data
            data = await fetcher.fetch_all(full_symbol)

            message = f"""Analyze {symbol} (exchange: {exchange}, full symbol: {full_symbol}).

=== PRE-FETCHED TRADINGVIEW DATA ===

--- OVERVIEW PAGE ({exchange}:{symbol}) ---
{data['overview']}

--- TECHNICALS PAGE ({exchange}:{symbol}) ---
{data['technicals']}

--- FORECAST PAGE ({exchange}:{symbol}) ---
{data['forecast']}

--- OPTIONS CHAIN ({exchange}:{symbol}) ---
{data['options_chain']}

=== END OF DATA ===

Previous activities for {symbol}:
{previous_context}

Current timestamp: {analysis_ts}
All market data has been pre-fetched above. Do NOT use any browser tools — analyze the data provided and output your activity in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

            agent = ChatAgent(
                chat_client=self.client,
                name=name,
                instructions=instructions,
            )
            result = await agent.run(message)
            response_text = result.text or str(result)

            logger.info(
                "agent.run() completed for %s – response length=%d",
                full_symbol, len(response_text),
            )
            logger.debug(
                "Response first 500 chars for %s: %s",
                full_symbol, response_text[:500],
            )

            print(f"Response: {response_text[:200]}...")

            # Parse activity from agent output
            activity_line, json_data = self._extract_activity_line(full_symbol, response_text)

            # Build activity payload
            activity_payload: Dict = {}
            if json_data is not None:
                activity_payload = dict(json_data)
                activity_payload["timestamp"] = analysis_ts
            else:
                activity_payload = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "summary": activity_line,
                    "timestamp": analysis_ts,
                }

            # Determine if this is an alert
            is_alert = self._is_sell_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert

            # Write activity to CosmosDB
            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            print(f"Logged activity")

            # Write alert if actionable
            if is_alert:
                alert_data = self._build_alert_data(full_symbol, json_data, analysis_ts)
                cosmos.write_alert(
                    symbol=symbol,
                    agent_type=agent_type,
                    alert_data=alert_data,
                    activity_id=dec_doc["id"],
                    timestamp=analysis_ts,
                )
                print(f"⚠️ SELL ALERT logged for {full_symbol}")

        except Exception as e:
            logger.error(
                "agent.run() FAILED for %s:\n%s",
                full_symbol, traceback.format_exc(),
            )
            print(f"Error analyzing {full_symbol}: {e}")
            cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data={
                    "error": str(e),
                    "symbol": symbol,
                    "exchange": exchange,
                    "timestamp": analysis_ts,
                    "is_alert": False,
                },
                timestamp=analysis_ts,
            )

        # ── Telemetry (best-effort, never blocks) ─────────────────
        try:
            total_duration = round(time.time() - run_start, 2)
            fetch_stats = getattr(fetcher, "last_fetch_stats", {})
            for resource, stats in fetch_stats.items():
                cosmos.write_telemetry("tv_fetch", {
                    "symbol": symbol,
                    "resource": resource,
                    "duration_seconds": stats["duration"],
                    "response_size_chars": stats["size"],
                })
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for %s", full_symbol)

    # ------------------------------------------------------------------
    # Position Monitor (single position, CosmosDB-backed)
    # ------------------------------------------------------------------

    async def run_position_monitor(
        self,
        name: str,
        instructions: str,
        symbol: str,
        exchange: str,
        position: dict,
        agent_type: str,
        cosmos: CosmosDBService,
        context_provider: ContextProvider,
        max_activity_entries: int = 2,
        fetcher=None,
    ):
        """Run position monitor for a single open position.

        Args:
            name: Agent name (e.g. "OpenCallMonitor")
            instructions: System instructions for the monitor agent
            symbol: Ticker symbol
            exchange: Exchange code
            position: Position dict with strike, expiration, position_id, type
            agent_type: Agent type key (e.g. "open_call_monitor")
            cosmos: CosmosDBService instance
            context_provider: ContextProvider for history injection
            max_activity_entries: Max recent activities for context (0–5)
            fetcher: TradingViewFetcher instance (shared)
        """
        full_symbol = f"{exchange}-{symbol}" if exchange else symbol
        strike = position["strike"]
        expiration = position["expiration"]
        position_id = position.get("position_id", "")
        position_type = position.get("type", "call")

        print(f"\n--- Monitoring {symbol} ${strike} exp {expiration} ---")
        logger.info(
            "Position monitor pre-fetch + agent.run() for %s strike=%s exp=%s",
            full_symbol, strike, expiration,
        )

        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)
        run_start = time.time()

        try:
            # Context injection from CosmosDB (filtered by position)
            previous_context = context_provider.get_context(
                symbol, agent_type, max_entries=max_activity_entries,
                position_id=position_id,
            )

            data = await fetcher.fetch_all(full_symbol)

            message = f"""Analyze open {position_type} position for {symbol}:
- Current strike: ${strike}
- Current expiration: {expiration}
- Exchange: {exchange}

=== PRE-FETCHED TRADINGVIEW DATA ===

--- OVERVIEW PAGE ({exchange}:{symbol}) ---
{data['overview']}

--- TECHNICALS PAGE ({exchange}:{symbol}) ---
{data['technicals']}

--- FORECAST PAGE ({exchange}:{symbol}) ---
{data['forecast']}

--- OPTIONS CHAIN ({exchange}:{symbol}) ---
{data['options_chain']}

=== END OF DATA ===

Previous monitor activities for {symbol}:
{previous_context}

Current timestamp: {analysis_ts}
Analyze the position risk and output your activity in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

            agent = ChatAgent(
                chat_client=self.client,
                name=name,
                instructions=instructions,
            )
            result = await agent.run(message)
            response_text = result.text or str(result)

            logger.info(
                "agent.run() completed for %s – response length=%d",
                full_symbol, len(response_text),
            )
            logger.debug(
                "Response first 500 chars for %s: %s",
                full_symbol, response_text[:500],
            )

            print(f"Response: {response_text[:200]}...")

            # Parse activity
            activity_line, json_data = self._extract_activity_line(full_symbol, response_text)

            activity_payload: Dict = {}
            if json_data is not None:
                activity_payload = dict(json_data)
                activity_payload["timestamp"] = analysis_ts
            else:
                activity_payload = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "current_strike": strike,
                    "current_expiration": expiration,
                    "summary": activity_line,
                    "timestamp": analysis_ts,
                }
            activity_payload["position_id"] = position_id

            # Normalize monitor-agent field names so templates/APIs
            # can use standard names (strike, expiration, activity)
            activity_payload.setdefault(
                "strike",
                activity_payload.get("new_strike")
                or activity_payload.get("current_strike"),
            )
            activity_payload.setdefault(
                "expiration",
                activity_payload.get("new_expiration")
                or activity_payload.get("current_expiration"),
            )
            if "action" in activity_payload and "activity" not in activity_payload:
                activity_payload["activity"] = activity_payload["action"]

            # Determine if this is a roll/close alert
            is_alert = self._is_roll_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert

            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            print(f"Logged activity")

            if is_alert:
                alert_data = self._build_roll_alert_data(full_symbol, json_data, analysis_ts)
                cosmos.write_alert(
                    symbol=symbol,
                    agent_type=agent_type,
                    alert_data=alert_data,
                    activity_id=dec_doc["id"],
                    timestamp=analysis_ts,
                )
                print(f"⚠️ ROLL ALERT logged for {full_symbol} ${strike} exp {expiration}")

        except Exception as e:
            logger.error(
                "Position monitor FAILED for %s strike=%s exp=%s:\n%s",
                full_symbol, strike, expiration, traceback.format_exc(),
            )
            print(f"Error monitoring {full_symbol} ${strike} exp {expiration}: {e}")
            cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data={
                    "error": str(e),
                    "symbol": symbol,
                    "exchange": exchange,
                    "current_strike": strike,
                    "current_expiration": expiration,
                    "position_id": position_id,
                    "timestamp": analysis_ts,
                    "is_alert": False,
                },
                timestamp=analysis_ts,
            )

        # ── Telemetry (best-effort, never blocks) ─────────────────
        try:
            total_duration = round(time.time() - run_start, 2)
            fetch_stats = getattr(fetcher, "last_fetch_stats", {})
            for resource, stats in fetch_stats.items():
                cosmos.write_telemetry("tv_fetch", {
                    "symbol": symbol,
                    "resource": resource,
                    "duration_seconds": stats["duration"],
                    "response_size_chars": stats["size"],
                })
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for %s", full_symbol)
