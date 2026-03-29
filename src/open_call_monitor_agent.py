from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider


async def run_open_call_monitor(config, runner: AgentRunner,
                                 cosmos: CosmosDBService,
                                 context_provider: ContextProvider):
    """Run open covered call position monitoring from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
    """
    from .tv_open_call_instructions import TV_OPEN_CALL_INSTRUCTIONS

    print(f"\n{'='*60}")
    print(f"Starting OpenCallMonitor monitoring")
    print(f"{'='*60}")

    call_symbols = cosmos.get_symbols_with_active_positions("call")
    if not call_symbols:
        print("No active call positions — skipping OpenCallMonitor")
        return

    total = sum(len(s["_active_positions"]) for s in call_symbols)
    print(f"Monitoring {total} open call position(s)")

    from .tv_data_fetcher import TradingViewFetcher

    async with TradingViewFetcher() as fetcher:
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
                    context_provider=context_provider,
                    max_activity_entries=config.max_activity_entries,
                    fetcher=fetcher,
                )

    print(f"\n{'='*60}")
    print(f"Completed OpenCallMonitor monitoring")
    print(f"{'='*60}\n")
