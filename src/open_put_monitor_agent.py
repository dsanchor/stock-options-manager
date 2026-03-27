from .agent_runner import AgentRunner


async def run_open_put_monitor(config, runner: AgentRunner):
    """Run open cash-secured put position monitoring (TradingView only).

    Args:
        config: Configuration object with open put monitor settings
        runner: Initialized AgentRunner instance
    """
    provider = config.mcp_provider
    if provider != "tradingview":
        print(
            f"WARNING: Open Put Monitor only supports TradingView provider "
            f"(current: {provider}). Skipping."
        )
        return

    from .tv_open_put_instructions import TV_OPEN_PUT_INSTRUCTIONS

    opm_config = config.open_put_monitor_config

    await runner.run_position_monitor_agent(
        name="OpenPutMonitor",
        instructions=TV_OPEN_PUT_INSTRUCTIONS,
        positions_file=opm_config['positions_file'],
        decision_log_path=opm_config['decision_log'],
        signal_log_path=opm_config['signal_log'],
        position_type="put",
        max_decision_entries=config.max_decision_entries,
        max_signal_entries=config.max_signal_entries,
    )
