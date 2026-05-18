# Epic FHIR App Registration Guide

## Overview

To access your health records programmatically, you register a "patient-facing" app with Epic.
This gives you a **client ID** that your local script uses to authenticate via your MyChart credentials.

No approval process or review is needed for personal/non-production use.

## Step-by-Step

### 1. Create an account on open.epic.com

- Go to https://open.epic.com
- **Important:** The main site lets you browse with Google login, but the **Build Apps**
  section requires a separate **Epic UserWeb** account.
- Go to the "Build Apps" tab and sign up for a UserWeb account when prompted.
  - It asks for "Legal company name" — your own name works fine for personal use.
  - Use a real email — you'll need to verify it.
- Wait for the verification email, then log in.

### 2. Create a new app

- After logging in, go to **"Build Apps"** → **"Create"**
- Fill in:
  - **App Name**: anything (e.g., "My Health Records")
  - **Application Audience**: select **"Patients"**
  - **Incoming API**: select **"SMART on FHIR"** (not Backend Services)

### 3. Configure the app

Key settings:

| Field | Value |
|-------|-------|
| **SMART on FHIR Version** | R4 |
| **Grant Type** | Authorization Code |
| **Redirect URI** | `http://localhost:8080/callback` |
| **Application Type** | Patient Access |

### 4. Select FHIR Resources (Scopes)

Request these scopes for labs + notes:

- `patient/Observation.read` — lab results, vitals
- `patient/DiagnosticReport.read` — lab panels/reports
- `patient/DocumentReference.read` — clinical notes
- `patient/Patient.read` — demographics (needed for context)
- `patient/Encounter.read` — visit context for notes
- `patient/Condition.read` — problem list (useful context)
- `launch/patient` — standalone patient launch
- `openid fhirUser` — identity token

### 5. Select API Endpoints

Epic requires selecting specific API interactions. Select these R4 endpoints:

**Search (for querying collections):**
- Observation.Search (Labs) (R4)
- Observation.Search (Vital Signs) (R4)
- Observation.Search (Social History) (R4)
- Observation.Search (Assessments) (R4)
- DiagnosticReport.Search (Results) (R4)
- DocumentReference.Search (Clinical Notes) (R4)
- DocumentReference.Search (Labs) (R4)
- Encounter.Search (Patient Chart) (R4)
- Condition.Search (Problems) (R4)
- Condition.Search (Encounter Diagnosis) (R4)
- Condition.Search (Health Concerns) (R4)
- Condition.Search (Care Plan Problem) (R4)
- AllergyIntolerance.Search (Patient Chart) (R4)
- MedicationRequest.Search (Signed Medication Order) (R4)
- Patient.Search (Demographics) (R4)

**Read (for fetching individual resources by ID):**
- Patient.Read (Demographics) (R4) — patient name/DOB lookup
- Observation.Read (Labs) (R4) — dedup cross-references from DiagnosticReports
- Observation.Read (Vital Signs) (R4) — individual vital sign reads
- Observation.Read (Study Finding) (R4) — structured imaging findings
- Binary.Read (Clinical Notes) (R4) — note content (HTML/RTF attachments)
- Binary.Read (Labs) (R4) — lab report document content
- Binary.Read (Study) (R4) — imaging study content (DICOM)
- Media.Read (Study) (R4) — imaging media references
- Media.Search (Study) (R4) — find imaging studies
- Binary.Search (Study) (R4) — find imaging binary content
- Observation.Search (Study Finding) (R4) — find imaging findings

> **Note:** Without the Binary.Read endpoints, note and report content will return 403.
> Without Observation.Read (Labs), the dedup logic cannot fetch referenced observations.
> These are separate from the Search endpoints — both are needed.

### 6. Save and note your Client ID

After saving, Epic gives you a **Non-Production Client ID** immediately.
This works against any Epic sandbox and (importantly) against real MyChart endpoints
for patient-access apps under the Cures Act.

