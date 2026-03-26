from .agent_runner import AgentRunner
from .cash_secured_put_instructions import CASH_SECURED_PUT_INSTRUCTIONS


async def run_cash_secured_put_analysis(config, runner: AgentRunner):
    """Run cash secured put analysis for configured symbols.
    
    Args:
        config: Configuration object with cash secured put settings
        runner: Initialized AgentRunner instance
    """
    csp_config = config.cash_secured_put_config
    
    await runner.run_agent(
        name="CashSecuredPutAgent",
        instructions=CASH_SECURED_PUT_INSTRUCTIONS,
        symbols_file=csp_config['symbols_file'],
        decision_log_path=csp_config['decision_log'],
        signal_log_path=csp_config['signal_log']
    )
