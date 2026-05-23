"""
Configuration and data directory management.

Public config (client ID, redirect URI, providers) lives in config.json (tracked in git).
Private overrides (DATA_DIR) live in .env (gitignored).
Private data (tokens, DB, raw responses) stored in a configurable directory,
separate from source code (default: ../EHR Import Private/).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project directory (where this file lives)
PROJECT_DIR = Path(__file__).parent.resolve()
load_dotenv(PROJECT_DIR / ".env")

# Load public config
with open(PROJECT_DIR / "config.json") as f:
    _config = json.load(f)

# Data directory: configurable, defaults to sibling "EHR Import Private"
_data_dir_env = os.getenv("DATA_DIR")
if _data_dir_env:
    DATA_DIR = Path(_data_dir_env).resolve()
else:
    DATA_DIR = (PROJECT_DIR / ".." / "EHR Import Private").resolve()

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Derived paths
DB_PATH = DATA_DIR / "ehr_data.db"
TOKEN_STORE = DATA_DIR / "tokens.json"
ENDPOINTS_FILE = DATA_DIR / "discovered_endpoints.json"
RAW_PULLS_DIR = DATA_DIR / "raw_pulls"
JWK_PRIVATE_KEY_PATH = DATA_DIR / "jwk_private.pem"

# App config (public — safe to commit)
_active_app = _config.get("active_app", "public")
_apps = _config.get("apps", {})
_active_app_config = _apps.get(_active_app, {})

CLIENT_ID = _active_app_config.get("client_id", _config.get("client_id", ""))
NON_PRODUCTION_CLIENT_ID = _active_app_config.get("non_production_client_id", _config.get("non_production_client_id", ""))
AUTH_METHODS = _active_app_config["auth_methods"]
REDIRECT_URI = _config["redirect_uri"]
PROVIDERS = _config.get("providers", {})


def get_client_id(provider_name: str) -> str:
    """Return the appropriate client ID for a provider.

    Uses non-production client ID if the provider is flagged with
    "non_production": true in config.json providers section.
    Otherwise uses the production client ID.
    """
    provider_config = PROVIDERS.get(provider_name, {})
    if provider_config.get("non_production"):
        return NON_PRODUCTION_CLIENT_ID
    return CLIENT_ID


# Legacy: module-level ACTIVE_CLIENT_ID for any code that hasn't migrated yet.
# Prefer get_client_id(provider_name) in new code.
ACTIVE_CLIENT_ID = CLIENT_ID


def print_config():
    """Print current configuration for debugging."""
    print(f"Project directory: {PROJECT_DIR}")
    print(f"Data directory:    {DATA_DIR}")
    print(f"Database:          {DB_PATH}")
    print(f"Token store:       {TOKEN_STORE}")
    print(f"Endpoints file:    {ENDPOINTS_FILE}")
    print(f"Raw pulls:         {RAW_PULLS_DIR}")
    print(f"Client ID (prod):  {CLIENT_ID}")
    print(f"Client ID (non-prod): {NON_PRODUCTION_CLIENT_ID}")
    print(f"Redirect URI:      {REDIRECT_URI}")
    print(f"JWK private key:   {JWK_PRIVATE_KEY_PATH} ({'exists' if JWK_PRIVATE_KEY_PATH.exists() else 'missing'})")
    print(f"Client secret:     {DATA_DIR / 'client_secret.txt'} ({'exists' if (DATA_DIR / 'client_secret.txt').exists() else 'missing'})")
    print(f"Providers:         {list(PROVIDERS.keys())}")


if __name__ == "__main__":
    print_config()
