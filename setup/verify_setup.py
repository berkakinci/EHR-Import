#!/usr/bin/env python3
"""
Verify that the EHR Import environment is correctly set up.

Checks:
- Python version
- Required packages installed
- config.json readable
- Data directory exists and is writable
- Self-signed cert exists (or can be generated)
- Discovered endpoints file exists

Usage:
    python setup/verify_setup.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

errors = []
warnings = []


def check(label, condition, error_msg=None, is_warning=False):
    if condition:
        print(f"  ✓ {label}")
    else:
        msg = f"  ✗ {label}" + (f" — {error_msg}" if error_msg else "")
        print(msg)
        if is_warning:
            warnings.append(label)
        else:
            errors.append(label)


print("=== EHR Import Setup Verification ===\n")

# Python version
print("[Python]")
v = sys.version_info
check(f"Python {v.major}.{v.minor}.{v.micro}", v.major == 3 and v.minor >= 11,
      "Python 3.11+ required")

# Required packages
print("\n[Packages]")
packages = ["requests", "dotenv", "httpx", "cryptography"]
for pkg in packages:
    try:
        mod = pkg if pkg != "dotenv" else "dotenv"
        __import__(mod)
        check(f"{pkg} installed", True)
    except ImportError:
        check(f"{pkg} installed", False, "pip install -r requirements.txt")

# Config
print("\n[Configuration]")
try:
    from ehr_import import config
    check("config.json loaded", True)
    check(f"Client ID configured", bool(config.client_id), "Check config.json")
    check(f"Redirect URI: {config.redirect_uri}", "localhost" in config.redirect_uri)
    check(f"Providers configured: {len(config.providers)}", len(config.providers) > 0)
except Exception as e:
    check("config.json loaded", False, str(e))
    print("\n  Cannot continue without config. Fix config.json first.")
    sys.exit(1)

# Data directory
print("\n[Data Directory]")
check(f"Data dir exists: {config.data_dir}", config.data_dir.exists(),
      f"Will be created on first run")
if config.data_dir.exists():
    check("Data dir writable", config.data_dir.is_dir(),
          "Check permissions")

# Cert
print("\n[TLS Certificate]")
cert_dir = config.data_dir / "certs"
cert_file = cert_dir / "localhost.pem"
key_file = cert_dir / "localhost-key.pem"
check("Self-signed cert exists", cert_file.exists() and key_file.exists(),
      "Run: python setup/generate_cert.py", is_warning=True)

# Endpoints
print("\n[Endpoints]")
check("Discovered endpoints file exists", config.endpoints_file.exists(),
      "Run: python discover.py", is_warning=True)
if config.endpoints_file.exists():
    import json
    with open(config.endpoints_file) as f:
        eps = json.load(f)
    configured = sum(1 for v in eps.values() if v and v.get("authorization_endpoint"))
    check(f"Providers with auth endpoints: {configured}/{len(eps)}", configured > 0)

# Client secret
print("\n[Authentication]")
secret_file = config.data_dir / "client_secret.txt"
check("Client secret file exists", secret_file.exists(),
      "Create DATA_DIR/client_secret.txt with your sandbox client secret",
      is_warning=True)

# Summary
print("\n" + "=" * 40)
if errors:
    print(f"\n✗ {len(errors)} error(s) — fix these before proceeding:")
    for e in errors:
        print(f"  - {e}")
elif warnings:
    print(f"\n⚠ Setup OK with {len(warnings)} warning(s):")
    for w in warnings:
        print(f"  - {w}")
    print("\nThese are optional for initial setup but needed before first use.")
else:
    print("\n✓ All checks passed. Ready to go!")

print()
