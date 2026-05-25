# Epic FHIR App Registration Guide

## Overview

To access your health records programmatically, you register a "patient-facing" app with Epic.
This gives you a **client ID** that your local script uses to authenticate via your MyChart credentials.

**You probably don't need to do this.** This project ships with a pre-registered public client ID
that works for any Epic MyChart system — just clone and run. This guide is only for developers
who want refresh tokens (confidential client) or want to fork the project under their own app registration.

## Step-by-Step

### 1. Create an account on open.epic.com

- Go to https://open.epic.com
- **Important:** The main site lets you browse with Google login, but the **Build Apps**
  section requires a separate **Epic UserWeb** account.
- Go to the "Build Apps" tab and sign up for a UserWeb account when prompted.
  - It asks for "Legal company name" — your own name works fine for personal use.
  - Use a real email — you'll need to verify it.
- Wait for the verification email, then log in.

### 2. Create a new app (page 1: "Create")

After logging in, go to **"Build Apps"** → **"Create"**. Fill in:

| Field | Value | Notes |
|-------|-------|-------|
| **Application Name** | e.g., "EHR Import" | Shown to patients during consent |
| **Application Audience** | Patients | |
| **Automatic Client Distribution** | USCDI v3 | Auto-distributes to all qualifying orgs on production |
| **Public Documentation URL** | Your GitHub repo URL | Optional but recommended |
| **Incoming APIs** | See [API Endpoints](#api-endpoints) below | Select from the Available → Selected list |
| **Endpoint URI** | `https://localhost:9432/callback` | The OAuth redirect URI |
| **Can Register Dynamic Clients** | ☐ unchecked | Not needed |
| **Is Confidential Client** | ☐ or ☑ depending on your needs | See below |

**Confidential client decision:**
- **Unchecked** (public client): No secrets needed, anyone can use the client ID, but no refresh tokens — access expires after ~1 hour and requires re-login. Best for open-source distribution.
- **Checked** (confidential client): Requires a client secret or JWT key pair, enables refresh tokens. Best for persistent access and apps with server infrastructure.
  - Checking this reveals additional options:
  - **Requires Persistent Access** — ☑ check (enables refresh tokens)
  - **Uses Rolling Refresh Tokens** — ☑ check (each refresh gives a new refresh token)
  - **Can Have Indefinite Access** — not available for this app type

Click **Save** to proceed to the Test page.

### 3. Additional settings (visible after Save or on confidential apps)

These fields appear on the same page depending on your choices:

| Field | Value | Notes |
|-------|-------|-------|
| **SMART on FHIR Version** | R4 | |
| **SMART Scope Version** | SMART v1 | v2 adds PKCE requirement server-side |
| **FHIR ID Generation Scheme** | Use Unconstrained FHIR IDs | Default; 64-char limit only for legacy systems |
| **Summary** | Short description (≤500 chars) | Shown to patients/orgs |
| **Description** | Longer explanation (≤1998 chars) | Why/What/How format recommended |
| **Intended Purposes** | Individuals' Access to their EHI | Check applicable boxes |
| **Intended Users** | Individual/Caregiver | Check applicable boxes |

**Confidential client only:**

| Field | Value | Notes |
|-------|-------|-------|
| **Requires Persistent Access** | ☑ | Enables refresh tokens |
| **Uses Rolling Refresh Tokens** | ☑ | Each refresh gives a new refresh token |
| **Can Have Indefinite Access** | Not available for this app type | |
| **Non-Production JWK Set URL** | Your JWKS URL for sandbox | e.g., raw GitHub URL |
| **Production JWK Set URL** | Your JWKS URL for production | e.g., raw GitHub URL |
| **Sandbox Client Secret** | Generate or Store Hash | For client_secret auth method |

### 4. Test page

After saving, you'll see your **Non-Production Client ID**. The app is now in Draft status.

- Click **"Ready for Sandbox"** to enable sandbox testing
- Changes take up to 1 hour to sync to the sandbox
- Test with sandbox credentials: `fhircamila` / `epicepic1`

### 5. Mark Ready for Production

Once sandbox testing passes:

1. Navigate back to your app on the Build Apps page
2. Fill in any remaining required fields (summary, description, Data Use Questionnaire)
3. Check the compliance checkbox
4. Click **"Save and Ready for Production"**

**Important:** After marking ready for production, you cannot change the app's API
selections or most settings. You can still modify redirect URIs and JWK Set URLs.

### 6. Production distribution

After marking ready:

- If you selected **USCDI v3** automatic distribution, Epic organizations will
  automatically request your app. You'll see "Client ID Requests" appear.
- Go to **"Review & Manage Downloads"** to activate each organization:
  1. Activate for **Non-Production** first (required before production)
  2. Then activate for **Production**
  3. Each activation may take up to 12 hours (1 business day) to sync
- For confidential clients, you'll need to provide credentials (client secret or JWK Set URL)
  during activation.

## API Endpoints

For the `confidential` app, select **all patient-facing R4 endpoints** in the
"Incoming APIs" list. This ensures complete data access across all organizations
without needing to guess which subresources contain your data.

Epic's API catalog has 600+ endpoints organized by resource type and subresource
(e.g., `Condition.Search (Problems)`, `Condition.Search (Encounter Diagnosis)`).
A single FHIR search for `Condition` may touch multiple subresources — if any are
missing from your app registration, Epic returns partial results with a 4119
OperationOutcome warning ("may not contain the entire record").

**Important:** Once an app is marked "Ready for Production," you cannot change its
API selections. If you need to add endpoints, you must register a new app.

> **Note:** Without the Binary.Read endpoints, note and report content will return 403.
> Without Observation.Read (Labs), the dedup logic cannot fetch referenced observations.
> These are separate from the Search endpoints — both are needed.

## Scopes

The app requests OAuth scopes during authorization. The full list is in `ehr_import/auth.py`
(SCOPES constant). It includes `patient/<Resource>.read` for every resource type the app
is registered for.

You don't configure scopes during registration — they're requested at runtime in the
authorization URL. Epic grants them based on which APIs are selected on the app registration.

**Important:** If you register an API endpoint but don't request its corresponding scope,
some Epic organizations may return empty results instead of an error. Always keep the
scope list in sync with your registered APIs.

## Finding Your Provider's FHIR Endpoint

Each health system publishes a FHIR base URL. Discover it from the MyChart URL:

```
https://<mychart-host>/MyChart/.well-known/smart-configuration
```

Or look it up on: https://open.epic.com/MyApps/Endpoints

The `discover.py` script automates this.

## Programmatic Access to Epic API Specifications

The fhir.epic.com website loads content dynamically with JavaScript, but the underlying
data is available via JSON endpoints.

### API Catalog (no auth required)

```
GET https://fhir.epic.com/Specifications/Selections
```

Returns a JSON object with `Data.Items[]` — the full list of 696+ APIs with metadata:
- `Id` — numeric API identifier
- `Name` — e.g., "Binary.Read (Clinical Notes) (R4)"
- `API_GroupName` — grouping (e.g., "Clinical Notes Document Group")
- `API_ALaCarteName` — licensing tier
- `IsUSCDI` — whether it's part of USCDI certification

### Individual API Spec (session cookie required)

```
GET https://fhir.epic.com/Specifications/Api?id={ID}
Cookie: <session cookies>
```

No login is required — visiting the site sets the necessary session cookies automatically.

```sh
# Fetch the catalog and save the session cookie in one step
curl -s -c epic_cookies.txt 'https://fhir.epic.com/Specifications/Selections' -o catalog.json

# Use the cookie to fetch any API spec by ID
curl -s -b epic_cookies.txt 'https://fhir.epic.com/Specifications/Api?id=1044'   # Binary.Read (Clinical Notes)
curl -s -b epic_cookies.txt 'https://fhir.epic.com/Specifications/Api?id=931'    # Patient.Read (Demographics)
```

### Key Findings from Spec Data

- **Don't request scopes for unregistered resources** — if you request a scope (e.g.,
  `patient/Binary.read`) but the corresponding API endpoint isn't selected on your app
  registration, Epic may reject the authorization flow. Always keep the scope list in
  `ehr_import/auth.py` in sync with what's actually registered.
- **`PerformsScopeValidation: false`**: The API catalog reports this for all patient-facing
  R4 resources. This means the server doesn't check scopes when serving data — access is
  controlled by app registration. But scopes still matter for the auth flow itself and
  for the patient consent screen.
- **Sandbox sync delay**: Changes to app registration take up to 1 hour to propagate to the sandbox.
- **Production sync delay**: Activation of org downloads takes up to 12 hours (1 business day).
- **Automatic Client Distribution**: Setting this to "None" unlocks the full endpoint catalog
  but removes self-service activation. Without USCDI v3 (or similar) distribution, each
  provider's IT team must manually request your client ID — impractical for personal use.
