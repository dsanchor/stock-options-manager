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
from .options_chain_parser import (
    parse_options_chain,
    filter_options_chain_for_position,
    filter_options_chain_by_delta,
    filter_options_chain_by_roll_direction,
    format_roll_candidates_table,
    OPTIONS_CHAIN_SCHEMA_DESCRIPTION,
)
from .tv_cache import get_tv_cache as _get_tv_cache

# Canonical timestamp format — used for ALL activity and alert log entries.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Valid action values for Phase 1 → Phase 2 handoff and Phase 2 output.
# Bare "ROLL" is NEVER valid — a direction is always required.
VALID_ROLL_ACTIONS = {
    "ROLL_DOWN", "ROLL_UP", "ROLL_OUT",
    "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT",
}
VALID_PHASE2_ACTIVITIES = VALID_ROLL_ACTIONS | {"WAIT", "CLOSE"}

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
    
    def __init__(self, project_endpoint: str, model: str, api_key: str,
                 telegram_notifier=None):
        """Initialize the agent runner.
        
        Args:
            project_endpoint: Azure AI Foundry project endpoint URL
            model: Model deployment name
            api_key: Azure OpenAI API key
            telegram_notifier: Optional TelegramNotifier for alert notifications
        """
        self.client = AzureOpenAIChatClient(
            endpoint=project_endpoint,
            deployment_name=model,
            api_key=api_key,
        )
        self.telegram_notifier = telegram_notifier
    
    # ── Options chain formatting ────────────────────────────────────────

    @staticmethod
    def _format_options_chain(raw_chain: str, symbol: str, current_strike: float = None, option_type: str = None) -> str:
        """Parse raw options chain through the shared parser; fall back to raw."""
        structured = parse_options_chain(raw_chain, symbol)
        if structured.get("calls") or structured.get("puts"):
            if current_strike is not None:
                structured = filter_options_chain_for_position(structured, current_strike, option_type)
            structured = filter_options_chain_by_delta(structured)
            return (
                OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
                + json.dumps(structured, indent=2)
            )
        return raw_chain

    @staticmethod
    def _format_current_contract_chain(
        raw_chain: str,
        symbol: str,
        current_strike: float,
        expiration: str,
        option_type: str,
    ) -> str:
        """Extract only the current contract from the options chain.

        Returns the chain schema description plus a minimal JSON containing
        just the single strike/expiration being monitored.  This gives
        Phase 1 (assessment) the delta, IV, gamma, theta etc. for the
        current position without the full chain noise.
        """
        structured = parse_options_chain(raw_chain, symbol)
        bucket_key = "calls" if option_type == "call" else "puts"
        bucket = structured.get(bucket_key, {})

        # Normalise expiration to YYYYMMDD (the chain key format)
        exp_key = expiration.replace("-", "")
        strike_key = str(current_strike)
        # Try common float representations (72 → "72.0", 72.5 → "72.5")
        contract = None
        for sk in (strike_key, f"{current_strike:.1f}", f"{current_strike:.2f}"):
            contract = bucket.get(exp_key, {}).get(sk)
            if contract is not None:
                strike_key = sk
                break

        if contract is None:
            return f"(current contract {option_type} ${current_strike} exp {expiration} not found in chain)"

        minimal_chain = {
            "symbol": structured.get("symbol", symbol),
            "timestamp": structured.get("timestamp", ""),
            "current_position": {
                "strike": current_strike,
                "expiration": expiration,
                "type": option_type,
            },
            bucket_key: {
                exp_key: {
                    strike_key: contract,
                },
            },
        }
        return (
            OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
            + json.dumps(minimal_chain, indent=2)
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

    # Activities that are NOT alerts (non-actionable states)
    _NON_ALERT_ACTIVITIES = frozenset({
        "WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING", "SKIPPED",
    })

    # Roll activities that trigger alerts (position monitors)
    _ROLL_ACTIVITIES = frozenset({
        "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
        "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE",
    })

    def _is_alert(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates an alert.
        
        Rule: Anything that is NOT wait, hold, doing nothing, or skipped is an alert.
        This includes SELL, ROLL_*, CLOSE, and any other action-oriented activities.
        """
        if json_data is not None:
            activity = json_data.get("activity", "").upper().strip()
            if activity:
                # If activity is NOT in the non-alert list, it's an alert
                return activity not in self._NON_ALERT_ACTIVITIES
        
        # Fallback text check - look for non-alert keywords
        upper = response_text.upper()
        # Check if it explicitly states a non-alert activity
        for non_alert in self._NON_ALERT_ACTIVITIES:
            if f"ACTIVITY: {non_alert}" in upper or f'"activity": "{non_alert}"' in upper.replace(" ", ""):
                return False
        
        # If we find any activity indicator but no non-alert match, assume it's an alert
        if "ACTIVITY:" in upper or '"activity"' in upper:
            return True
        
        # Legacy fallback: check for explicit alert indicators
        return "CLEAR SELL ALERT" in upper or "🚨" in response_text or "ALERT: SELL" in upper

    def _extract_alert_enrichment(self, json_data: Optional[Dict]) -> Dict:
        """Extract alert-specific enrichment fields (confidence, risk_flags).
        
        Returns a dict with only alert-enrichment fields present in json_data.
        Per Danny's unified schema: alerts are activities with is_alert=true
        and these additional fields merged in.
        """
        enrichment = {}
        if json_data is not None:
            if "confidence" in json_data:
                enrichment["confidence"] = json_data["confidence"]
            if "risk_flags" in json_data:
                enrichment["risk_flags"] = json_data["risk_flags"]
        return enrichment
    
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
            data = await fetcher.fetch_all(full_symbol,
                                           force_refresh=True,
                                           cache=_get_tv_cache())

            # Track partial 403 errors — analysis continues with available data
            has_data_error = data.get("tv_403", False)
            if has_data_error:
                failed = data.get("tv_403_resources", [])
                logger.warning(
                    "TradingView 403 on %s for %s — continuing with partial data",
                    failed, full_symbol,
                )
                print(f"⚠️ TradingView 403 on {failed} for {full_symbol} — continuing with partial data")

            message = f"""Analyze {symbol} (exchange: {exchange}, full symbol: {full_symbol}).

=== PRE-FETCHED TRADINGVIEW DATA ===

--- OVERVIEW PAGE ({exchange}:{symbol}) ---
{data['overview']}

--- TECHNICALS PAGE ({exchange}:{symbol}) ---
{data['technicals']}

--- FORECAST PAGE ({exchange}:{symbol}) ---
{data['forecast']}

--- DIVIDENDS PAGE ({exchange}:{symbol}) ---
{data['dividends']}

--- OPTIONS CHAIN ({exchange}:{symbol}) ---
{self._format_options_chain(data.get('options_chain', ''), symbol)}

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

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert

            # Flag partial data when any TradingView resource returned 403
            if has_data_error:
                activity_payload["data_error"] = True
            
            # If alert, merge alert-enrichment fields into activity payload
            if is_alert:
                alert_enrichment = self._extract_alert_enrichment(json_data)
                activity_payload.update(alert_enrichment)

            # Write activity to CosmosDB (unified write path)
            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            
            if is_alert:
                print(f"⚠️ SELL ALERT logged for {full_symbol}")
                if self.telegram_notifier:
                    # Build display data for Telegram from the activity doc
                    alert_data = {
                        "timestamp": analysis_ts,
                        "symbol": symbol,
                        "exchange": exchange,
                        "activity": json_data.get("activity", "SELL") if json_data else "SELL",
                        "strike": json_data.get("strike") if json_data else None,
                        "expiration": json_data.get("expiration") if json_data else None,
                        "underlying_price": json_data.get("underlying_price") if json_data else None,
                        "confidence": json_data.get("confidence") if json_data else None,
                        "risk_rating": json_data.get("risk_rating") if json_data else None,
                        "risk_flags": json_data.get("risk_flags") if json_data else None,
                    }
                    self.telegram_notifier.send_alert(
                        symbol=symbol, agent_type=agent_type,
                        alert_data=alert_data, is_roll=False,
                    )
            else:
                print(f"Logged activity")

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
                    "error": stats.get("error", False),
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

    # ------------------------------------------------------------------
    # Phase 1 → Phase 2 handoff detection
    # ------------------------------------------------------------------

    @staticmethod
    def _try_extract_handoff_json(response_text: str) -> Optional[Dict]:
        """Extract a handoff JSON block from Phase 1 output.

        A handoff block contains ``action_needed`` (a ROLL_* action)
        signalling that Phase 2 (roll management) should run.  Returns *None*
        when the output is a WAIT activity, the action is invalid, or cannot
        be parsed.

        Invalid actions (bare ``ROLL`` or unknown values) are rejected here
        with a warning so Phase 2 never runs with an ambiguous direction.
        """

        def _validate_action(data: Dict) -> Optional[Dict]:
            """Return *data* if action_needed is valid, else *None*."""
            action = str(data.get("action_needed", "")).upper().strip()
            if action in VALID_ROLL_ACTIONS:
                return data
            if action == "ROLL":
                logger.warning(
                    "Phase 1 returned bare 'ROLL' (no direction) — "
                    "rejecting handoff (will treat as WAIT)",
                )
            else:
                logger.warning(
                    "Phase 1 returned invalid action_needed '%s' — "
                    "rejecting handoff (will treat as WAIT)",
                    action,
                )
            return None

        # Check fenced ```json blocks first
        for block in re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL):
            block = block.strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "action_needed" in data:
                    return _validate_action(data)
            except json.JSONDecodeError:
                continue

        # Fallback: raw JSON object containing "action_needed"
        for match in re.finditer(r'\{[^{}]*"action_needed"\s*:', response_text):
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
                            if isinstance(data, dict) and "action_needed" in data:
                                return _validate_action(data)
                        except json.JSONDecodeError:
                            break
        return None

    # ------------------------------------------------------------------
    # Phase 1: Position Assessment
    # ------------------------------------------------------------------

    async def _run_position_assessment(
        self,
        name: str,
        instructions: str,
        symbol: str,
        exchange: str,
        position_type: str,
        strike: float,
        expiration: str,
        data: dict,
        previous_context: str,
        analysis_ts: str,
        current_contract_chain: str = "",
    ) -> Tuple[str, Optional[Dict], Optional[Dict]]:
        """Run Phase 1 — position assessment agent.

        Returns:
            (response_text, activity_json, handoff_json)
            - activity_json is set when agent outputs a standard activity (WAIT).
            - handoff_json is set when agent outputs an action_needed (ROLL).
            Exactly one of activity_json / handoff_json will be non-None on success.
        """
        full_symbol = f"{exchange}-{symbol}" if exchange else symbol

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

--- CURRENT CONTRACT ({position_type.upper()} ${strike} exp {expiration}) ---
{current_contract_chain}

=== END OF DATA ===

Previous monitor activities for {symbol}:
{previous_context}

Current timestamp: {analysis_ts}
Analyze the position risk and output your response in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

        agent = ChatAgent(
            chat_client=self.client,
            name=name,
            instructions=instructions,
        )
        result = await agent.run(message)
        response_text = result.text or str(result)

        logger.info(
            "Phase 1 (assessment) completed for %s – response length=%d",
            full_symbol, len(response_text),
        )
        logger.debug(
            "Phase 1 first 500 chars for %s: %s",
            full_symbol, response_text[:500],
        )
        print(f"Phase 1 response: {response_text[:200]}...")

        # Try handoff first (action_needed), then standard activity
        handoff_json = self._try_extract_handoff_json(response_text)
        if handoff_json is not None:
            return response_text, None, handoff_json

        # Standard activity (WAIT path)
        activity_json = self._try_extract_json(response_text)
        return response_text, activity_json, None

    # ------------------------------------------------------------------
    # Phase 2: Roll Management
    # ------------------------------------------------------------------

    async def _run_roll_management(
        self,
        name: str,
        roll_instructions: str,
        handoff_json: Dict,
        filtered_chain_text: str,
        analysis_ts: str,
        full_symbol: str,
    ) -> Tuple[str, Optional[Dict]]:
        """Run Phase 2 — roll management agent.

        Receives the Phase 1 handoff payload and the full filtered options
        chain.  Returns (response_text, json_data) following the same
        activity schema as the original single-agent output.
        """
        phase1_text = json.dumps(handoff_json, indent=2)

        message = f"""POSITION ASSESSMENT RESULT:
{phase1_text}

ROLL CANDIDATES:
{filtered_chain_text}

Based on the assessment and candidates above, select the best roll candidate and produce the final activity JSON.
Pick a candidate by its row number. Use the pre-computed values (net credit, DTE, etc.) directly — do NOT recalculate.

Current timestamp: {analysis_ts}
Output your activity in the required JSON format. Use the timestamp above in your JSON output; do NOT generate your own."""

        agent = ChatAgent(
            chat_client=self.client,
            name=f"{name}_roll",
            instructions=roll_instructions,
        )
        result = await agent.run(message)
        response_text = result.text or str(result)

        logger.info(
            "Phase 2 (roll mgmt) completed for %s – response length=%d",
            full_symbol, len(response_text),
        )
        logger.debug(
            "Phase 2 first 500 chars for %s: %s",
            full_symbol, response_text[:500],
        )
        print(f"Phase 2 response: {response_text[:200]}...")

        json_data = self._try_extract_json(response_text)
        return response_text, json_data

    # ------------------------------------------------------------------
    # Position Monitor — 2-phase orchestrator
    # ------------------------------------------------------------------

    async def run_position_monitor(
        self,
        name: str,
        symbol: str,
        exchange: str,
        position: dict,
        agent_type: str,
        cosmos: CosmosDBService,
        context_provider: ContextProvider,
        max_activity_entries: int = 2,
        fetcher=None,
        assessment_instructions: str = None,
        roll_instructions: str = None,
    ):
        """Run position monitor for a single open position (2-phase).

        * **Phase 1 (Position Assessment):** Evaluates position risk using
          overview, technicals, forecast, and previous context — no full chain.
          If the result is WAIT, persists and returns immediately.
        * **Phase 2 (Roll Management):** Only invoked when Phase 1 decides
          action ≠ WAIT.  Receives the Phase 1 handoff JSON plus the full
          filtered options chain and roll-specific instructions.

        Args:
            name: Agent name (e.g. "OpenCallMonitor")
            symbol: Ticker symbol
            exchange: Exchange code
            position: Position dict with strike, expiration, position_id, type
            agent_type: Agent type key (e.g. "open_call_monitor")
            cosmos: CosmosDBService instance
            context_provider: ContextProvider for history injection
            max_activity_entries: Max recent activities for context (0–5)
            fetcher: TradingViewFetcher instance (shared)
            assessment_instructions: Phase 1 system instructions
            roll_instructions: Phase 2 system instructions
        """
        full_symbol = f"{exchange}-{symbol}" if exchange else symbol
        strike = position["strike"]
        expiration = position["expiration"]
        position_id = position.get("position_id", "")
        position_type = position.get("type", "call")

        print(f"\n--- Monitoring {symbol} ${strike} exp {expiration} (2-phase) ---")
        logger.info(
            "Position monitor 2-phase for %s strike=%s exp=%s",
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

            data = await fetcher.fetch_all(full_symbol,
                                           force_refresh=True,
                                           cache=_get_tv_cache())

            # Track partial 403 errors — analysis continues with available data
            has_data_error = data.get("tv_403", False)
            if has_data_error:
                failed = data.get("tv_403_resources", [])
                logger.warning(
                    "TradingView 403 on %s for %s — continuing with partial data",
                    failed, full_symbol,
                )
                print(f"⚠️ TradingView 403 on {failed} for {full_symbol} — continuing with partial data")

            # Pre-compute the structured filtered chain (for Phase 2)
            structured_chain = parse_options_chain(data.get('options_chain', ''), symbol)
            if structured_chain.get("calls") or structured_chain.get("puts"):
                structured_chain = filter_options_chain_for_position(
                    structured_chain, float(strike), position_type,
                )
                structured_chain = filter_options_chain_by_delta(structured_chain)
            filtered_chain_text = (
                OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n"
                + json.dumps(structured_chain, indent=2)
            ) if (structured_chain.get("calls") or structured_chain.get("puts")) else data.get('options_chain', '')

            # ── Two-phase execution ─────────────────────────────────
            # Phase 1 gets only the current contract's chain data
            current_contract_chain = self._format_current_contract_chain(
                data.get('options_chain', ''), symbol,
                current_strike=float(strike),
                expiration=expiration,
                option_type=position_type,
            )

            response_text, activity_json, handoff_json = await self._run_position_assessment(
                name=name,
                instructions=assessment_instructions,
                symbol=symbol,
                exchange=exchange,
                position_type=position_type,
                strike=strike,
                expiration=expiration,
                data=data,
                previous_context=previous_context,
                analysis_ts=analysis_ts,
                current_contract_chain=current_contract_chain,
            )

            if handoff_json is not None:
                # Phase 1 says action needed → run Phase 2
                logger.info(
                    "Phase 1 triggered action '%s' for %s — launching Phase 2",
                    handoff_json.get("action_needed"), full_symbol,
                )
                print(f"↪ Phase 1 action: {handoff_json.get('action_needed')} — running roll management…")

                # Apply direction-aware filtering so Phase 2 only sees
                # strikes/expirations valid for the roll direction.
                roll_type = handoff_json.get("action_needed", "")
                if roll_type and (structured_chain.get("calls") or structured_chain.get("puts")):
                    direction_filtered = filter_options_chain_by_roll_direction(
                        structured_chain,
                        current_strike=float(strike),
                        current_expiration=expiration,
                        roll_type=roll_type,
                        option_type=position_type,
                    )
                    # Pre-compute candidates as a readable table.
                    # Look up buyback cost from the pre-direction-filtered chain
                    # because direction filtering usually excludes the current contract.
                    _bb_cost = None
                    _bb_bucket_key = "calls" if position_type == "call" else "puts"
                    _bb_bucket = structured_chain.get(_bb_bucket_key, {})
                    _bb_exp_key = expiration.replace("-", "")
                    _bb_strike_key = str(float(strike))
                    if _bb_exp_key in _bb_bucket and _bb_strike_key in _bb_bucket[_bb_exp_key]:
                        _bb_ask = _bb_bucket[_bb_exp_key][_bb_strike_key].get("ask")
                        if _bb_ask is not None:
                            _bb_cost = float(_bb_ask)

                    underlying_px = float(
                        handoff_json.get("underlying_price", 0) or 0
                    )
                    filtered_chain_text = format_roll_candidates_table(
                        chain=direction_filtered,
                        current_strike=float(strike),
                        current_expiration=expiration,
                        option_type=position_type,
                        underlying_price=underlying_px,
                        roll_type=roll_type,
                        buyback_cost=_bb_cost,
                    )

                try:
                    phase2_response, phase2_json = await self._run_roll_management(
                        name=name,
                        roll_instructions=roll_instructions,
                        handoff_json=handoff_json,
                        filtered_chain_text=filtered_chain_text,
                        analysis_ts=analysis_ts,
                        full_symbol=full_symbol,
                    )
                    # Use Phase 2 output as the final result
                    response_text = phase2_response
                    json_data = phase2_json

                    # Validate Phase 2 produced usable JSON
                    if json_data is None or "activity" not in (json_data or {}):
                        logger.warning(
                            "Phase 2 returned malformed output for %s — degrading to error payload",
                            full_symbol,
                        )
                        print(f"⚠️ Phase 2 malformed output for {full_symbol} — degrading to error payload")
                        raise ValueError("Phase 2 returned no valid activity JSON")

                    # Reject bare "ROLL" from Phase 2 — direction is required
                    p2_activity = str(json_data.get("activity", "")).upper().strip()
                    if p2_activity == "ROLL":
                        logger.warning(
                            "Phase 2 returned bare 'ROLL' for %s — converting to CLOSE",
                            full_symbol,
                        )
                        json_data["activity"] = "CLOSE"
                        json_data["reason"] = (
                            json_data.get("reason", "")
                            + " [Auto-corrected: bare ROLL converted to CLOSE"
                            " — direction was required]"
                        )
                    elif p2_activity not in VALID_PHASE2_ACTIVITIES:
                        logger.warning(
                            "Phase 2 returned invalid activity '%s' for %s — converting to CLOSE",
                            p2_activity, full_symbol,
                        )
                        json_data["activity"] = "CLOSE"
                        json_data["reason"] = (
                            json_data.get("reason", "")
                            + f" [Auto-corrected: invalid activity '{p2_activity}'"
                            " converted to CLOSE]"
                        )

                    # Validate that ROLL actions include specific target strike and expiration
                    p2_activity = str(json_data.get("activity", "")).upper().strip()
                    if p2_activity in VALID_ROLL_ACTIONS:
                        new_strike = json_data.get("new_strike")
                        new_expiration = json_data.get("new_expiration")
                        if new_strike is None or new_expiration is None:
                            logger.warning(
                                "Phase 2 returned %s for %s without new_strike/new_expiration — converting to CLOSE",
                                p2_activity, full_symbol,
                            )
                            json_data["activity"] = "CLOSE"
                            json_data["reason"] = (
                                json_data.get("reason", "")
                                + f" [Auto-corrected: {p2_activity} had no specific target"
                                " (new_strike/new_expiration missing) — converted to CLOSE]"
                            )
                            json_data["new_strike"] = None
                            json_data["new_expiration"] = None
                            json_data["estimated_roll_cost"] = None
                            if "roll_economics" in json_data:
                                json_data["roll_economics"] = None
                        elif json_data.get("roll_economics") is None:
                            logger.warning(
                                "Phase 2 returned %s for %s without roll_economics — converting to CLOSE",
                                p2_activity, full_symbol,
                            )
                            json_data["activity"] = "CLOSE"
                            json_data["reason"] = (
                                json_data.get("reason", "")
                                + f" [Auto-corrected: {p2_activity} had no roll_economics — converted to CLOSE]"
                            )

                except Exception as phase2_err:
                    # Phase 2 failed — persist Phase 1's handoff with error flag
                    logger.error(
                        "Phase 2 (roll mgmt) FAILED for %s: %s\n%s",
                        full_symbol, phase2_err, traceback.format_exc(),
                    )
                    print(f"⚠️ Phase 2 error for {full_symbol}: {phase2_err} — persisting Phase 1 output with error flag")

                    # Build a degraded activity from the handoff JSON
                    _raw_reason = handoff_json.get("reason", "")
                    # Sanitize internal "Agent 2" references — the reason is user-facing
                    _raw_reason = re.sub(
                        r'\s*;?\s*Agent\s*2\s+[^.]*\.?', '', _raw_reason,
                    ).strip()
                    if not _raw_reason:
                        _raw_reason = "Roll was warranted but roll agent failed."
                    json_data = {
                        "symbol": handoff_json.get("symbol", symbol),
                        "exchange": handoff_json.get("exchange", exchange),
                        "activity": handoff_json.get("action_needed", "CLOSE"),
                        "current_strike": handoff_json.get("current_strike", strike),
                        "current_expiration": handoff_json.get("current_expiration", expiration),
                        "underlying_price": handoff_json.get("underlying_price"),
                        "assignment_risk": handoff_json.get("assignment_risk"),
                        "reason": _raw_reason + " [Roll agent unavailable — manual review recommended]",
                        "confidence": handoff_json.get("confidence"),
                        "roll_economics": None,
                        "risk_flags": list(handoff_json.get("risk_flags", [])) + ["roll_agent_error"],
                        "timestamp": analysis_ts,
                    }
            else:
                # Phase 1 returned WAIT — use directly
                json_data = activity_json

            # ── Persist activity (common path) ────────────────────────
            activity_line, json_data = self._extract_activity_line(full_symbol, response_text) if json_data is None else (
                self._extract_summary_line(response_text) or "", json_data
            )

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

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert

            # Flag partial data when any TradingView resource returned 403
            if has_data_error:
                activity_payload["data_error"] = True
            
            # If alert, merge alert-enrichment fields into activity payload
            if is_alert:
                alert_enrichment = self._extract_alert_enrichment(json_data)
                activity_payload.update(alert_enrichment)

            # Write activity to CosmosDB (unified write path)
            dec_doc = cosmos.write_activity(
                symbol=symbol,
                agent_type=agent_type,
                activity_data=activity_payload,
                timestamp=analysis_ts,
            )
            
            if is_alert:
                print(f"⚠️ ROLL ALERT logged for {full_symbol} ${strike} exp {expiration}")
                if self.telegram_notifier:
                    # Build display data for Telegram from the activity doc
                    alert_data = {
                        "timestamp": analysis_ts,
                        "symbol": symbol,
                        "exchange": exchange,
                        "action": json_data.get("activity", "ROLL") if json_data else "ROLL",
                        "current_strike": strike,
                        "current_expiration": expiration,
                        "new_strike": json_data.get("new_strike") if json_data else None,
                        "new_expiration": json_data.get("new_expiration") if json_data else None,
                        "underlying_price": json_data.get("underlying_price") if json_data else None,
                        "confidence": json_data.get("confidence") if json_data else None,
                        "assignment_risk": json_data.get("assignment_risk") if json_data else None,
                        "risk_flags": json_data.get("risk_flags") if json_data else None,
                    }
                    # Normalize for templates
                    alert_data["activity"] = alert_data["action"]
                    alert_data["strike"] = alert_data.get("new_strike") or alert_data.get("current_strike")
                    alert_data["expiration"] = alert_data.get("new_expiration") or alert_data.get("current_expiration")
                    self.telegram_notifier.send_alert(
                        symbol=symbol, agent_type=agent_type,
                        alert_data=alert_data, is_roll=True,
                    )
            else:
                print(f"Logged activity")

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
                    "error": stats.get("error", False),
                })
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
                "two_phase": True,
            })
        except Exception:
            logger.debug("Telemetry write skipped for %s", full_symbol)

    async def run_summary_agent(
        self,
        cosmos: CosmosDBService,
        telegram_notifier,
        activity_count: int = 3
    ):
        """Generate and send daily portfolio summary via Telegram.
        
        Args:
            cosmos: CosmosDBService instance
            telegram_notifier: TelegramNotifier instance
            activity_count: Number of recent activities per symbol (default: 3)
        """
        from .tv_summary_instructions import TV_SUMMARY_INSTRUCTIONS
        
        logger.info("="*70)
        logger.info("Summary Agent - Starting execution")
        logger.info("  Activity count per symbol: %d", activity_count)
        
        # Gate check: skip if Telegram is not enabled
        if telegram_notifier is None:
            logger.info("Summary agent skipped — Telegram notifier not configured")
            print("⏭️  Summary agent skipped — Telegram notifier not configured")
            return
        
        # Check if Telegram is actually enabled via credentials
        creds = telegram_notifier._get_credentials()
        if creds is None:
            logger.info("Summary agent skipped — Telegram notifications disabled")
            print("⏭️  Summary agent skipped — Telegram notifications disabled")
            return
        
        logger.info("Telegram notifier configured - proceeding with summary")
        print("\n" + "="*70)
        print("📊 DAILY PORTFOLIO SUMMARY AGENT")
        print("="*70)
        
        try:
            import json

            # ── 1. Load all symbol configs for portfolio context ──
            all_symbols = cosmos.list_symbols()
            logger.info("Loaded %d symbol configs", len(all_symbols))

            # Build closed position IDs and portfolio structure
            closed_position_ids: set = set()
            portfolio = {
                "active_calls": [],
                "active_puts": [],
                "watching_calls": [],
                "watching_puts": [],
            }

            for sym_doc in all_symbols:
                sym = sym_doc.get("symbol", "?")
                wl = sym_doc.get("watchlist", {})
                positions = sym_doc.get("positions", [])

                # Track closed position IDs for activity filtering
                for p in positions:
                    if p.get("status") != "active":
                        pid = p.get("position_id")
                        if pid:
                            closed_position_ids.add(pid)

                # Active positions (only active, never closed)
                active_calls = [p for p in positions
                                if p.get("status") == "active"
                                and p.get("type") == "call"]
                active_puts = [p for p in positions
                               if p.get("status") == "active"
                               and p.get("type") == "put"]

                if active_calls:
                    portfolio["active_calls"].append({
                        "symbol": sym,
                        "positions": [
                            {"strike": p.get("strike"),
                             "expiration": p.get("expiration"),
                             "position_id": p.get("position_id")}
                            for p in active_calls
                        ],
                    })
                if active_puts:
                    portfolio["active_puts"].append({
                        "symbol": sym,
                        "positions": [
                            {"strike": p.get("strike"),
                             "expiration": p.get("expiration"),
                             "position_id": p.get("position_id")}
                            for p in active_puts
                        ],
                    })

                # Watchlist / following (only if enabled)
                if wl.get("covered_call"):
                    portfolio["watching_calls"].append(sym)
                if wl.get("cash_secured_put"):
                    portfolio["watching_puts"].append(sym)

            logger.info(
                "Portfolio: %d active-call symbols, %d active-put symbols, "
                "%d watching-calls, %d watching-puts, %d closed position IDs",
                len(portfolio["active_calls"]),
                len(portfolio["active_puts"]),
                len(portfolio["watching_calls"]),
                len(portfolio["watching_puts"]),
                len(closed_position_ids),
            )

            # ── 2. Fetch recent activities and filter closed positions ──
            logger.info("Fetching recent activities from CosmosDB (limit=%d per symbol)", activity_count)
            activities_by_symbol = cosmos.get_recent_activities_by_symbol(
                limit_per_symbol=activity_count
            )

            # Filter out activities linked to closed positions
            if closed_position_ids:
                for sym, acts in list(activities_by_symbol.items()):
                    filtered = [a for a in acts
                                if a.get("position_id") not in closed_position_ids]
                    if filtered:
                        activities_by_symbol[sym] = filtered
                    else:
                        del activities_by_symbol[sym]

            has_activities = bool(activities_by_symbol)
            has_portfolio = (portfolio["active_calls"] or portfolio["active_puts"]
                            or portfolio["watching_calls"] or portfolio["watching_puts"])

            if not has_activities and not has_portfolio:
                logger.info("No activities or portfolio data — summary agent has nothing to report")
                print("ℹ️  No activities or portfolio data — nothing to summarize")
                return

            logger.info("Loaded activities for %d symbol(s)", len(activities_by_symbol))
            print(f"📋 Loaded activities for {len(activities_by_symbol)} symbol(s)")

            portfolio_text = json.dumps(portfolio, indent=2, default=str)
            activities_text = json.dumps(activities_by_symbol, indent=2, default=str) if has_activities else "{}"

            logger.info("Building prompt with portfolio (%d chars) + activities (%d chars)",
                        len(portfolio_text), len(activities_text))

            # ── 3. Build the prompt ──
            prompt = f"""{TV_SUMMARY_INSTRUCTIONS}

## PORTFOLIO OVERVIEW

The following shows ALL active (open) positions and ALL symbols on the watchlist (following).
Use this to determine which symbols belong in each section.
All positions are SOLD (short) options — sold calls and sold puts.

```json
{portfolio_text}
```

## RECENT ACTIVITIES DATA

The following is a dictionary of recent activities grouped by symbol (newest first).
Only includes activities for active (open) positions — closed positions are excluded.

```json
{activities_text}
```

Generate your 3-line summaries now. Output plain text only — no JSON, no code blocks.
Every symbol listed in the portfolio overview MUST appear in the corresponding section, even if there are no recent activities for it.
"""
            
            # Run the agent
            agent = ChatAgent(name="SummaryAgent", chat_client=self.client)
            print("🤖 Running summary agent...")
            logger.info("Invoking ChatAgent with %d symbols", len(activities_by_symbol))
            
            run_start = time.time()
            response = await agent.run(prompt)
            run_duration = round(time.time() - run_start, 2)
            
            logger.info("Agent response received in %.2fs", run_duration)
            
            # Extract the summary text
            summary_text = response.text.strip()
            
            if not summary_text:
                logger.warning("Summary agent returned empty response")
                print("⚠️  Summary agent returned empty response")
                return
            
            logger.info("Summary text extracted (%d chars)", len(summary_text))
            print(f"✅ Summary generated ({run_duration}s)")
            print("\n" + "-"*70)
            print(summary_text)
            print("-"*70 + "\n")
            
            # Send to Telegram
            print("📤 Sending summary to Telegram...")
            logger.info("Preparing Telegram message...")
            header = "📊 <b>Daily Portfolio Summary</b>\n\n"
            telegram_message = header + "<pre>" + summary_text + "</pre>"
            
            logger.info("Sending message to Telegram (length=%d chars)", len(telegram_message))
            success = telegram_notifier.send_message(telegram_message)
            
            if success:
                logger.info("Summary sent to Telegram successfully")
                print("✅ Summary sent to Telegram")
            else:
                logger.warning("Failed to send summary to Telegram")
                print("❌ Failed to send summary to Telegram")
            
            # Telemetry (best-effort)
            try:
                logger.debug("Writing telemetry data to CosmosDB")
                cosmos.write_telemetry("agent_run", {
                    "symbol": "ALL",
                    "agent_type": "summary",
                    "duration_seconds": run_duration,
                    "symbols_count": len(activities_by_symbol),
                })
                logger.debug("Telemetry written successfully")
            except Exception as telem_err:
                logger.debug("Telemetry write skipped for summary agent: %s", str(telem_err))
        
        except Exception as e:
            logger.error("Summary agent failed: %s", str(e), exc_info=True)
            print(f"❌ Summary agent failed: {str(e)}")
        
        logger.info("Summary Agent - Completed execution")
        logger.info("="*70)
        print("="*70 + "\n")

    async def run_report_agent(
        self,
        symbol: str,
        exchange: str,
        context_text: str,
        cosmos: CosmosDBService,
        cached_resources: list | None = None,
    ) -> str:
        """Generate a comprehensive position/situation report for a symbol.

        Uses ChatAgent to produce a structured markdown report from
        pre-gathered context (TradingView data + CosmosDB activities).

        Args:
            symbol: Ticker symbol (e.g. "AAPL")
            exchange: Exchange code (e.g. "NASDAQ")
            context_text: Pre-built context string with all data
            cosmos: CosmosDBService for storing the report
            cached_resources: TradingView resources served from cache

        Returns:
            The generated markdown report text.
        """
        from .tv_report_instructions import TV_REPORT_INSTRUCTIONS

        full_symbol = f"{exchange}-{symbol}" if exchange else symbol
        analysis_ts = datetime.now().strftime(TIMESTAMP_FORMAT)

        logger.info("Starting report agent for %s", full_symbol)
        print(f"\n--- Generating report for {full_symbol} ---")

        run_start = time.time()

        message = f"""Generate a comprehensive situation report for {symbol} (exchange: {exchange}, full symbol: {full_symbol}).

=== AVAILABLE DATA ===

{context_text}

=== END OF DATA ===

Current timestamp: {analysis_ts}
All market data has been pre-fetched above. Do NOT use any browser tools — analyze the data provided and generate your report."""

        agent = ChatAgent(
            chat_client=self.client,
            name="ReportAgent",
            instructions=TV_REPORT_INSTRUCTIONS,
        )
        result = await agent.run(message)
        report_text = result.text or str(result)

        run_duration = round(time.time() - run_start, 2)
        logger.info("Report agent completed for %s in %.2fs (%d chars)",
                     full_symbol, run_duration, len(report_text))
        print(f"✅ Report generated ({run_duration}s, {len(report_text)} chars)")

        # Persist report to CosmosDB
        cosmos.write_report(
            symbol=symbol,
            report_markdown=report_text,
            cached_resources=cached_resources or [],
            timestamp=analysis_ts,
        )

        # Telemetry (best-effort)
        try:
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": "report",
                "duration_seconds": run_duration,
            })
        except Exception:
            logger.debug("Telemetry write skipped for report agent")

        return report_text
