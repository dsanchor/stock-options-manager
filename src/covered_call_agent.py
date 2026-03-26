from .agent_runner import AgentRunner
from .covered_call_instructions import COVERED_CALL_INSTRUCTIONS


async def run_covered_call_analysis(config, runner: AgentRunner):
    """Run covered call analysis for configured symbols.
    
    Args:
        config: Configuration object with covered call settings
        runner: Initialized AgentRunner instance
    """
    cc_config = config.covered_call_config
    
    await runner.run_agent(
        name="CoveredCallAgent",
        instructions=COVERED_CALL_INSTRUCTIONS,
        symbols_file=cc_config['symbols_file'],
        decision_log_path=cc_config['decision_log'],
        signal_log_path=cc_config['signal_log']
    )
