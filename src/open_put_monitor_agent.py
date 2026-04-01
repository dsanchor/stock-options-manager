from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider


async def run_open_put_monitor(config, runner: AgentRunner,
                                cosmos: CosmosDBService,
                                context_provider: ContextProvider):
    """Run open cash-secured put position monitoring from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
    """
    from .tv_open_put_instructions import TV_OPEN_PUT_INSTRUCTIONS

    print(f"\n{'='*60}")
    print(f"Starting OpenPutMonitor monitoring")
    print(f"{'='*60}")

    put_symbols = cosmos.get_symbols_with_active_positions("put")
    if not put_symbols:
        print("No active put positions — skipping OpenPutMonitor")
        return

    total = sum(len(s["_active_positions"]) for s in put_symbols)
    print(f"Monitoring {total} open put position(s)")

    from .tv_data_fetcher import create_fetcher

    async with create_fetcher(config) as fetcher:
        for sym_doc in put_symbols:
            for pos in sym_doc["_active_positions"]:
                await runner.run_position_monitor(
                    name="OpenPutMonitor",
                    instructions=TV_OPEN_PUT_INSTRUCTIONS,
                    symbol=sym_doc["symbol"],
                    exchange=sym_doc["exchange"],
                    position=pos,
                    agent_type="open_put_monitor",
                    cosmos=cosmos,
                    context_provider=context_provider,
                    max_activity_entries=config.max_activity_entries,
                    fetcher=fetcher,
                )

    print(f"\n{'='*60}")
    print(f"Completed OpenPutMonitor monitoring")
    print(f"{'='*60}\n")
