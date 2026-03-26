import sys
import time
import signal
import asyncio
import schedule
from datetime import datetime

from .config import Config
from .agent_runner import AgentRunner
from .covered_call_agent import run_covered_call_analysis
from .cash_secured_put_agent import run_cash_secured_put_analysis


class OptionsAgentScheduler:
    """Main scheduler for periodic options agent execution."""
    
    def __init__(self):
        self.running = True
        self.config = None
        self.runner = None
    
    def setup(self):
        """Initialize configuration and agent runner."""
        print("Loading configuration...")
        self.config = Config()
        
        print("Initializing Agent Framework Runner...")
        self.runner = AgentRunner(
            project_endpoint=self.config.azure_endpoint,
            model=self.config.model_deployment,
            mcp_command=self.config.mcp_command,
            mcp_args=self.config.mcp_args,
            mcp_description=self.config.mcp_description,
        )
        
        print(f"Scheduler configured: Running every {self.config.interval_minutes} minutes")
    
    def run_all_agents(self):
        """Execute both agents (bridges async to sync for scheduler)."""
        asyncio.run(self._run_all_agents_async())
    
    async def _run_all_agents_async(self):
        """Execute both covered call and cash secured put agents asynchronously."""
        print(f"\n{'#'*70}")
        print(f"# Starting scheduled agent run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*70}\n")
        
        try:
            # Run covered call agent
            await run_covered_call_analysis(self.config, self.runner)
            
            # Run cash secured put agent
            await run_cash_secured_put_analysis(self.config, self.runner)
            
        except Exception as e:
            print(f"ERROR during agent execution: {str(e)}")
        
        print(f"\n{'#'*70}")
        print(f"# Completed scheduled agent run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*70}\n")
    
    def schedule_agents(self):
        """Set up periodic scheduling."""
        # Schedule periodic execution
        schedule.every(self.config.interval_minutes).minutes.do(self.run_all_agents)
        
        print(f"Agents scheduled to run every {self.config.interval_minutes} minutes")
        print("Press Ctrl+C to stop\n")
    
    def signal_handler(self, sig, frame):
        """Handle graceful shutdown on Ctrl+C."""
        print("\n\nShutdown signal received. Stopping scheduler...")
        self.running = False
    
    def run(self):
        """Main execution loop."""
        # Set up signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initialize
        self.setup()
        
        # Run immediately on start
        print("Running agents immediately on startup...")
        self.run_all_agents()
        
        # Set up periodic schedule
        self.schedule_agents()
        
        # Main loop
        while self.running:
            schedule.run_pending()
            time.sleep(1)
        
        print("Scheduler stopped. Goodbye!")


def main():
    """Entry point for the options agent scheduler."""
    print("="*70)
    print(" Options Trading Agent Scheduler")
    print(" Using Microsoft Agent Framework + MCP Integration")
    print("="*70)
    print()
    
    try:
        scheduler = OptionsAgentScheduler()
        scheduler.run()
    except Exception as e:
        print(f"FATAL ERROR: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
