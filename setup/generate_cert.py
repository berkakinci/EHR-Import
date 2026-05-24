#!/usr/bin/env python3
"""
Generate a self-signed TLS certificate for the local OAuth callback server.

The HTTPS redirect URI (https://localhost:9432/callback) requires a certificate.
This script generates one and stores it in the private data directory.

This is run automatically by auth.py if no cert exists, but you can run it
manually to pre-generate or regenerate the cert.

Usage:
    python setup/generate_cert.py
"""

import sys
import subprocess
from pathlib import Path

# Add parent directory to path so we can import config
sys.path.insert(0, str(Path(__file__).parent.parent))
from ehr_import import config
DATA_DIR = config.data_dir

CERT_DIR = DATA_DIR / "certs"
CERT_FILE = CERT_DIR / "localhost.pem"
KEY_FILE = CERT_DIR / "localhost-key.pem"


def generate_with_cryptography():
    """Generate cert using the cryptography Python package."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def generate_with_openssl():
    """Fallback: generate cert using the openssl command."""
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
        "-days", "3650", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)


def main():
    CERT_DIR.mkdir(parents=True, exist_ok=True)

    if CERT_FILE.exists() and KEY_FILE.exists():
        print(f"Certificate already exists at:")
        print(f"  Cert: {CERT_FILE}")
        print(f"  Key:  {KEY_FILE}")
        response = input("Regenerate? [y/N] ").strip().lower()
        if response != "y":
            print("Keeping existing cert.")
            return

    print("Generating self-signed certificate for localhost...")

    try:
        generate_with_cryptography()
        print("Generated using Python cryptography package.")
    except ImportError:
        print("cryptography package not installed, falling back to openssl...")
        generate_with_openssl()
        print("Generated using openssl command.")

    print(f"\n✓ Certificate saved to:")
    print(f"  Cert: {CERT_FILE}")
    print(f"  Key:  {KEY_FILE}")
    print(f"\nValid for 10 years. Your browser will show a security warning")
    print(f"the first time — this is expected for self-signed certs.")


if __name__ == "__main__":
    main()
