import sys
import time
import signal
import asyncio
from datetime import datetime
import pytz

from croniter import croniter

from .config import Config
from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider
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
        self.cosmos = None
        self.context_provider = None
        self._cron_changed = False
        self._summary_cron_changed = False
        self._last_config_reload = None
        self._config_reload_interval = 60  # seconds
    
    def reschedule(self, new_cron: str, new_timezone: str = None):
        """Update cron expression and/or timezone. The run loop will pick it up on next iteration."""
        self.config.cron_expression = new_cron
        if new_timezone:
            self.config.timezone = new_timezone
        self._cron_changed = True
    
    def reschedule_summary(self, new_cron: str):
        """Update summary agent cron expression. The run loop will pick it up on next iteration."""
        summary_config = self.config.config.get('summary_agent', {})
        summary_config['cron'] = new_cron
        self.config.config['summary_agent'] = summary_config
        self._summary_cron_changed = True
    
    def setup(self):
        """Initialize configuration, CosmosDB, and agent runner."""
        print("Loading configuration...")
        self.config = Config()
        
        print("Initializing CosmosDB service...")
        self.cosmos = CosmosDBService(
            endpoint=self.config.cosmosdb_endpoint,
            key=self.config.cosmosdb_key,
            database_name=self.config.cosmosdb_database,
        )
        self.context_provider = ContextProvider(self.cosmos)

        # Merge config.yaml defaults into CosmosDB (first-run seed + new keys)
        settings_defaults = {
            k: v for k, v in self.config.config.items()
            if k not in ('azure', 'cosmosdb')
        }
        merged_settings = self.cosmos.merge_defaults(settings_defaults)
        
        # Update Config object with merged settings from CosmosDB (CosmosDB takes precedence)
        if merged_settings:
            for key, value in merged_settings.items():
                if key not in ('azure', 'cosmosdb'):
                    self.config.config[key] = value

        from .telegram_notifier import TelegramNotifier
        telegram_notifier = TelegramNotifier(cosmos=self.cosmos)

        print("Initializing Agent Framework Runner...")
        self.runner = AgentRunner(
            project_endpoint=self.config.azure_endpoint,
            model=self.config.model_deployment,
            api_key=self.config.api_key,
            telegram_notifier=telegram_notifier,
        )
        
        print(f"Scheduler configured with cron: {self.config.cron_expression}")
        print(f"Scheduler timezone: {self.config.timezone}")
        
        # Log summary agent configuration
        summary_config = self.config.config.get('summary_agent', {})
        summary_enabled = summary_config.get('enabled', True)
        summary_cron = summary_config.get('cron', '0 8 * * *')
        summary_activity_count = summary_config.get('activity_count', 3)
        
        print(f"\nSummary Agent Configuration:")
        print(f"  Enabled: {summary_enabled}")
        if summary_enabled:
            print(f"  Cron: {summary_cron}")
            print(f"  Timezone: {self.config.timezone}")
            print(f"  Activity count: {summary_activity_count}")
        else:
            print(f"  Status: Disabled in config")
    
    def run_all_agents(self):
        """Execute all agents (bridges async to sync for scheduler)."""
        asyncio.run(self._run_all_agents_async())
    
    async def _run_all_agents_async(self):
        """Execute all agents asynchronously."""
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'#'*70}")
        print(f"# Starting scheduled agent run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'#'*70}\n")
        
        try:
            cosmos = self.cosmos
            ctx = self.context_provider
            runner = self.runner
            config = self.config

            # Run covered call agent
            await run_covered_call_analysis(config, runner, cosmos, ctx)
            
            # Run cash secured put agent
            await run_cash_secured_put_analysis(config, runner, cosmos, ctx)

            # Run open position monitors
            await run_open_call_monitor(config, runner, cosmos, ctx)
            await run_open_put_monitor(config, runner, cosmos, ctx)
            
        except Exception as e:
            print(f"ERROR during agent execution: {str(e)}")
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'#'*70}")
        print(f"# Completed scheduled agent run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'#'*70}\n")
    
    def run_summary_agent_job(self):
        """Execute summary agent (bridges async to sync for scheduler)."""
        asyncio.run(self._run_summary_agent_async())
    
    async def _run_summary_agent_async(self):
        """Run summary agent if enabled in config."""
        summary_config = self.config.config.get('summary_agent', {})
        if not summary_config.get('enabled', True):
            print("⏭️  Summary agent disabled in config")
            return
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'='*70}")
        print(f"📊 Summary Agent - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*70}\n")
        
        activity_count = summary_config.get('activity_count', 3)
        await self.runner.run_summary_agent(
            cosmos=self.cosmos,
            telegram_notifier=self.runner.telegram_notifier,
            activity_count=activity_count
        )
    
    def signal_handler(self, sig, frame):
        """Handle graceful shutdown on Ctrl+C."""
        print("\n\nShutdown signal received. Stopping scheduler...")
        self.running = False
    
    def _reload_config_from_cosmos(self):
        """Reload settings from CosmosDB and detect changes to cron/timezone.
        
        This method is called periodically to pick up configuration changes
        made through the web UI without requiring a scheduler restart.
        """
        try:
            cosmos_settings = self.cosmos.get_settings()
            if not cosmos_settings:
                return
            
            # Track if we need to update anything
            main_cron_changed = False
            summary_cron_changed = False
            timezone_changed = False
            
            # Check scheduler settings
            scheduler_settings = cosmos_settings.get('scheduler', {})
            new_cron = scheduler_settings.get('cron')
            new_timezone = scheduler_settings.get('timezone')
            
            if new_cron and new_cron != self.config.cron_expression:
                self.config.cron_expression = new_cron
                main_cron_changed = True
            
            if new_timezone and new_timezone != self.config.timezone:
                old_timezone = self.config.timezone
                self.config.timezone = new_timezone
                timezone_changed = True
                # If timezone changed, recalculate both schedules
                if not main_cron_changed:
                    main_cron_changed = True
            
            # Check summary agent settings
            summary_settings = cosmos_settings.get('summary_agent', {})
            new_summary_cron = summary_settings.get('cron')
            current_summary_cron = self.config.config.get('summary_agent', {}).get('cron', '0 8 * * *')
            
            if new_summary_cron and new_summary_cron != current_summary_cron:
                if 'summary_agent' not in self.config.config:
                    self.config.config['summary_agent'] = {}
                self.config.config['summary_agent']['cron'] = new_summary_cron
                summary_cron_changed = True
            
            # Update other summary agent settings
            if summary_settings:
                if 'summary_agent' not in self.config.config:
                    self.config.config['summary_agent'] = {}
                for key in ['enabled', 'activity_count']:
                    if key in summary_settings:
                        self.config.config['summary_agent'][key] = summary_settings[key]
            
            # Set flags for the main loop to pick up
            if main_cron_changed:
                self._cron_changed = True
                if timezone_changed:
                    print(f"✓ Config reloaded from CosmosDB: timezone changed to {new_timezone}")
                if new_cron:
                    print(f"✓ Config reloaded from CosmosDB: monitor cron changed to {new_cron}")
            
            if summary_cron_changed:
                self._summary_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: summary cron changed to {new_summary_cron}")
                
        except Exception as e:
            # Don't crash the scheduler on config reload errors
            print(f"⚠️  Error reloading config from CosmosDB: {e}")
    
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
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        
        # Initialize main scheduler cron
        cron = croniter(self.config.cron_expression, now_tz)
        next_run = cron.get_next(datetime)
        
        # Initialize summary agent cron (if enabled)
        summary_config = self.config.config.get('summary_agent', {})
        summary_enabled = summary_config.get('enabled', True)
        summary_cron_expr = summary_config.get('cron', '0 8 * * *')
        summary_next_run = None
        summary_cron = None
        
        if summary_enabled:
            try:
                summary_cron = croniter(summary_cron_expr, now_tz)
                summary_next_run = summary_cron.get_next(datetime)
                print(f"\nMonitor Agents - Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                print(f"Summary Agent  - Next run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid summary agent cron expression '{summary_cron_expr}': {e}")
                print(f"⚠️  Summary agent scheduling disabled")
                summary_enabled = False
        else:
            print(f"\nMonitor Agents - Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"Summary Agent  - Disabled")
        
        # Track when we last reloaded config
        self._last_config_reload = time.time()
        
        print("Press Ctrl+C to stop\n")
        
        while self.running:
            # Periodically reload config from CosmosDB to pick up web UI changes
            current_time = time.time()
            if current_time - self._last_config_reload >= self._config_reload_interval:
                self._reload_config_from_cosmos()
                self._last_config_reload = current_time
            
            # Check if main cron was updated from the web UI
            if self._cron_changed:
                self._cron_changed = False
                tz = pytz.timezone(self.config.timezone)
                now_tz = datetime.now(tz)
                cron = croniter(self.config.cron_expression, now_tz)
                next_run = cron.get_next(datetime)
                print(f"Monitor agents cron rescheduled to: {self.config.cron_expression}")
                print(f"Timezone: {self.config.timezone}")
                print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check if summary cron was updated from the web UI
            if self._summary_cron_changed:
                self._summary_cron_changed = False
                summary_config = self.config.config.get('summary_agent', {})
                summary_cron_expr = summary_config.get('cron', '0 8 * * *')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    summary_cron = croniter(summary_cron_expr, now_tz)
                    summary_next_run = summary_cron.get_next(datetime)
                    summary_enabled = summary_config.get('enabled', True)
                    print(f"Summary agent cron rescheduled to: {summary_cron_expr}")
                    print(f"Next scheduled run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid summary agent cron expression '{summary_cron_expr}': {e}")
                    summary_enabled = False

            now_tz = datetime.now(tz)
            
            # Check main scheduler
            if now_tz >= next_run:
                self.run_all_agents()
                next_run = cron.get_next(datetime)
                print(f"Monitor Agents - Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check summary agent scheduler
            if summary_enabled and summary_next_run and now_tz >= summary_next_run:
                self.run_summary_agent_job()
                summary_next_run = summary_cron.get_next(datetime)
                print(f"Summary Agent  - Next run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            time.sleep(1)
        
        print("Scheduler stopped. Goodbye!")


def main():
    """Entry point for the options agent scheduler."""
    print("="*70)
    print(" Stock Options Manager Scheduler")
    print(" Using Microsoft Agent Framework + Playwright")
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
