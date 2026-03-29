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

    def _validate(self) -> None:
        """Validate required configuration fields."""
        required_fields = [
            ('azure', 'project_endpoint'),
            ('azure', 'model_deployment'),
            ('azure', 'api_key'),
            ('cosmosdb', 'endpoint'),
            ('cosmosdb', 'key'),
            ('scheduler', 'cron'),
        ]

        for *path, field in required_fields:
            obj = self.config
            for key in path:
                if key not in obj:
                    raise ValueError(
                        f"Missing required config: {'.'.join(path + [field])}"
                    )
                obj = obj[key]
            if field not in obj:
                raise ValueError(
                    f"Missing required config: {'.'.join(path + [field])}"
                )

    # ── Azure ──────────────────────────────────────────────────────────

    @property
    def azure_endpoint(self) -> str:
        return self.config['azure']['project_endpoint']

    @property
    def model_deployment(self) -> str:
        return self.config['azure']['model_deployment']

    @property
    def api_key(self) -> str:
        return self.config['azure']['api_key']

    # ── CosmosDB ───────────────────────────────────────────────────────

    @property
    def cosmosdb_endpoint(self) -> str:
        return self.config['cosmosdb']['endpoint']

    @property
    def cosmosdb_key(self) -> str:
        return self.config['cosmosdb']['key']

    @property
    def cosmosdb_database(self) -> str:
        return self.config.get('cosmosdb', {}).get(
            'database', 'stock-options-manager'
        )

    # ── Scheduler ──────────────────────────────────────────────────────

    @property
    def cron_expression(self) -> str:
        return self.config['scheduler']['cron']

    @cron_expression.setter
    def cron_expression(self, value: str):
        self.config['scheduler']['cron'] = value

    # ── Context ────────────────────────────────────────────────────────

    @property
    def max_activity_entries(self) -> int:
        """Recent activities for context injection (0=none, max 5). Default 2."""
        val = self.config.get('context', {}).get('max_activity_entries', 2)
        return max(0, min(5, val))

    @property
    def activity_ttl_days(self) -> int:
        return self.config.get('context', {}).get('activity_ttl_days', 90)
