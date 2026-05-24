"""
SMART on FHIR standalone patient launch — OAuth2 authorization code flow.

Supports multiple authentication methods (configured per-app in config.json):
  - "public": PKCE (S256), no client secret, no refresh tokens
  - "secret": client secret authentication
  - "jwt": JWT assertion (private_key_jwt), enables refresh tokens

During token exchange, tries each configured method in order until one succeeds.
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
from datetime import datetime, timezone, timedelta

import requests

from . import config

# Scopes we need — must match the APIs registered on open.epic.com
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
    "patient/MedicationRequest.read",
    "patient/AllergyIntolerance.read",
    "patient/MedicationDispense.read",
    "patient/Procedure.read",
    "patient/CarePlan.read",
    "patient/CareTeam.read",
    "patient/Coverage.read",
    "patient/ServiceRequest.read",
    "patient/Immunization.read",
    "patient/Goal.read",
    "patient/Device.read",
    "patient/RelatedPerson.read",
    "patient/Specimen.read",
    "patient/Organization.read",
    "patient/Practitioner.read",
    "patient/PractitionerRole.read",
    "patient/Location.read",
    "patient/Medication.read",
    "patient/QuestionnaireResponse.read",
    "patient/Binary.read",
    "patient/Media.read",
])

# Self-signed cert paths (for local HTTPS callback)
CERT_DIR = config.data_dir / "certs"
CERT_FILE = CERT_DIR / "localhost.pem"
KEY_FILE = CERT_DIR / "localhost-key.pem"


# --- Auth method helpers ---

def _can_use_method(method: str) -> bool:
    """Check whether the credentials needed for a given auth method are available."""
    if method == "jwt":
        return config.jwk_private_key_path.exists()
    elif method == "secret":
        secret_file = config.data_dir / "client_secret.txt"
        return secret_file.exists() and bool(secret_file.read_text().strip())
    elif method == "public":
        return True  # PKCE needs no credentials
    return False


def _get_usable_auth_methods() -> list[str]:
    """
    Return the ordered list of auth methods that are both configured and have
    the required credentials available.
    """
    return [m for m in config.auth_methods if _can_use_method(m)]


# --- PKCE helpers ---


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]  # 43-128 chars
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# --- JWT assertion helpers (confidential client) ---

def _load_jwk_private_key():
    """Load the RSA private key for JWT signing."""
    if not config.jwk_private_key_path.exists():
        raise FileNotFoundError(
            f"JWT private key not found at {config.jwk_private_key_path}\n"
            "Run: python setup/generate_jwk.py"
        )

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(config.jwk_private_key_path, "rb") as f:
        return load_pem_private_key(f.read(), password=None)


def _load_jwk_kid() -> str:
    """Load the key ID from the project's jwks.json."""
    jwks_path = config.project_dir / "jwks.json"
    if not jwks_path.exists():
        raise FileNotFoundError(
            f"jwks.json not found at {jwks_path}\n"
            "Run: python setup/generate_jwk.py"
        )

    with open(jwks_path) as f:
        jwks = json.load(f)

    return jwks["keys"][0]["kid"]


def build_client_assertion(token_endpoint: str, client_id: str = None) -> str:
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
        "iss": client_id or config.client_id,
        "sub": client_id or config.client_id,
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
    if not config.endpoints_file.exists():
        raise FileNotFoundError(
            "Run discover_endpoints.py first to find your provider's FHIR URLs"
        )

    with open(config.endpoints_file) as f:
        endpoints = json.load(f)

    if provider_name not in endpoints or endpoints[provider_name] is None:
        raise ValueError(f"No endpoints found for '{provider_name}'. Check discovery results.")

    return endpoints[provider_name]


def authorize(provider_name: str) -> dict:
    """
    Run the full OAuth2 authorization code flow.

    Tries each configured auth method (from config.json auth_methods array) in order
    during the token exchange. The first method that succeeds wins.
    """
    active_client_id = config.get_client_id(provider_name)
    if not active_client_id:
        raise ValueError("No client ID configured")

    # Reset class-level state (in case of re-use within same process)
    CallbackHandler.auth_code = None
    CallbackHandler.state = None

    usable_methods = _get_usable_auth_methods()
    if not usable_methods:
        raise ValueError(
            f"No usable auth methods. Configured: {config.auth_methods}. "
            "Check that required credential files exist."
        )

    # Determine if any method needs PKCE (include it if "public" is in the list)
    use_pkce = "public" in usable_methods

    endpoint_config = load_endpoint_config(provider_name)

    auth_endpoint = endpoint_config["authorization_endpoint"]
    token_endpoint = endpoint_config["token_endpoint"]

    # Per-provider redirect URI override (e.g., for WAF workarounds)
    provider_config = config.providers.get(provider_name, {})
    redirect_uri = provider_config.get("redirect_uri", config.redirect_uri)

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": active_client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "aud": endpoint_config.get("fhir_base_url", ""),
    }

    # PKCE — include if any usable method is "public" (harmless for confidential too)
    code_verifier = None
    if use_pkce:
        code_verifier, code_challenge = generate_pkce_pair()
        auth_params["code_challenge"] = code_challenge
        auth_params["code_challenge_method"] = "S256"

    auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"

    print(f"\nOpening browser for MyChart login...")
    print(f"Provider: {provider_name}")
    print(f"Auth methods to try: {usable_methods}")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")

    # Start local HTTPS server to catch callback
    parsed_redirect = urlparse(redirect_uri)
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

    # --- Token exchange: try each method in order ---
    tokens = None
    successful_method = None

    for method in usable_methods:
        token_data = {
            "grant_type": "authorization_code",
            "code": CallbackHandler.auth_code,
            "redirect_uri": redirect_uri,
            "client_id": active_client_id,
        }

        if method == "public":
            token_data["code_verifier"] = code_verifier
        elif method == "jwt":
            assertion = build_client_assertion(token_endpoint, active_client_id)
            token_data["client_assertion_type"] = (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            )
            token_data["client_assertion"] = assertion
        elif method == "secret":
            token_data["client_secret"] = _load_client_secret()

        resp = requests.post(token_endpoint, data=token_data)

        if resp.ok:
            tokens = resp.json()
            successful_method = method
            break
        else:
            # Log failure and try next method
            detail = ""
            try:
                err_body = resp.json()
                detail = err_body.get("error_description") or err_body.get("error", "")
            except (ValueError, AttributeError):
                detail = resp.text[:200] if resp.text else ""
            print(f"  ✗ {method}: HTTP {resp.status_code} — {detail}")

    if tokens is None:
        raise RuntimeError(
            f"Token exchange failed with all methods: {usable_methods}. "
            "Check app registration and credential files."
        )

    print(f"  ✓ Token exchange succeeded with: {successful_method}")

    # Save tokens (include auth_method so refresh knows what to use)
    token_record = {
        "provider": provider_name,
        "auth_method": successful_method,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "patient": tokens.get("patient"),  # Patient FHIR ID
        "fhir_base_url": endpoint_config.get("fhir_base_url"),
        "token_endpoint": token_endpoint,
    }

    save_tokens(provider_name, token_record)

    has_refresh = "✓" if tokens.get("refresh_token") else "✗ (no refresh token — re-run auth when token expires)"
    print(f"✓ Tokens saved. Patient ID: {tokens.get('patient')}")
    print(f"  Refresh token: {has_refresh}")
    return token_record


