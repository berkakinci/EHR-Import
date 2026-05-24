#!/usr/bin/env python3
"""OAuth2 authorization — authenticate with a provider's MyChart."""
import sys

from ehr_import import config
from ehr_import.auth import authorize, _get_usable_auth_methods

if len(sys.argv) < 2:
    print("Usage: python auth.py <provider_name>")
    usable = _get_usable_auth_methods()
    print(f"\n  Configured auth methods: {config.auth_methods}")
    print(f"  Usable (credentials available): {usable}")
    print("\nAvailable providers (from discovered_endpoints.json):")
    if config.endpoints_file.exists():
        import json
        with open(config.endpoints_file) as f:
            for name in json.load(f):
                print(f"  - \"{name}\"")
    else:
        print("  (run discover.py first)")
    sys.exit(1)

authorize(sys.argv[1])
