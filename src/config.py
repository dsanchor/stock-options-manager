import os
import re
import yaml
from typing import Any, Dict


class Config:
    """Configuration loader with environment variable substitution."""
    
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r') as f:
            raw_config = yaml.safe_load(f)
        
        self.config = self._substitute_env_vars(raw_config)
        self._validate()
    
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
            ('azure', 'api_key'),
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
        

    
    @property
    def azure_endpoint(self) -> str:
        return self.config['azure']['project_endpoint']
    
    @property
    def model_deployment(self) -> str:
        return self.config['azure']['model_deployment']
    
    @property
    def api_key(self) -> str:
        return self.config['azure']['api_key']
    
    @property
    def cron_expression(self) -> str:
        return self.config['scheduler']['cron']

    @cron_expression.setter
    def cron_expression(self, value: str):
        self.config['scheduler']['cron'] = value
    
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
