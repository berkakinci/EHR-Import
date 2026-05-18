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
- Binary.Read (Clinical Notes) (R4) — note content (HTML/RTF attachments)
- Binary.Read (Labs) (R4) — lab report document content

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
