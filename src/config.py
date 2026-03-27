import os
import re
import yaml
from typing import Any, Dict


class Config:
    """Configuration loader with environment variable substitution."""
    
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r') as f:
            raw_config = yaml.safe_load(f)
        
        # Prune inactive MCP provider sections before env var substitution,
        # so we don't require env vars for providers that aren't selected.
        raw_config = self._prune_inactive_providers(raw_config)
        
        self.config = self._substitute_env_vars(raw_config)
        self._validate()
    
    def _prune_inactive_providers(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Remove inactive MCP provider sub-sections to avoid requiring their env vars."""
        mcp = config.get('mcp', {})
        provider = mcp.get('provider')
        if not provider:
            return config  # Let _validate() handle the missing provider error
        
        # Keep only the selected provider's sub-section (plus the 'provider' key)
        pruned_mcp = {'provider': provider}
        if provider in mcp:
            pruned_mcp[provider] = mcp[provider]
        
        config = dict(config)
        config['mcp'] = pruned_mcp
        return config
    
    def _substitute_env_vars(self, obj: Any) -> Any:
        """Recursively substitute ${VAR_NAME} with environment variables."""
        if isinstance(obj, dict):
            return {k: self._substitute_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # Match ${VAR_NAME} pattern
            pattern = r'\$\{([^}]+)\}'
            matches = re.findall(pattern, obj)
            result = obj
            for var_name in matches:
                env_value = os.environ.get(var_name, '')
                if not env_value:
                    raise ValueError(f"Environment variable {var_name} not set")
                result = result.replace(f'${{{var_name}}}', env_value)
            return result
        else:
            return obj
    
    def _validate(self):
        """Validate required configuration fields."""
        required_fields = [
            ('azure', 'project_endpoint'),
            ('azure', 'model_deployment'),
            ('scheduler', 'cron'),
        ]
        
        for *path, field in required_fields:
            obj = self.config
            for key in path:
                if key not in obj:
                    raise ValueError(f"Missing required config: {'.'.join(path + [field])}")
                obj = obj[key]
            if field not in obj:
                raise ValueError(f"Missing required config: {'.'.join(path + [field])}")
        
        # Validate MCP provider configuration
        mcp = self.config.get('mcp', {})
        if 'provider' not in mcp:
            raise ValueError(
                "Missing required config: mcp.provider. "
                "Config format has changed — 'mcp' now requires a 'provider' field "
                "and per-provider sub-sections. See config.yaml for the new format."
            )
        
        provider = mcp['provider']
        if provider not in mcp:
            raise ValueError(
                f"MCP provider '{provider}' selected but no 'mcp.{provider}' "
                f"section found in config. Available providers: "
                f"{[k for k in mcp if k != 'provider']}"
            )
        
        provider_cfg = mcp[provider]
        transport = provider_cfg.get('transport', 'stdio')
        if transport == 'streamable_http':
            for field in ('url',):
                if field not in provider_cfg:
                    raise ValueError(f"Missing required config: mcp.{provider}.{field}")
        else:
            for field in ('command', 'args'):
                if field not in provider_cfg:
                    raise ValueError(f"Missing required config: mcp.{provider}.{field}")
    
    @property
    def azure_endpoint(self) -> str:
        return self.config['azure']['project_endpoint']
    
    @property
    def model_deployment(self) -> str:
        return self.config['azure']['model_deployment']
    
    @property
    def mcp_provider(self) -> str:
        """Return the selected MCP provider name (e.g., 'massive' or 'alphavantage')."""
        return self.config['mcp']['provider']
    
    @property
    def _mcp_provider_config(self) -> Dict[str, Any]:
        """Return the config dict for the selected MCP provider."""
        return self.config['mcp'][self.mcp_provider]
    
    @property
    def mcp_transport(self) -> str:
        """Return the transport type for the selected MCP provider (default: 'stdio')."""
        return self._mcp_provider_config.get('transport', 'stdio')
    
    @property
    def mcp_url(self) -> str:
        """Return the URL for HTTP-based MCP providers."""
        return self._mcp_provider_config.get('url', '')
    
    @property
    def mcp_command(self) -> str:
        return self._mcp_provider_config.get('command', '')
    
    @property
    def mcp_args(self) -> list:
        return self._mcp_provider_config.get('args', [])
    
    @property
    def mcp_description(self) -> str:
        return self._mcp_provider_config.get('description', 'MCP server integration')
    
    @property
    def mcp_env_key(self) -> str:
        """Return the environment variable name required by the selected MCP provider, or empty string if none needed."""
        return self._mcp_provider_config.get('env_key', '')
    
    @property
    def cron_expression(self) -> str:
        return self.config['scheduler']['cron']
    
    @property
    def covered_call_config(self) -> Dict[str, str]:
        return self.config['covered_call']
    
    @property
    def cash_secured_put_config(self) -> Dict[str, str]:
        return self.config['cash_secured_put']

    @property
    def open_call_monitor_config(self) -> Dict[str, str]:
        return self.config.get('open_call_monitor', {})

    @property
    def open_put_monitor_config(self) -> Dict[str, str]:
        return self.config.get('open_put_monitor', {})

    @property
    def max_decision_entries(self) -> int:
        return self.config.get('context', {}).get('max_decision_entries', 20)

    @property
    def max_signal_entries(self) -> int:
        return self.config.get('context', {}).get('max_signal_entries', 10)
