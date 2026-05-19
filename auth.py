"""
SMART on FHIR standalone patient launch — OAuth2 authorization code flow.

Supports two client types:
  - "public": PKCE (S256), no client secret, no refresh tokens
  - "confidential": JWT assertion (private_key_jwt), enables refresh tokens

Opens a browser for MyChart login, runs a local HTTPS server to catch the callback,
exchanges the auth code for access + refresh tokens, and saves them locally.
"""

import json
import hashlib
import secrets
import ssl
import uuid
import webbrowser
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

from config import (
    TOKEN_STORE, ENDPOINTS_FILE, ACTIVE_CLIENT_ID, REDIRECT_URI, DATA_DIR,
    JWK_PRIVATE_KEY_PATH,
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


# --- PKCE helpers (public client) ---

def _detect_auth_method() -> str:
    """
    Detect which authentication method to use based on available credential files.

    Returns: "jwt", "secret", or "public"
    Priority: JWT > client secret > public (PKCE only)
    """
    if JWK_PRIVATE_KEY_PATH.exists():
        return "jwt"
    secret_file = DATA_DIR / "client_secret.txt"
    if secret_file.exists() and secret_file.read_text().strip():
        return "secret"
    return "public"


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]  # 43-128 chars
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# --- JWT assertion helpers (confidential client) ---

def _load_jwk_private_key():
    """Load the RSA private key for JWT signing."""
    if not JWK_PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"JWT private key not found at {JWK_PRIVATE_KEY_PATH}\n"
            "Run: python setup/generate_jwk.py"
        )

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(JWK_PRIVATE_KEY_PATH, "rb") as f:
        return load_pem_private_key(f.read(), password=None)


def _load_jwk_kid() -> str:
    """Load the key ID from the project's jwks.json."""
    jwks_path = Path(__file__).parent / "jwks.json"
    if not jwks_path.exists():
        raise FileNotFoundError(
            f"jwks.json not found at {jwks_path}\n"
            "Run: python setup/generate_jwk.py"
        )

    with open(jwks_path) as f:
        jwks = json.load(f)

    return jwks["keys"][0]["kid"]


def build_client_assertion(token_endpoint: str) -> str:
    """
    Build a signed JWT for private_key_jwt authentication.

    The JWT asserts the client's identity to the token endpoint.
    Epic verifies it against the public key at the registered JWK Set URL.
    """
    import jwt as pyjwt

    private_key = _load_jwk_private_key()
    kid = _load_jwk_kid()

    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": ACTIVE_CLIENT_ID,
        "sub": ACTIVE_CLIENT_ID,
        "aud": token_endpoint,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "nbf": now,
    }

    token = pyjwt.encode(
        payload,
        private_key,
        algorithm="RS384",
        headers={"alg": "RS384", "typ": "JWT", "kid": kid},
    )

    return token


# --- Self-signed cert for HTTPS callback ---

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
        import datetime as dt

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
            .not_valid_before(dt.datetime.now(dt.timezone.utc))
            .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(_ipaddress_from_string("127.0.0.1")),
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
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
            "-days", "3650", "-nodes",
            "-subj", "/CN=localhost",
        ], check=True, capture_output=True)
        print(f"✓ Generated self-signed cert (via openssl) at {CERT_DIR}/")


def _ipaddress_from_string(addr: str):
    """Helper to create an IPAddress object for SAN."""
    import ipaddress
    return ipaddress.IPv4Address(addr)


# --- Callback server ---

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


