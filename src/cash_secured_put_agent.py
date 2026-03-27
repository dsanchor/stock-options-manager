from .agent_runner import AgentRunner
from .tv_cash_secured_put_instructions import TV_CASH_SECURED_PUT_INSTRUCTIONS


async def run_cash_secured_put_analysis(config, runner: AgentRunner):
    """Run cash secured put analysis for configured symbols.
    
    Args:
        config: Configuration object with cash secured put settings
        runner: Initialized AgentRunner instance
    """
    csp_config = config.cash_secured_put_config
    
    await runner.run_agent(
        name="CashSecuredPutAgent",
        instructions=TV_CASH_SECURED_PUT_INSTRUCTIONS,
        symbols_file=csp_config['symbols_file'],
        decision_log_path=csp_config['decision_log'],
        signal_log_path=csp_config['signal_log'],
        max_decision_entries=config.max_decision_entries,
        max_signal_entries=config.max_signal_entries,
    )
