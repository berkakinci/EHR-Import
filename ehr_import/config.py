"""
Configuration and data directory management.

Public config (client ID, redirect URI, providers) lives in config.json (tracked in git).
Private overrides (data_dir) live in .env (gitignored).
Private data (tokens, DB, raw responses) stored in a configurable directory,
separate from source code (default: ../EHR Import Private/).

Usage:
    from ehr_import import config
    db = sqlite3.connect(config.db_path)
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project directory (parent of this package)
project_dir = Path(__file__).parent.parent.resolve()
load_dotenv(project_dir / ".env")

# Load public config
with open(project_dir / "config.json") as f:
    _config = json.load(f)

# Data directory: configurable, defaults to sibling "EHR Import Private"
_data_dir_env = os.getenv("DATA_DIR")
if _data_dir_env:
    data_dir = Path(_data_dir_env).resolve()
else:
    data_dir = (project_dir / ".." / "EHR Import Private").resolve()

# Ensure data directory exists
data_dir.mkdir(parents=True, exist_ok=True)

# Derived paths
db_path = data_dir / "ehr_data.db"
token_store = data_dir / "tokens.json"
endpoints_file = data_dir / "discovered_endpoints.json"
raw_pulls_dir = data_dir / "raw_pulls"
jwk_private_key_path = data_dir / "jwk_private.pem"

# App config (public — safe to commit)
_active_app = _config.get("active_app", "public")
_apps = _config.get("apps", {})
_active_app_config = _apps.get(_active_app, {})

client_id = _active_app_config.get("client_id", _config.get("client_id", ""))
non_production_client_id = _active_app_config.get("non_production_client_id", _config.get("non_production_client_id", ""))
auth_methods = _active_app_config["auth_methods"]
redirect_uri = _config["redirect_uri"]
providers = _config.get("providers", {})


def get_client_id(provider_name: str) -> str:
    """Return the appropriate client ID for a provider.

    Uses non-production client ID if the provider is flagged with
    "non_production": true in config.json providers section.
    Otherwise uses the production client ID.
    """
    provider_config = providers.get(provider_name, {})
    if provider_config.get("non_production"):
        return non_production_client_id
    return client_id


def print_config():
    """Print current configuration for debugging."""
    print(f"Project directory: {project_dir}")
    print(f"Data directory:    {data_dir}")
    print(f"Database:          {db_path}")
    print(f"Token store:       {token_store}")
    print(f"Endpoints file:    {endpoints_file}")
    print(f"Raw pulls:         {raw_pulls_dir}")
    print(f"Client ID (prod):  {client_id}")
    print(f"Client ID (non-prod): {non_production_client_id}")
    print(f"Redirect URI:      {redirect_uri}")
    print(f"JWK private key:   {jwk_private_key_path} ({'exists' if jwk_private_key_path.exists() else 'missing'})")
    print(f"Client secret:     {data_dir / 'client_secret.txt'} ({'exists' if (data_dir / 'client_secret.txt').exists() else 'missing'})")
    print(f"Providers:         {list(providers.keys())}")


if __name__ == "__main__":
    print_config()