# --- Core auth flow ---

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
    """
    Run the full OAuth2 authorization code flow.

    Auth method is auto-detected from available credential files:
      - jwk_private.pem exists → JWT assertion (private_key_jwt)
      - client_secret.txt exists → client secret
      - neither → public client with PKCE
    """
    if not ACTIVE_CLIENT_ID:
        raise ValueError("No client ID configured")

    # Reset class-level state (in case of re-use within same process)
    CallbackHandler.auth_code = None
    CallbackHandler.state = None

    auth_method = _detect_auth_method()

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

    # PKCE for public clients (also fine to include for confidential — adds security)
    code_verifier = None
    if auth_method == "public":
        code_verifier, code_challenge = generate_pkce_pair()
        auth_params["code_challenge"] = code_challenge
        auth_params["code_challenge_method"] = "S256"

    auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"

    print(f"\nOpening browser for MyChart login...")
    print(f"Provider: {provider_name}")
    print(f"Auth method: {auth_method}")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")

    # Start local HTTPS server to catch callback
    parsed_redirect = urlparse(REDIRECT_URI)
    port = parsed_redirect.port or 9432
    use_https = parsed_redirect.scheme == "https"

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("localhost", port), CallbackHandler)

    if use_https:
        ensure_self_signed_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    webbrowser.open(auth_url)

    print(f"Waiting for callback on localhost:{port} ({'HTTPS' if use_https else 'HTTP'})...")
    print("(If your browser asks about a certificate warning, accept it and continue.)")

    # Wait for the auth code — no timeout (user may need time for consent pages)
    server.timeout = None

    try:
        while not CallbackHandler.auth_code:
            server.handle_request()
    finally:
        server.server_close()

    if not CallbackHandler.auth_code:
        raise RuntimeError("No authorization code received")

    if CallbackHandler.state != state:
        raise RuntimeError("State mismatch — possible CSRF attack")

    print("✓ Authorization code received. Exchanging for tokens...")

    # --- Token exchange (branched by auth method) ---
    token_data = {
        "grant_type": "authorization_code",
        "code": CallbackHandler.auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": ACTIVE_CLIENT_ID,
    }

    if auth_method == "public":
        # Public client: send PKCE code_verifier, no credentials
        token_data["code_verifier"] = code_verifier

    elif auth_method == "jwt":
        # Confidential client: JWT assertion
        assertion = build_client_assertion(token_endpoint)
        token_data["client_assertion_type"] = (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )
        token_data["client_assertion"] = assertion

    elif auth_method == "secret":
        # Confidential client: client secret
        token_data["client_secret"] = _load_client_secret()

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

    has_refresh = "✓" if tokens.get("refresh_token") else "✗ (public client — re-run auth when token expires)"
    print(f"✓ Tokens saved. Patient ID: {tokens.get('patient')}")
    print(f"  Refresh token: {has_refresh}")
    return token_record


# --- Client secret (legacy confidential path) ---

def _load_client_secret() -> str | None:
    """Load client secret from the private data directory."""
    secret_file = DATA_DIR / "client_secret.txt"
    if secret_file.exists():
        return secret_file.read_text().strip()
    return None


# --- Token persistence ---

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


# --- Token refresh (confidential client only) ---

def refresh_access_token(provider_name: str) -> dict:
    """
    Use refresh token to get a new access token.

    Only available for confidential clients (jwt or secret).
    Public clients must re-authorize.
    """
    tokens = load_tokens(provider_name)
    auth_method = _detect_auth_method()

    if not tokens or not tokens.get("refresh_token"):
        if auth_method == "public":
            raise ValueError(
                f"No refresh token for '{provider_name}' (public client). "
                f"Run: python auth.py \"{provider_name}\" to re-authorize."
            )
        raise ValueError(f"No refresh token for '{provider_name}'. Run authorize() again.")

    token_endpoint = tokens["token_endpoint"]

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": ACTIVE_CLIENT_ID,
    }

    # Authenticate the refresh request (same method as token exchange)
    if auth_method == "jwt":
        assertion = build_client_assertion(token_endpoint)
        token_data["client_assertion_type"] = (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )
        token_data["client_assertion"] = assertion
    elif auth_method == "secret":
        client_secret = _load_client_secret()
        if client_secret:
            token_data["client_secret"] = client_secret

    resp = requests.post(token_endpoint, data=token_data)
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


# --- CLI entry point ---

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python auth.py <provider_name>")
        print(f"\n  Auth method (detected): {_detect_auth_method()}")
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
