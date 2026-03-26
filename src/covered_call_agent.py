from .agent_runner import AgentRunner
from .covered_call_instructions import COVERED_CALL_INSTRUCTIONS


async def run_covered_call_analysis(config, runner: AgentRunner):
    """Run covered call analysis for configured symbols.
    
    Args:
        config: Configuration object with covered call settings
        runner: Initialized AgentRunner instance
    """
    cc_config = config.covered_call_config
    
    # Select instructions based on MCP provider
    provider = config.mcp_provider
    if provider == "alphavantage":
        from .av_covered_call_instructions import AV_COVERED_CALL_INSTRUCTIONS
        instructions = AV_COVERED_CALL_INSTRUCTIONS
    elif provider == "yahoo":
        from .yf_covered_call_instructions import YF_COVERED_CALL_INSTRUCTIONS
        instructions = YF_COVERED_CALL_INSTRUCTIONS
    elif provider == "tradingview":
        from .tv_covered_call_instructions import TV_COVERED_CALL_INSTRUCTIONS
        instructions = TV_COVERED_CALL_INSTRUCTIONS
    else:
        instructions = COVERED_CALL_INSTRUCTIONS
    
    await runner.run_agent(
        name="CoveredCallAgent",
        instructions=instructions,
        symbols_file=cc_config['symbols_file'],
        decision_log_path=cc_config['decision_log'],
        signal_log_path=cc_config['signal_log']
    )
