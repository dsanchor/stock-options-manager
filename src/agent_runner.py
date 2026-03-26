import asyncio
import os
from typing import List
from agent_framework import ChatAgent, MCPStdioTool, MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity import AzureCliCredential

from .logger import read_decision_log, append_decision, append_signal


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
        # Try to find a formatted decision line in the response
        for line in response_text.split('\n'):
            if symbol in line and ('SELL' in line.upper() or 'WAIT' in line.upper()):
                return line.strip()
        
        # Fallback: create a summary
        decision = "SELL" if "SELL" in response_text.upper() and "CLEAR SELL SIGNAL" in response_text.upper() else "WAIT"
        reason = response_text[:100].replace('\n', ' ').strip()
        return f"{symbol} | DECISION: {decision} | Reason: {reason}"
    
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
        
        # Validate API key is set
        if not os.environ.get(self.mcp_env_key):
            raise RuntimeError(
                f"{self.mcp_env_key} environment variable is not set. "
                f"Export it before running: export {self.mcp_env_key}='your-key'"
            )
        
        # Create MCP tool based on transport type
        if self.mcp_transport == "streamable_http":
            mcp_tool = MCPStreamableHTTPTool(
                name=self.mcp_provider,
                url=self.mcp_url,
                description=self.mcp_description,
                approval_mode="never_require",
            )
        else:
            mcp_env = os.environ.copy()
            mcp_tool = MCPStdioTool(
                name=self.mcp_provider,
                command=self.mcp_command,
                args=self.mcp_args,
                description=self.mcp_description,
                approval_mode="never_require",
                env=mcp_env,
            )
        
        # Use context manager for proper MCP cleanup
        async with mcp_tool:
            agent = ChatAgent(
                chat_client=self.client,
                name=name,
                instructions=instructions,
                tools=mcp_tool,
            )
            
            for symbol in symbols:
                print(f"\n--- Analyzing {symbol} ---")
                
                try:
                    # Build message with context
                    message = f"""Analyze {symbol} and provide a trading decision.

Previous decisions for context:
{previous_decisions}

Analyze {symbol} NOW. Use the available MCP tools to gather current data. Provide your decision in the required output format."""

                    # Run agent (async)
                    result = await agent.run(message)
                    response_text = result.text or str(result)
                    
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
                    error_msg = f"{symbol} | DECISION: ERROR | Reason: {str(e)}"
                    print(f"Error analyzing {symbol}: {e}")
                    append_decision(decision_log_path, error_msg)
        
        print(f"\n{'='*60}")
        print(f"Completed {name} analysis")
        print(f"{'='*60}\n")
