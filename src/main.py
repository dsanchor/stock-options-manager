import sys
import time
import signal
import asyncio
from datetime import datetime

from croniter import croniter

from .config import Config
from .agent_runner import AgentRunner
from .covered_call_agent import run_covered_call_analysis
from .cash_secured_put_agent import run_cash_secured_put_analysis
from .open_call_monitor_agent import run_open_call_monitor
from .open_put_monitor_agent import run_open_put_monitor


class OptionsAgentScheduler:
    """Main scheduler for cron-based options agent execution."""
    
    def __init__(self):
        self.running = True
        self.config = None
        self.runner = None
        self._cron_changed = False
    
    def reschedule(self, new_cron: str):
        """Update cron expression. The run loop will pick it up on next iteration."""
        self.config.cron_expression = new_cron
        self._cron_changed = True
    
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
        
        print(f"Scheduler configured with cron: {self.config.cron_expression}")
    
    def run_all_agents(self):
        """Execute all agents (bridges async to sync for scheduler)."""
        asyncio.run(self._run_all_agents_async())
    
    async def _run_all_agents_async(self):
        """Execute all agents asynchronously."""
        print(f"\n{'#'*70}")
        print(f"# Starting scheduled agent run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*70}\n")
        
        try:
            # Run covered call agent
            await run_covered_call_analysis(self.config, self.runner)
            
            # Run cash secured put agent
            await run_cash_secured_put_analysis(self.config, self.runner)

            # Run open position monitors (skip gracefully if no active positions)
            await run_open_call_monitor(self.config, self.runner)
            await run_open_put_monitor(self.config, self.runner)
            
        except Exception as e:
            print(f"ERROR during agent execution: {str(e)}")
        
        print(f"\n{'#'*70}")
        print(f"# Completed scheduled agent run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#'*70}\n")
    
    def signal_handler(self, sig, frame):
        """Handle graceful shutdown on Ctrl+C."""
        print("\n\nShutdown signal received. Stopping scheduler...")
        self.running = False
    
    def run(self, install_signals=True):
        """Main execution loop using cron expression.
        
        Args:
            install_signals: Install SIGINT/SIGTERM handlers. Set to False when
                running inside a thread (signals can only be set in the main thread).
        """
        if install_signals:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        
        self.setup()
        
        cron = croniter(self.config.cron_expression, datetime.now())
        
        # Schedule via cron
        next_run = cron.get_next(datetime)
        print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print("Press Ctrl+C to stop\n")
        
        while self.running:
            # Check if cron was updated from the web UI
            if self._cron_changed:
                self._cron_changed = False
                cron = croniter(self.config.cron_expression, datetime.now())
                next_run = cron.get_next(datetime)
                print(f"Cron rescheduled to: {self.config.cron_expression}")
                print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}\n")

            now = datetime.now()
            if now >= next_run:
                self.run_all_agents()
                next_run = cron.get_next(datetime)
                print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}\n")
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
    print("TIP: Use 'python run.py' to start both web dashboard and scheduler.")
    print("     Use 'python run.py --scheduler-only' for scheduler only.")
    print()
    main()
