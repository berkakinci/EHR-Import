# EHR Import

Pull your personal health records (labs, clinical notes) from Epic-based EHRs (Electronic Health Records) into a local database.

Uses the FHIR R4 API with SMART on FHIR authentication — you log in with your MyChart credentials and the app downloads your data. Everything stays on your machine.

No app registration needed — the included client ID works for any Epic MyChart system.

## Quick Start

```bash
# 1. Set up environment and install dependencies
bash setup/setup_env.sh

# 2. Verify configuration
python setup/verify_setup.py

# 3. Discover your provider's FHIR endpoints
python discover_endpoints.py

# 4. Authenticate (opens browser for MyChart login)
python auth.py "Boston Children's"

# 5. Pull your records (all authorized patients at this provider)
python pull_data.py "Boston Children's"

# 6. Pull a specific patient only
python pull_data.py "Boston Children's" --patient <patient_id>

# 7. Pull only new records since a date
python pull_data.py "Boston Children's" --since 2024-06-01
```

## Authentication

Each app in `config.json` declares its allowed `auth_methods` (tried in order during token exchange):

| App | Auth methods | Refresh tokens | Notes |
|-----|-------------|----------------|-------|
| `public` (default for open-source) | PKCE | ✗ (re-login each session) | No secrets needed |
| `confidential` (personal use) | JWT assertion, client secret | ✓ | All patient-facing R4 APIs |

For most users, the default public app works — just clone and run. For persistent
access without re-login, see [DEVELOPMENT.md](docs/DEVELOPMENT.md) for confidential client setup.

## What You Get

- **Patient demographics** — name, date of birth
- **Labs** — all lab results with values, units, reference ranges, dates
- **Clinical notes** — visit notes, consult notes, discharge summaries (full text content)
- **Diagnostic reports** — imaging, pathology, lab panels with presentedForm content
- **Conditions** — diagnoses, problems, health concerns
- **Vital signs** — height, weight, blood pressure, temperature, etc.
- **Allergies** — allergens, reactions, criticality
- **Encounters** — office visits, telehealth, ED visits, hospitalizations
- **Medications** — active and historical prescriptions with dosage
- **Social history** — smoking status, etc.
- **Assessments** — survey/questionnaire results (PHQ-9, GAD-7, etc.)
- Stored in a local SQLite database you can query however you like
- Raw FHIR JSON preserved alongside structured storage
- OperationOutcome warnings captured in full for forensic analysis
- Multi-patient support — pull records for family members from the same provider

## Supported Providers

Any Epic-based health system with MyChart. Pre-configured:

| Config name | Organization | MyChart URL |
|-------------|--------------|-------------|
| Boston Children's | Boston Children's Hospital | mychart.childrenshospital.org |
| Tufts | Tufts Medicine | mytuftsmed.org |
| Andover Pedi | Pediatric Physicians' Organization at Children's (CHPPOC) | mychart.chppoc.org |

Add your own by editing `config.json`.

## Privacy

All private data (tokens, database, raw API responses) is stored in a separate
directory outside this repo (`../EHR Import Private/` by default). Nothing
sensitive is committed to git. Override the location with `DATA_DIR` in `.env`.
