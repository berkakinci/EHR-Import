# Authentication Methods

The app supports three OAuth2 authentication methods. Which methods are available is
determined by the `auth_methods` array in each app's config within `config.json`.

During token exchange, the app tries each configured method in order until one succeeds.

## Configuration

```json
{
    "active_app": "confidential",
    "apps": {
        "public": {
            "client_id": "...",
            "auth_methods": ["public"]
        },
        "confidential": {
            "client_id": "...",
            "non_production_client_id": "...",
            "auth_methods": ["jwt", "secret"]
        }
    }
}
```

Each method is only attempted if the required credentials are present:
- `"public"` — always available (PKCE needs no credentials)
- `"secret"` — requires `DATA_DIR/client_secret.txt`
- `"jwt"` — requires `DATA_DIR/jwk_private.pem`

The successful method is stored in `tokens.json` so that token refresh uses the same method.

## Public Client (default for open-source use)

- No client secret or key pair needed — just the client ID
- Uses PKCE (S256 code challenge) for security
- Anyone can clone the repo and use it immediately
- **Tradeoff:** No refresh tokens. Access tokens expire (~1 hour), requiring re-login.
  Acceptable for a "download my data" tool that runs occasionally.
- Token exchange sends: `client_id` + `code` + `redirect_uri` + `code_verifier`

## Confidential Client (advanced use)

- Requires a registered app with the "confidential client" profile enabled
- Authenticates to the token endpoint using a signed JWT (`private_key_jwt`)
- **Enables refresh tokens** — access persists across sessions without re-login
- Token exchange sends: `client_id` + `code` + `redirect_uri` + `client_assertion`

### JWT Assertion Flow (private_key_jwt)

Instead of a client secret, the app signs a short-lived JWT with an RSA private key.
Epic verifies it against the public key hosted at the app's registered JWK Set URL.

1. Generate an RSA key pair (one-time setup via `setup/generate_jwk.py`)
2. Host the public key as a JWKS file (e.g., raw GitHub URL or any HTTPS endpoint)
3. Register the JWK Set URL on open.epic.com
4. At token exchange, the app builds a JWT with:
   - `iss`: client ID
   - `sub`: client ID
   - `aud`: token endpoint URL
   - `jti`: unique UUID
   - `exp`: current time + 5 minutes
5. Signs it with RS384 and sends as `client_assertion`

The private key lives in `DATA_DIR/jwk_private.pem` (gitignored).
The public JWKS lives in the public repo at `jwks.json`.

### Why JWK Set URL over Client Secret

- Epic hashes client secrets — you can't retrieve them after generation
- Secrets are per-organization (each org download needs its own secret)
- JWK Set URL is set once at the app level and works for all organizations
- Epic recommends JWK Set URL and is deprecating other methods for backend apps

## Why Two Client Types?

Epic's model requires each developer to register their own app. For an open-source tool
whose purpose is helping patients access their own data, this creates unacceptable friction.

The public client path eliminates all credential management — users just need the shared
client ID (published in the repo). The confidential path exists for developers who want
refresh tokens and are willing to register their own app.

## Production Distribution (Confidential Client)

After marking an app "Ready for Production" on open.epic.com:
1. Epic organizations request to download the app (happens automatically for qualifying apps)
2. The developer must activate each download via "Review & Manage Downloads"
3. Non-production must be activated before production
4. With JWK Set URL auth, select "JWK Set URL (Recommended)" — it uses the app-level URL
5. Leave "Use App-level Endpoint URIs" checked unless redirect URIs vary per org (our single localhost redirect works fine at app level)
6. There may be a sync delay (up to 1 business day) before the org recognizes the client ID

## Scope Behavior

- Sandbox auto-grants all registered scopes regardless of what you request — don't rely on sandbox to validate scope configuration
- Production may be stricter — keep the requested scope list in sync with registered APIs
- Don't request scopes for unregistered resources — this may break the auth flow entirely
- Epic's API catalog lists `PerformsScopeValidation: false` for all resources, yet production has been observed to hide data when scopes are not explicitly requested. Unverified: it's unclear whether this is actual scope enforcement or a side effect of something else.
