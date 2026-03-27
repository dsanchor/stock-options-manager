import asyncio
import json
import logging
import os
import re
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from agent_framework import ChatAgent, MCPStdioTool, MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity import AzureCliCredential

from .logger import read_decision_log, read_signal_log, append_decision, append_signal

# ---------------------------------------------------------------------------
# Debug logging setup – outputs to console AND logs/mcp_debug.log
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set to INFO or DEBUG as needed

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(_fmt)

_file_handler = logging.FileHandler("logs/agents.log")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_fmt)

logger.addHandler(_console_handler)
logger.addHandler(_file_handler)


def _mask_url(url: str) -> str:
    """Mask the apikey query-param value in a URL for safe logging."""
    return re.sub(r'(apikey=)[^&]+', r'\1***', url, flags=re.IGNORECASE)


class AgentRunner:
    """Manages agent execution using Microsoft Agent Framework with MCP integration."""
    
    def __init__(self, project_endpoint: str, model: str, mcp_command: str, mcp_args: List[str], 
                 mcp_description: str, mcp_provider: str = "massive", mcp_env_key: str = "MASSIVE_API_KEY",
                 mcp_transport: str = "stdio", mcp_url: str = ""):
        """Initialize the agent runner.
        
        Args:
            project_endpoint: Azure AI Foundry project endpoint URL
            model: Model deployment name
            mcp_command: Command to launch MCP server (e.g., "uvx")
            mcp_args: Arguments for MCP command
            mcp_description: Description of the MCP server capabilities
            mcp_provider: Name of the MCP provider (used as MCP tool name)
            mcp_env_key: Environment variable name required by the MCP provider
            mcp_transport: Transport type ("stdio" or "streamable_http")
            mcp_url: URL for HTTP-based MCP providers
        """
        credential = AzureCliCredential()
        self.client = AzureOpenAIChatClient(
            endpoint=project_endpoint,
            deployment_name=model,
            credential=credential,
        )
        self.mcp_command = mcp_command
        self.mcp_args = mcp_args
        self.mcp_description = mcp_description
        self.mcp_provider = mcp_provider
        self.mcp_env_key = mcp_env_key
        self.mcp_transport = mcp_transport
        self.mcp_url = mcp_url
    
    def _read_symbols(self, symbols_file: str) -> List[str]:
        """Read symbols from file, one per line, ignoring comments."""
        if not os.path.exists(symbols_file):
            print(f"Warning: Symbols file {symbols_file} not found")
            return []
        
        with open(symbols_file, 'r') as f:
            symbols = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        return symbols

    @staticmethod
    def _read_positions(positions_file: str) -> List[Tuple[str, float, str]]:
        """Read positions from file. Each line: EXCHANGE-SYMBOL,strike,expiration.

        Returns:
            List of (symbol, strike, expiration) tuples.
            symbol is the full EXCHANGE-SYMBOL string (e.g. "NYSE-MO").
        """
        if not os.path.exists(positions_file):
            return []

        positions: List[Tuple[str, float, str]] = []
        with open(positions_file, 'r') as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) != 3:
                    logger.warning(
                        "Skipping malformed position line %d in %s: %r",
                        lineno, positions_file, line,
                    )
                    continue
                symbol_str, strike_str, expiration = parts
                try:
                    strike = float(strike_str)
                except ValueError:
                    logger.warning(
                        "Invalid strike %r on line %d of %s — skipping",
                        strike_str, lineno, positions_file,
                    )
                    continue
                positions.append((symbol_str, strike, expiration))
        return positions
    
    # ── JSON / SUMMARY extraction ──────────────────────────────────────

    @staticmethod
    def _try_extract_json(response_text: str) -> Optional[Dict]:
        """Try to parse a JSON decision block from the agent response.

        Looks for fenced ```json blocks first, then falls back to finding a
        raw JSON object that contains a ``"decision"`` key.
        """
        # 1. Fenced code block: ```json ... ```
        fenced = re.findall(r'```json\s*\n(.*?)```', response_text, re.DOTALL)
        for block in fenced:
            block = block.strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "decision" in data:
                    return data
            except json.JSONDecodeError:
                continue

        # 2. Raw JSON object containing "decision"
        for match in re.finditer(r'\{[^{}]*"decision"\s*:', response_text):
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
                            if isinstance(data, dict) and "decision" in data:
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

    def _extract_decision_line(self, symbol: str, response_text: str) -> Tuple[str, Optional[Dict]]:
        """Extract a concise decision line and optional JSON from the response.

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
            decision = json_data.get("decision", "WAIT")
            agent_type = json_data.get("agent", "covered_call").replace("_", " ")
            if decision == "SELL":
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
        decision = "SELL" if "SELL" in response_text.upper() and "CLEAR SELL SIGNAL" in response_text.upper() else "WAIT"
        reason = response_text[:100].replace('\n', ' ').strip()
        return f"{ticker} | DECISION: {decision} | Reason: {reason}", None

    # Fields allowed in the signal log (lean, machine-parseable)
    _SIGNAL_FIELDS = (
        "timestamp", "symbol", "exchange", "decision",
        "strike", "expiration", "underlying_price",
        "confidence", "risk_flags",
    )

    # Fields for roll signal log (position monitors)
    _ROLL_SIGNAL_FIELDS = (
        "timestamp", "symbol", "exchange", "action",
        "current_strike", "current_expiration",
        "new_strike", "new_expiration",
        "underlying_price", "confidence", "risk_flags",
    )

    _ROLL_DECISIONS = frozenset({
        "ROLL_UP", "ROLL_DOWN", "ROLL_OUT",
        "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT", "CLOSE",
    })

    def _is_roll_signal(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates a roll or close signal."""
        if json_data is not None:
            decision = json_data.get("decision", "").upper()
            if decision in self._ROLL_DECISIONS:
                return True
        # Fallback text check
        upper = response_text.upper()
        return any(f"DECISION: {rd}" in upper or f'"decision": "{rd}"' in upper.replace(" ", "")
                   for rd in self._ROLL_DECISIONS)

    def _build_roll_signal_data(self, symbol: str, json_data: Optional[Dict]) -> Dict:
        """Build a roll signal entry with the allowed fields."""
        exchange, ticker = (symbol.split('-', 1) if '-' in symbol
                            else ("", symbol))
        base: Dict = {
            "timestamp": datetime.now().isoformat(),
            "symbol": ticker,
            "exchange": exchange,
            "action": json_data.get("decision", "ROLL") if json_data else "ROLL",
        }
        if json_data is not None:
            for key in self._ROLL_SIGNAL_FIELDS:
                if key in json_data and key not in base:
                    base[key] = json_data[key]
        for key in self._ROLL_SIGNAL_FIELDS:
            base.setdefault(key, None)
        return base

    def _build_signal_data(self, symbol: str, json_data: Optional[Dict]) -> Dict:
        """Build a signal entry with only the allowed fields."""
        exchange, ticker = (symbol.split('-', 1) if '-' in symbol
                            else ("", symbol))
        base: Dict = {
            "timestamp": datetime.now().isoformat(),
            "symbol": ticker,
            "exchange": exchange,
            "decision": "SELL",
        }
        if json_data is not None:
            for key in self._SIGNAL_FIELDS:
                if key in json_data and key not in base:
                    base[key] = json_data[key]
        # Ensure every allowed field is present (null if missing)
        for key in self._SIGNAL_FIELDS:
            base.setdefault(key, None)
        return base

    def _is_sell_signal(self, response_text: str, json_data: Optional[Dict] = None) -> bool:
        """Check if response indicates a clear sell signal.

        Uses structured JSON ``decision`` field when available, with fallback
        to text-based keyword matching for backward compatibility.
        """
        # Structured check
        if json_data is not None:
            if json_data.get("decision", "").upper() == "SELL":
                return True

        # Legacy text-based checks
        upper = response_text.upper()
        return "CLEAR SELL SIGNAL" in upper or "🚨" in response_text or "SIGNAL: SELL" in upper
    
    async def run_agent(self, name: str, instructions: str, symbols_file: str, 
                       decision_log_path: str, signal_log_path: str,
                       max_decision_entries: int = 20,
                       max_signal_entries: int = 10):
        """Run agent analysis for all symbols.
        
        Args:
            name: Agent name
            instructions: Base instructions for the agent
            symbols_file: Path to file containing symbols (one per line)
            decision_log_path: Path to decision log
            signal_log_path: Path to signal log
            max_decision_entries: Max recent decisions to inject per symbol
            max_signal_entries: Max recent signals to inject per symbol
        """
        print(f"\n{'='*60}")
        print(f"Starting {name} analysis")
        print(f"{'='*60}")
        
        # Read symbols
        symbols = self._read_symbols(symbols_file)
        if not symbols:
            print(f"No symbols found in {symbols_file}")
            return
        
        print(f"Analyzing {len(symbols)} symbols: {', '.join(symbols)}")
        
        # Validate API key is set (skip for providers that don't need one, e.g. Yahoo Finance)
        if self.mcp_env_key and not os.environ.get(self.mcp_env_key):
            raise RuntimeError(
                f"{self.mcp_env_key} environment variable is not set. "
                f"Export it before running: export {self.mcp_env_key}='your-key'"
            )
        
        # -----------------------------------------------------------------
        # TradingView path: pre-fetch data in Python, agent only analyzes
        # -----------------------------------------------------------------
        if self.mcp_provider == "tradingview":
            from .tv_data_fetcher import TradingViewFetcher

            async with TradingViewFetcher(self.mcp_command, self.mcp_args) as fetcher:
                agent = ChatAgent(
                    chat_client=self.client,
                    name=name,
                    instructions=instructions,
                    # NO tools — agent only analyzes pre-fetched data
                )
                logger.debug(
                    "ChatAgent '%s' (TradingView pre-fetch mode).",
                    name,
                )

                for symbol in symbols:
                    print(f"\n--- Analyzing {symbol} ---")
                    logger.info("Starting pre-fetch + agent.run() for symbol=%s", symbol)

                    try:
                        exchange, ticker = symbol.split('-', 1) if '-' in symbol else ("", symbol)

                        # Read per-symbol context
                        previous_decisions = read_decision_log(
                            decision_log_path, max_entries=max_decision_entries, symbol=ticker)
                        previous_signals = read_signal_log(
                            signal_log_path, max_entries=max_signal_entries, symbol=ticker)

                        # Pre-fetch all TradingView data deterministically
                        data = await fetcher.fetch_all(symbol)

                        message = f"""Analyze {ticker} (exchange: {exchange}, full symbol: {symbol}).

=== PRE-FETCHED TRADINGVIEW DATA ===

--- OVERVIEW PAGE ({exchange}:{ticker}) ---
{data['overview']}

--- TECHNICALS PAGE ({exchange}:{ticker}) ---
{data['technicals']}

--- FORECAST PAGE ({exchange}:{ticker}) ---
{data['forecast']}

--- OPTIONS CHAIN ({exchange}:{ticker}) ---
{data['options_chain']}

=== END OF DATA ===

Previous decisions for {ticker}:
{previous_decisions}

Previous signals for {ticker}:
{previous_signals}

All market data has been pre-fetched above. Do NOT use any browser tools — analyze the data provided and output your decision in the required JSON format."""

                        result = await agent.run(message)
                        response_text = result.text or str(result)

                        logger.info(
                            "agent.run() completed for %s – response length=%d",
                            symbol, len(response_text),
                        )
                        logger.debug(
                            "Response first 500 chars for %s: %s",
                            symbol, response_text[:500],
                        )

                        print(f"Response: {response_text[:200]}...")

                        # Log decision
                        decision_line, json_data = self._extract_decision_line(symbol, response_text)
                        if json_data is not None:
                            append_decision(decision_log_path, json_data)
                        else:
                            append_decision(decision_log_path, {
                                "symbol": symbol,
                                "summary": decision_line,
                                "timestamp": datetime.now().isoformat(),
                            })
                        print(f"Logged decision")

                        # Check for sell signal
                        if self._is_sell_signal(response_text, json_data):
                            signal_data = self._build_signal_data(symbol, json_data)
                            append_signal(signal_log_path, signal_data)
                            print(f"⚠️ SELL SIGNAL logged for {symbol}")

                    except Exception as e:
                        logger.error(
                            "agent.run() FAILED for %s:\n%s",
                            symbol, traceback.format_exc(),
                        )
                        print(f"Error analyzing {symbol}: {e}")
                        append_decision(decision_log_path, {
                            "error": str(e),
                            "symbol": symbol,
                            "timestamp": datetime.now().isoformat(),
                        })

        # -----------------------------------------------------------------
        # All other providers: existing MCP-based flow (agent uses tools)
        # -----------------------------------------------------------------
        else:
            # Create MCP tool based on transport type
            if self.mcp_transport == "streamable_http":
                safe_url = _mask_url(self.mcp_url)
                logger.info(
                    "Creating MCPStreamableHTTPTool – provider=%s, url=%s, description=%s",
                    self.mcp_provider, safe_url, self.mcp_description,
                )
                mcp_tool = MCPStreamableHTTPTool(
                    name=self.mcp_provider,
                    url=self.mcp_url,
                    description=self.mcp_description,
                    approval_mode="never_require",
                )
            else:
                logger.info(
                    "Creating MCPStdioTool – provider=%s, command=%s, args=%s, description=%s",
                    self.mcp_provider, self.mcp_command, self.mcp_args, self.mcp_description,
                )
                mcp_env = os.environ.copy()
                mcp_tool = MCPStdioTool(
                    name=self.mcp_provider,
                    command=self.mcp_command,
                    args=self.mcp_args,
                    description=self.mcp_description,
                    approval_mode="never_require",
                    env=mcp_env,
                )
            logger.debug("MCP tool object created: %r", mcp_tool)

            # Use context manager for proper MCP cleanup
            try:
                logger.info("Opening MCP connection (entering context manager)…")
                ctx = mcp_tool.__aenter__()
                await ctx
                logger.info("MCP connection established successfully.")
            except Exception:
                logger.error(
                    "MCP connection FAILED during context-manager entry:\n%s",
                    traceback.format_exc(),
                )
                raise

            try:
                # Tool discovery
                if hasattr(mcp_tool, "tools"):
                    logger.debug("Available MCP tools: %s", mcp_tool.tools)
                elif hasattr(mcp_tool, "list_tools"):
                    try:
                        discovered = await mcp_tool.list_tools()
                        logger.debug("Available MCP tools: %s", discovered)
                    except Exception:
                        logger.debug("list_tools() call failed: %s", traceback.format_exc())
                else:
                    logger.debug(
                        "MCP tool object has no .tools attr or list_tools() method – "
                        "skipping tool discovery."
                    )

                agent = ChatAgent(
                    chat_client=self.client,
                    name=name,
                    instructions=instructions,
                    tools=mcp_tool,
                )
                logger.debug("ChatAgent '%s' created with MCP tool '%s'.", name, self.mcp_provider)

                for symbol in symbols:
                    print(f"\n--- Analyzing {symbol} ---")
                    logger.info("Starting agent.run() for symbol=%s", symbol)

                    try:
                        if '-' in symbol:
                            exchange, ticker = symbol.split('-', 1)
                        else:
                            exchange, ticker = "", symbol

                        # Read per-symbol context
                        previous_decisions = read_decision_log(
                            decision_log_path, max_entries=max_decision_entries, symbol=ticker)
                        previous_signals = read_signal_log(
                            signal_log_path, max_entries=max_signal_entries, symbol=ticker)

                        message = f"""Analyze {ticker} (exchange: {exchange}, full symbol: {symbol}) and provide a trading decision.

Previous decisions for {ticker}:
{previous_decisions}

Previous signals for {ticker}:
{previous_signals}

Analyze {ticker} NOW. Use the available MCP tools to gather current data. The full exchange-symbol is {symbol}. Provide your decision in the required output format."""

                        result = await agent.run(message)
                        response_text = result.text or str(result)

                        logger.info(
                            "agent.run() completed for %s – response length=%d",
                            symbol, len(response_text),
                        )
                        logger.debug(
                            "Response first 500 chars for %s: %s",
                            symbol, response_text[:500],
                        )

                        # Trace tool calls from the conversation
                        if result.messages:
                            tool_calls_summary = []
                            for msg in result.messages:
                                role = getattr(msg, 'role', None) or getattr(msg, 'type', '?')
                                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        fn_name = getattr(tc, 'name', None) or (tc.function.name if hasattr(tc, 'function') else '?')
                                        fn_args = getattr(tc, 'arguments', None) or (tc.function.arguments if hasattr(tc, 'function') else '')
                                        tool_calls_summary.append(f"{fn_name}({str(fn_args)[:120]})")
                                elif hasattr(msg, 'content') and role in ('tool', 'function'):
                                    content_len = len(str(msg.content)) if msg.content else 0
                                    tool_calls_summary.append(f"  → response ({content_len} chars)")
                            if tool_calls_summary:
                                logger.debug(
                                    "Tool call trace for %s:\n  %s",
                                    symbol, "\n  ".join(tool_calls_summary),
                                )

                        print(f"Response: {response_text[:200]}...")

                        # Log decision
                        decision_line, json_data = self._extract_decision_line(symbol, response_text)
                        if json_data is not None:
                            append_decision(decision_log_path, json_data)
                        else:
                            append_decision(decision_log_path, {
                                "symbol": symbol,
                                "summary": decision_line,
                                "timestamp": datetime.now().isoformat(),
                            })
                        print(f"Logged decision")

                        # Check for sell signal
                        if self._is_sell_signal(response_text, json_data):
                            signal_data = self._build_signal_data(symbol, json_data)
                            append_signal(signal_log_path, signal_data)
                            print(f"⚠️ SELL SIGNAL logged for {symbol}")

                    except Exception as e:
                        logger.error(
                            "agent.run() FAILED for %s:\n%s",
                            symbol, traceback.format_exc(),
                        )
                        print(f"Error analyzing {symbol}: {e}")
                        append_decision(decision_log_path, {
                            "error": str(e),
                            "symbol": symbol,
                            "timestamp": datetime.now().isoformat(),
                        })

            finally:
                # MCP cleanup – always exit the context manager
                try:
                    logger.info("Closing MCP connection (exiting context manager)…")
                    await mcp_tool.__aexit__(None, None, None)
                    logger.info("MCP connection closed normally.")
                except Exception:
                    logger.error(
                        "MCP cleanup raised an exception:\n%s",
                        traceback.format_exc(),
                    )

        print(f"\n{'='*60}")
        print(f"Completed {name} analysis")
        print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Position Monitor Agent (TradingView-only)
    # ------------------------------------------------------------------

    async def run_position_monitor_agent(
        self,
        name: str,
        instructions: str,
        positions_file: str,
        decision_log_path: str,
        signal_log_path: str,
        position_type: str = "call",
        max_decision_entries: int = 20,
        max_signal_entries: int = 10,
    ):
        """Run position monitor for open options positions (TradingView only).

        Args:
            name: Agent name (e.g. "OpenCallMonitor")
            instructions: System instructions for the monitor agent
            positions_file: Path to file with positions (EXCHANGE-SYMBOL,strike,expiration)
            decision_log_path: Path to decision JSONL log
            signal_log_path: Path to signal JSONL log (roll signals only)
            position_type: "call" or "put"
            max_decision_entries: Max recent decisions to inject per symbol
            max_signal_entries: Max recent signals to inject per symbol
        """
        print(f"\n{'='*60}")
        print(f"Starting {name} monitoring")
        print(f"{'='*60}")

        positions = self._read_positions(positions_file)
        if not positions:
            print(f"No active positions in {positions_file} — skipping {name}")
            return

        print(f"Monitoring {len(positions)} open {position_type} position(s)")

        from .tv_data_fetcher import TradingViewFetcher

        async with TradingViewFetcher(self.mcp_command, self.mcp_args) as fetcher:
            agent = ChatAgent(
                chat_client=self.client,
                name=name,
                instructions=instructions,
            )
            logger.debug("ChatAgent '%s' (position monitor, TradingView pre-fetch).", name)

            for symbol, strike, expiration in positions:
                exchange, ticker = (symbol.split('-', 1) if '-' in symbol
                                    else ("", symbol))

                print(f"\n--- Monitoring {ticker} ${strike} exp {expiration} ---")
                logger.info(
                    "Position monitor pre-fetch + agent.run() for %s strike=%s exp=%s",
                    symbol, strike, expiration,
                )

                try:
                    previous_decisions = read_decision_log(
                        decision_log_path, max_entries=max_decision_entries, symbol=ticker)
                    previous_signals = read_signal_log(
                        signal_log_path, max_entries=max_signal_entries, symbol=ticker)

                    data = await fetcher.fetch_all(symbol)

                    message = f"""Analyze open {position_type} position for {ticker}:
- Current strike: ${strike}
- Current expiration: {expiration}
- Exchange: {exchange}

=== PRE-FETCHED TRADINGVIEW DATA ===

--- OVERVIEW PAGE ({exchange}:{ticker}) ---
{data['overview']}

--- TECHNICALS PAGE ({exchange}:{ticker}) ---
{data['technicals']}

--- FORECAST PAGE ({exchange}:{ticker}) ---
{data['forecast']}

--- OPTIONS CHAIN ({exchange}:{ticker}) ---
{data['options_chain']}

=== END OF DATA ===

Previous monitor decisions for {ticker}:
{previous_decisions}

Previous roll signals for {ticker}:
{previous_signals}

Analyze the position risk and output your decision in the required JSON format."""

                    result = await agent.run(message)
                    response_text = result.text or str(result)

                    logger.info(
                        "agent.run() completed for %s – response length=%d",
                        symbol, len(response_text),
                    )
                    logger.debug(
                        "Response first 500 chars for %s: %s",
                        symbol, response_text[:500],
                    )

                    print(f"Response: {response_text[:200]}...")

                    # Log decision (every position, every run)
                    decision_line, json_data = self._extract_decision_line(symbol, response_text)
                    if json_data is not None:
                        append_decision(decision_log_path, json_data)
                    else:
                        append_decision(decision_log_path, {
                            "symbol": ticker,
                            "exchange": exchange,
                            "current_strike": strike,
                            "current_expiration": expiration,
                            "summary": decision_line,
                            "timestamp": datetime.now().isoformat(),
                        })
                    print(f"Logged decision")

                    # Check for roll signal
                    if self._is_roll_signal(response_text, json_data):
                        signal_data = self._build_roll_signal_data(symbol, json_data)
                        append_signal(signal_log_path, signal_data)
                        print(f"⚠️ ROLL SIGNAL logged for {symbol} ${strike} exp {expiration}")

                except Exception as e:
                    logger.error(
                        "Position monitor FAILED for %s strike=%s exp=%s:\n%s",
                        symbol, strike, expiration, traceback.format_exc(),
                    )
                    print(f"Error monitoring {symbol} ${strike} exp {expiration}: {e}")
                    append_decision(decision_log_path, {
                        "error": str(e),
                        "symbol": ticker,
                        "exchange": exchange,
                        "current_strike": strike,
                        "current_expiration": expiration,
                        "timestamp": datetime.now().isoformat(),
                    })

        print(f"\n{'='*60}")
        print(f"Completed {name} monitoring")
        print(f"{'='*60}\n")
