#!/usr/bin/env python3
"""
Generate an RSA key pair for JWT-based authentication (private_key_jwt).

Creates:
  - Private key (PEM) → DATA_DIR/jwk_private.pem (gitignored)
  - Public JWKS file  → project root/jwks.json (committed, hosted publicly)

The JWKS file can be served from a raw GitHub URL and registered as the
JWK Set URL on open.epic.com.

Usage:
    python setup/generate_jwk.py [--kid KEY_ID]
"""

import json
import sys
import uuid
import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

# Resolve paths
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent

# Import config to get data_dir
sys.path.insert(0, str(PROJECT_DIR))
from ehr_import import config
DATA_DIR = config.data_dir


def int_to_base64url(n: int) -> str:
    """Convert an integer to a base64url-encoded string (no padding)."""
    byte_length = (n.bit_length() + 7) // 8
    n_bytes = n.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode("ascii")


def generate_jwk(kid: str | None = None):
    """Generate an RSA key pair and save as PEM + JWKS."""
    if kid is None:
        kid = str(uuid.uuid4())[:8]

    private_key_path = DATA_DIR / "jwk_private.pem"
    jwks_path = PROJECT_DIR / "jwks.json"

    # Check if key already exists
    if private_key_path.exists():
        print(f"⚠ Private key already exists at {private_key_path}")
        print("  Delete it first if you want to regenerate.")
        print("  (This would invalidate the existing JWKS registration on open.epic.com)")
        sys.exit(1)

    # Generate RSA 2048-bit key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Save private key (PEM format, no encryption)
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    with open(private_key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    print(f"✓ Private key saved to {private_key_path}")

    # Extract public key numbers for JWKS
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()

    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS384",
        "kid": kid,
        "n": int_to_base64url(public_numbers.n),
        "e": int_to_base64url(public_numbers.e),
    }

    jwks = {"keys": [jwk]}

    with open(jwks_path, "w") as f:
        json.dump(jwks, f, indent=2)
    print(f"✓ Public JWKS saved to {jwks_path}")
    print(f"  Key ID (kid): {kid}")
    print(f"\n  Register this URL as your JWK Set URL on open.epic.com:")
    print(f"  https://raw.githubusercontent.com/berkakinci/EHR-Import/main/jwks.json")


if __name__ == "__main__":
    kid = None
    if "--kid" in sys.argv:
        idx = sys.argv.index("--kid") + 1
        if idx < len(sys.argv):
            kid = sys.argv[idx]

    generate_jwk(kid)