# --- Client secret (legacy confidential path) ---

def _load_client_secret() -> str | None:
    """Load client secret from the private data directory."""
    secret_file = config.data_dir / "client_secret.txt"
    if secret_file.exists():
        return secret_file.read_text().strip()
    return None


# --- Token persistence ---

def _token_key(provider_name: str, patient_id: str) -> str:
    """Build the token store key: 'provider:patient_id'."""
    return f"{provider_name}:{patient_id}"


def save_tokens(provider_name: str, token_record: dict):
    """Save tokens to local file (per-provider, per-patient)."""
    all_tokens = {}
    if config.token_store.exists():
        with open(config.token_store) as f:
            all_tokens = json.load(f)

    patient_id = token_record.get("patient")
    key = _token_key(provider_name, patient_id)

    # Note if there are existing tokens for other patients at this provider
    existing_patients = [
        t.get("patient")
        for k, t in all_tokens.items()
        if t.get("provider") == provider_name and t.get("patient") != patient_id
    ]
    if existing_patients:
        print(f"  ℹ Keeping existing token(s) for {len(existing_patients)} other patient(s) at {provider_name}")

    all_tokens[key] = token_record

    with open(config.token_store, "w") as f:
        json.dump(all_tokens, f, indent=2)


def load_tokens(provider_name: str, patient_id: str | None = None) -> dict | None:
    """
    Load saved tokens for a provider (and optionally a specific patient).

    If patient_id is None, returns the first token found for that provider.
    """
    if not config.token_store.exists():
        return None

    with open(config.token_store) as f:
        all_tokens = json.load(f)

    # Exact match
    if patient_id:
        key = _token_key(provider_name, patient_id)
        return all_tokens.get(key)

    # Find first token matching this provider
    for key, record in all_tokens.items():
        if record.get("provider") == provider_name:
            return record

    return None


def load_all_tokens_for_provider(provider_name: str) -> list[dict]:
    """Load all saved tokens for a provider (all patients)."""
    if not config.token_store.exists():
        return []

    with open(config.token_store) as f:
        all_tokens = json.load(f)

    return [
        record for record in all_tokens.values()
        if record.get("provider") == provider_name
    ]


# --- Token refresh (confidential client only) ---

def refresh_access_token(provider_name: str, patient_id: str | None = None) -> dict:
    """
    Use refresh token to get a new access token.

    Uses the auth_method stored in the token record (from the original authorize flow).
    Falls back to trying configured methods if not stored.
    """
    tokens = load_tokens(provider_name, patient_id)

    if not tokens or not tokens.get("refresh_token"):
        raise ValueError(
            f"No refresh token for '{provider_name}'. "
            f"Run: python auth.py \"{provider_name}\" to re-authorize."
        )

    token_endpoint = tokens["token_endpoint"]
    auth_method = tokens.get("auth_method")

    # Fallback: if no auth_method stored (old token), try first usable non-public method
    if not auth_method:
        usable = _get_usable_auth_methods()
        auth_method = next((m for m in usable if m != "public"), None)
        if not auth_method:
            raise ValueError(
                f"No auth method for refresh (public clients don't get refresh tokens). "
                f"Run: python auth.py \"{provider_name}\" to re-authorize."
            )

    active_client_id = config.get_client_id(provider_name)

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": active_client_id,
    }

    # Authenticate the refresh request
    if auth_method == "jwt":
        assertion = build_client_assertion(token_endpoint, active_client_id)
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
    print(f"✓ Token refreshed for {provider_name} (method: {auth_method})")
    return tokens


def main():
    """CLI entry point for auth."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python auth.py <provider_name>")
        usable = _get_usable_auth_methods()
        print(f"\n  Configured auth methods: {config.auth_methods}")
        print(f"  Usable (credentials available): {usable}")
        print("\nAvailable providers (from discovered_endpoints.json):")
        if config.endpoints_file.exists():
            with open(config.endpoints_file) as f:
                for name in json.load(f):
                    print(f"  - \"{name}\"")
        else:
            print("  (run discover_endpoints.py first)")
        sys.exit(1)

    provider = sys.argv[1]
    authorize(provider)
