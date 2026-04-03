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
        "WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING",
    })

    # Roll activities that trigger alerts (position monitors)
    _ROLL_ACTIVITIES = frozenset({
        "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
        "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE",
    })

    def _is_alert(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates an alert.
        
        Rule: Anything that is NOT wait, hold, or doing nothing is an alert.
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
            data = await fetcher.fetch_all(full_symbol)

            # ── 403 guard: skip agent if TradingView blocked us ───────
            if getattr(fetcher, "has_403", False):
                logger.warning(
                    "TradingView 403 detected for %s — skipping agent analysis",
                    full_symbol,
                )
                print(f"⛔ TradingView 403 for {full_symbol} — agent skipped")
                cosmos.update_tv_health(is_healthy=False, error="403 Forbidden")
                cosmos.write_activity(
                    symbol=symbol,
                    agent_type=agent_type,
                    activity_data={
                        "symbol": symbol,
                        "exchange": exchange,
                        "timestamp": analysis_ts,
                        "is_alert": False,
                        "activity": "SKIPPED",
                        "summary": "TradingView returned 403 — agent execution skipped",
                        "tv_403": True,
                    },
                    timestamp=analysis_ts,
                )
                return

            # Mark TradingView as healthy on successful fetch
            cosmos.update_tv_health(is_healthy=True)

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

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert
            
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

            # ── 403 guard: skip agent if TradingView blocked us ───────
            if getattr(fetcher, "has_403", False):
                logger.warning(
                    "TradingView 403 detected for %s — skipping position monitor",
                    full_symbol,
                )
                print(f"⛔ TradingView 403 for {full_symbol} — monitor skipped")
                cosmos.update_tv_health(is_healthy=False, error="403 Forbidden")
                cosmos.write_activity(
                    symbol=symbol,
                    agent_type=agent_type,
                    activity_data={
                        "symbol": symbol,
                        "exchange": exchange,
                        "current_strike": strike,
                        "current_expiration": expiration,
                        "position_id": position_id,
                        "timestamp": analysis_ts,
                        "is_alert": False,
                        "activity": "SKIPPED",
                        "summary": "TradingView returned 403 — monitor execution skipped",
                        "tv_403": True,
                    },
                    timestamp=analysis_ts,
                )
                return

            # Mark TradingView as healthy on successful fetch
            cosmos.update_tv_health(is_healthy=True)

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

            # Determine if this is an alert (anything NOT wait/hold/do_nothing)
            is_alert = self._is_alert(response_text, json_data)
            activity_payload["is_alert"] = is_alert
            
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
                })
            cosmos.write_telemetry("agent_run", {
                "symbol": symbol,
                "agent_type": agent_type,
                "duration_seconds": total_duration,
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
            # Fetch recent activities by symbol
            logger.info("Fetching recent activities from CosmosDB (limit=%d per symbol)", activity_count)
            activities_by_symbol = cosmos.get_recent_activities_by_symbol(
                limit_per_symbol=activity_count
            )
            
            if not activities_by_symbol:
                logger.info("No activities found — summary agent has nothing to report")
                print("ℹ️  No activities found — nothing to summarize")
                return
            
            logger.info("Loaded activities for %d symbol(s)", len(activities_by_symbol))
            print(f"📋 Loaded activities for {len(activities_by_symbol)} symbol(s)")
            
            # Format activities data for the agent
            import json
            activities_text = json.dumps(activities_by_symbol, indent=2, default=str)
            
            logger.info("Building prompt with activities data (%d chars)", len(activities_text))
            
            # Build the prompt
            prompt = f"""{TV_SUMMARY_INSTRUCTIONS}

## RECENT ACTIVITIES DATA

The following is a dictionary of recent activities grouped by symbol (newest first):

```json
{activities_text}
```

Generate your 3-line summaries now. Output plain text only — no JSON, no code blocks.
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
