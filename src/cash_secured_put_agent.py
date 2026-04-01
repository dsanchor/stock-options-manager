from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider
from .tv_cash_secured_put_instructions import TV_CASH_SECURED_PUT_INSTRUCTIONS


async def run_cash_secured_put_analysis(config, runner: AgentRunner,
                                         cosmos: CosmosDBService,
                                         context_provider: ContextProvider):
    """Run cash secured put analysis for all enabled symbols from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
    """
    print(f"\n{'='*60}")
    print(f"Starting CashSecuredPutAgent analysis")
    print(f"{'='*60}")

    csp_symbols = cosmos.get_cash_secured_put_symbols()
    if not csp_symbols:
        print("No symbols enabled for cash-secured put — skipping")
        return

    symbol_names = [s["symbol"] for s in csp_symbols]
    print(f"Analyzing {len(csp_symbols)} symbols: {', '.join(symbol_names)}")

    from .tv_data_fetcher import create_fetcher

    async with create_fetcher(config) as fetcher:
        for sym_doc in csp_symbols:
            await runner.run_symbol_agent(
                name="CashSecuredPutAgent",
                instructions=TV_CASH_SECURED_PUT_INSTRUCTIONS,
                symbol=sym_doc["symbol"],
                exchange=sym_doc["exchange"],
                agent_type="cash_secured_put",
                cosmos=cosmos,
                context_provider=context_provider,
                max_activity_entries=config.max_activity_entries,
                fetcher=fetcher,
            )

    print(f"\n{'='*60}")
    print(f"Completed CashSecuredPutAgent analysis")
    print(f"{'='*60}\n")
