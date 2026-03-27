import asyncio
import logging
import os
import re
import traceback
from typing import List
from agent_framework import ChatAgent, MCPStdioTool, MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity import AzureCliCredential

from .logger import read_decision_log, append_decision, append_signal

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
    
    def _extract_decision_line(self, symbol: str, response_text: str) -> str:
        """Extract a concise decision line from the agent response."""
        # Parse ticker from exchange-symbol format for matching
        ticker = symbol.split('-', 1)[1] if '-' in symbol else symbol
        for line in response_text.split('\n'):
            if ticker in line and ('SELL' in line.upper() or 'WAIT' in line.upper()):
                return line.strip()
        
        # Fallback: create a summary
        decision = "SELL" if "SELL" in response_text.upper() and "CLEAR SELL SIGNAL" in response_text.upper() else "WAIT"
        reason = response_text[:100].replace('\n', ' ').strip()
        return f"{ticker} | DECISION: {decision} | Reason: {reason}"
    
    def _is_sell_signal(self, response_text: str) -> bool:
        """Check if response indicates a clear sell signal."""
        upper = response_text.upper()
        return "CLEAR SELL SIGNAL" in upper or "🚨" in response_text or "SIGNAL: SELL" in upper
    
    async def run_agent(self, name: str, instructions: str, symbols_file: str, 
                       decision_log_path: str, signal_log_path: str):
        """Run agent analysis for all symbols.
        
        Args:
            name: Agent name
            instructions: Base instructions for the agent
            symbols_file: Path to file containing symbols (one per line)
            decision_log_path: Path to decision log
            signal_log_path: Path to signal log
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
        
        # Read previous decision log for context
        previous_decisions = read_decision_log(decision_log_path, max_entries=20)
        
        # Validate API key is set (skip for providers that don't need one, e.g. Yahoo Finance)
        if self.mcp_env_key and not os.environ.get(self.mcp_env_key):
            raise RuntimeError(
                f"{self.mcp_env_key} environment variable is not set. "
                f"Export it before running: export {self.mcp_env_key}='your-key'"
            )
        
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
            # Tool discovery – agent_framework does not currently expose a
            # public method to list available MCP tools after connection.
            # If a `.tools` attribute or `list_tools()` method is added in the
            # future, log them here for diagnostics.
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
                    # Parse exchange-symbol format (e.g., "NYSE-AA" → exchange="NYSE", ticker="AA")
                    if '-' in symbol:
                        exchange, ticker = symbol.split('-', 1)
                    else:
                        exchange, ticker = "", symbol

                    message = f"""Analyze {ticker} (exchange: {exchange}, full symbol: {symbol}) and provide a trading decision.

Previous decisions for context:
{previous_decisions}

Analyze {ticker} NOW. Use the available MCP tools to gather current data. The full exchange-symbol is {symbol}. Provide your decision in the required output format."""

                    # Run agent (async)
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

                    # Log decision (extract the 1-liner from the response)
                    decision_line = self._extract_decision_line(symbol, response_text)
                    append_decision(decision_log_path, decision_line)
                    print(f"Logged decision")

                    # Check for sell signal
                    if self._is_sell_signal(response_text):
                        append_signal(signal_log_path, decision_line)
                        print(f"⚠️ SELL SIGNAL logged for {symbol}")

                except Exception as e:
                    logger.error(
                        "agent.run() FAILED for %s:\n%s",
                        symbol, traceback.format_exc(),
                    )
                    error_msg = f"{symbol} | DECISION: ERROR | Reason: {str(e)}"
                    print(f"Error analyzing {symbol}: {e}")
                    append_decision(decision_log_path, error_msg)

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
