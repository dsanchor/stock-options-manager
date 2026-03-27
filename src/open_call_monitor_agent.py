from .agent_runner import AgentRunner


async def run_open_call_monitor(config, runner: AgentRunner):
    """Run open covered call position monitoring (TradingView only).

    Args:
        config: Configuration object with open call monitor settings
        runner: Initialized AgentRunner instance
    """
    provider = config.mcp_provider
    if provider != "tradingview":
        print(
            f"WARNING: Open Call Monitor only supports TradingView provider "
            f"(current: {provider}). Skipping."
        )
        return

    from .tv_open_call_instructions import TV_OPEN_CALL_INSTRUCTIONS

    ocm_config = config.open_call_monitor_config

    await runner.run_position_monitor_agent(
        name="OpenCallMonitor",
        instructions=TV_OPEN_CALL_INSTRUCTIONS,
        positions_file=ocm_config['positions_file'],
        decision_log_path=ocm_config['decision_log'],
        signal_log_path=ocm_config['signal_log'],
        position_type="call",
        max_decision_entries=config.max_decision_entries,
        max_signal_entries=config.max_signal_entries,
    )
