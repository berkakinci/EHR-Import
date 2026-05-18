"""
SMART on FHIR standalone patient launch — OAuth2 authorization code flow.

Opens a browser for MyChart login, runs a local HTTPS server to catch the callback,
exchanges the auth code for access + refresh tokens, and saves them locally.
"""

import json
import secrets
import ssl
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path

import requests

from config import (
    TOKEN_STORE, ENDPOINTS_FILE, ACTIVE_CLIENT_ID, REDIRECT_URI, DATA_DIR
)

# Scopes we need for labs + notes
SCOPES = " ".join([
    "openid",
    "fhirUser",
    "launch/patient",
    "patient/Patient.read",
    "patient/Observation.read",
    "patient/DiagnosticReport.read",
    "patient/DocumentReference.read",
    "patient/Encounter.read",
    "patient/Condition.read",
])

# Self-signed cert paths (for local HTTPS callback)
CERT_DIR = DATA_DIR / "certs"
CERT_FILE = CERT_DIR / "localhost.pem"
KEY_FILE = CERT_DIR / "localhost-key.pem"


def ensure_self_signed_cert():
    """Generate a self-signed certificate for localhost if it doesn't exist."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return

    CERT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime

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
                    x509.IPAddress(ipaddress_from_string("127.0.0.1")),
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

        print(f"✓ Generated self-signed cert at {CERT_DIR}/")

    except ImportError:
        # Fallback: use openssl command
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
            "-days", "3650", "-nodes",
            "-subj", "/CN=localhost",
        ], check=True, capture_output=True)
        print(f"✓ Generated self-signed cert (via openssl) at {CERT_DIR}/")


def ipaddress_from_string(addr: str):
    """Helper to create an IPAddress object for SAN."""
    import ipaddress
    return ipaddress.IPv4Address(addr)


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    auth_code = None
    state = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            CallbackHandler.auth_code = params["code"][0]
            CallbackHandler.state = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization successful!</h2>"
                b"<p>You can close this window and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Error: {error}</h2></body></html>".encode()
            )

    def log_message(self, format, *args):
        pass  # Suppress request logging


def load_endpoint_config(provider_name: str) -> dict:
    """Load discovered endpoints for a provider."""
    if not ENDPOINTS_FILE.exists():
        raise FileNotFoundError(
            "Run discover_endpoints.py first to find your provider's FHIR URLs"
        )

    with open(ENDPOINTS_FILE) as f:
        endpoints = json.load(f)

    if provider_name not in endpoints or endpoints[provider_name] is None:
        raise ValueError(f"No endpoints found for '{provider_name}'. Check discovery results.")

    return endpoints[provider_name]


def authorize(provider_name: str) -> dict:
    """Run the full OAuth2 authorization code flow."""
    if not ACTIVE_CLIENT_ID:
        raise ValueError("No client ID configured")

    config = load_endpoint_config(provider_name)

    auth_endpoint = config["authorization_endpoint"]
    token_endpoint = config["token_endpoint"]

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": ACTIVE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "aud": config.get("fhir_base_url", ""),
    }

    auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"

    print(f"\nOpening browser for MyChart login...")
    print(f"Provider: {provider_name}")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")

    # Start local HTTPS server to catch callback
    parsed_redirect = urlparse(REDIRECT_URI)
    port = parsed_redirect.port or 9432
    use_https = parsed_redirect.scheme == "https"

    server = HTTPServer(("localhost", port), CallbackHandler)

    if use_https:
        ensure_self_signed_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    webbrowser.open(auth_url)

    print(f"Waiting for callback on localhost:{port} ({'HTTPS' if use_https else 'HTTP'})...")
    server.handle_request()  # Handle single request (the callback)

    if not CallbackHandler.auth_code:
        raise RuntimeError("No authorization code received")

    if CallbackHandler.state != state:
        raise RuntimeError("State mismatch — possible CSRF attack")

    print("✓ Authorization code received. Exchanging for tokens...")

    # Exchange code for tokens (confidential client — include secret)
    token_data = {
        "grant_type": "authorization_code",
        "code": CallbackHandler.auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": ACTIVE_CLIENT_ID,
    }

    # Load client secret if available
    client_secret = _load_client_secret()
    if client_secret:
        token_data["client_secret"] = client_secret

    resp = requests.post(token_endpoint, data=token_data)
    resp.raise_for_status()
    tokens = resp.json()

    # Save tokens
    token_record = {
        "provider": provider_name,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "patient": tokens.get("patient"),  # Patient FHIR ID
        "fhir_base_url": config.get("fhir_base_url"),
        "token_endpoint": token_endpoint,
    }

    save_tokens(provider_name, token_record)
    print(f"✓ Tokens saved. Patient ID: {tokens.get('patient')}")
    return token_record


def _load_client_secret() -> str | None:
    """Load client secret from the private data directory."""
    secret_file = DATA_DIR / "client_secret.txt"
    if secret_file.exists():
        return secret_file.read_text().strip()
    return None


def save_tokens(provider_name: str, token_record: dict):
    """Save tokens to local file (per-provider)."""
    all_tokens = {}
    if TOKEN_STORE.exists():
        with open(TOKEN_STORE) as f:
            all_tokens = json.load(f)

    all_tokens[provider_name] = token_record

    with open(TOKEN_STORE, "w") as f:
        json.dump(all_tokens, f, indent=2)


def load_tokens(provider_name: str) -> dict | None:
    """Load saved tokens for a provider."""
    if not TOKEN_STORE.exists():
        return None

    with open(TOKEN_STORE) as f:
        all_tokens = json.load(f)

    return all_tokens.get(provider_name)


def refresh_access_token(provider_name: str) -> dict:
    """Use refresh token to get a new access token."""
    tokens = load_tokens(provider_name)

    if not tokens or not tokens.get("refresh_token"):
        raise ValueError(f"No refresh token for '{provider_name}'. Run authorize() again.")

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": ACTIVE_CLIENT_ID,
    }

    client_secret = _load_client_secret()
    if client_secret:
        token_data["client_secret"] = client_secret

    resp = requests.post(tokens["token_endpoint"], data=token_data)
    resp.raise_for_status()
    new_tokens = resp.json()

    # Update stored tokens
    tokens["access_token"] = new_tokens["access_token"]
    if "refresh_token" in new_tokens:
        tokens["refresh_token"] = new_tokens["refresh_token"]
    tokens["expires_in"] = new_tokens.get("expires_in")

    save_tokens(provider_name, tokens)
    print(f"✓ Token refreshed for {provider_name}")
    return tokens


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python auth.py <provider_name>")
        print("\nAvailable providers (from discovered_endpoints.json):")
        if ENDPOINTS_FILE.exists():
            with open(ENDPOINTS_FILE) as f:
                for name in json.load(f):
                    print(f"  - \"{name}\"")
        else:
            print("  (run discover_endpoints.py first)")
        sys.exit(1)

    provider = sys.argv[1]
    authorize(provider)
