from .agent_runner import AgentRunner
from .cash_secured_put_instructions import CASH_SECURED_PUT_INSTRUCTIONS


async def run_cash_secured_put_analysis(config, runner: AgentRunner):
    """Run cash secured put analysis for configured symbols.
    
    Args:
        config: Configuration object with cash secured put settings
        runner: Initialized AgentRunner instance
    """
    csp_config = config.cash_secured_put_config
    
    # Select instructions based on MCP provider
    provider = config.mcp_provider
    if provider == "alphavantage":
        from .av_cash_secured_put_instructions import AV_CASH_SECURED_PUT_INSTRUCTIONS
        instructions = AV_CASH_SECURED_PUT_INSTRUCTIONS
    elif provider == "yahoo":
        from .yf_cash_secured_put_instructions import YF_CASH_SECURED_PUT_INSTRUCTIONS
        instructions = YF_CASH_SECURED_PUT_INSTRUCTIONS
    elif provider == "tradingview":
        from .tv_cash_secured_put_instructions import TV_CASH_SECURED_PUT_INSTRUCTIONS
        instructions = TV_CASH_SECURED_PUT_INSTRUCTIONS
    else:
        instructions = CASH_SECURED_PUT_INSTRUCTIONS
    
    await runner.run_agent(
        name="CashSecuredPutAgent",
        instructions=instructions,
        symbols_file=csp_config['symbols_file'],
        decision_log_path=csp_config['decision_log'],
        signal_log_path=csp_config['signal_log']
    )