You do NOT need a "production" review for personal patient access — the non-production
client ID works for accessing your own records via MyChart login.

### 7. Find your provider's FHIR endpoint

Each health system publishes a FHIR base URL. You can discover it from the MyChart URL:

```
https://<mychart-host>/MyChart/.well-known/smart-configuration
```

Or look it up on: https://open.epic.com/MyApps/Endpoints

For our providers:
- Boston Children's: check `https://mychart.childrenshospital.org/MyChart/.well-known/smart-configuration`
- Tufts Medicine: check `https://mytuftsmed.org/MyChartPRD/.well-known/smart-configuration`
- CHPPOC: check `https://mychart.chppoc.org/MyChart/.well-known/smart-configuration`

The `discover_endpoints.py` script automates this.

## Notes

- The OAuth flow opens a browser window where you log in with your MyChart credentials
- Tokens are short-lived (usually 1 hour) with a refresh token for ongoing access
- Epic's patient-access API is rate-limited but generous for personal use
- All data returned is YOUR data — no special permissions needed beyond your MyChart login

## Programmatic Access to Epic API Specifications

The fhir.epic.com website loads content dynamically with JavaScript, but the underlying data is available via JSON endpoints.
No authentication is required for the catalog; individual spec details require a session cookie from the site.

### API Catalog (no auth required)

```
GET https://fhir.epic.com/Specifications/Selections
```

Returns a JSON object with `Data.Items[]` — the full list of 696+ APIs with metadata:
- `Id` — numeric API identifier
- `Name` — e.g., "Binary.Read (Clinical Notes) (R4)"
- `API_GroupName` — grouping (e.g., "Clinical Notes Document Group")
- `API_ALaCarteName` — licensing tier (e.g., "Industry-Standard Level 1 Group")
- `Categories` — functional categories
- `IsUSCDI` — whether it's part of USCDI certification
- `HasRequest` — whether Try It examples are available
- `IsFhir` — true for FHIR APIs, false for Epic proprietary

### Individual API Spec (session cookie required)

```
GET https://fhir.epic.com/Specifications/Api?id={ID}
Cookie: <session cookies>
```

Returns full spec details including:
- `Description` — HTML description of the API
- `UrlTemplate` — e.g., "api/FHIR/R4/Binary/{ID}"
- `SampleRequest` / `SampleResponse` — example calls
- `Parameters` — request/response schema
- `PerformsScopeValidation` — whether the API checks OAuth scopes independently
- `UsageScopes` — which access contexts are supported
- `SupportsOAuth2` — OAuth2 support flag
- `Errors` — documented error conditions
- `ChangeLog` — version history

No login is required — visiting the site sets the necessary session cookies automatically.

```sh
# Fetch the catalog and save the session cookie in one step
curl -s -c epic_cookies.txt 'https://fhir.epic.com/Specifications/Selections' -o catalog.json

# Use the cookie to fetch any API spec by ID
curl -s -b epic_cookies.txt 'https://fhir.epic.com/Specifications/Api?id=1044'   # Binary.Read (Clinical Notes)
curl -s -b epic_cookies.txt 'https://fhir.epic.com/Specifications/Api?id=931'    # Patient.Read (Demographics)
curl -s -b epic_cookies.txt 'https://fhir.epic.com/Specifications/Api?id=10139'  # Binary.Read (Labs)
```

### Key Findings from Spec Data

- **Binary resources do NOT perform scope validation** (`PerformsScopeValidation: false`).
  Access is controlled by the app registration (having the API selected), not by an OAuth scope in the token.
  There is no `patient/Binary.read` scope — requesting it will break the OAuth flow.
- **Binary.Read and DocumentReference.Read (Clinical Notes)** are in the same API group ("Clinical Notes Document Group").
  Selecting one in the app registration typically includes the other.
- **Sandbox sync delay**: Changes to app registration take up to 1 hour to propagate to the sandbox.
  This is a common source of 403 errors immediately after adding new APIs.
