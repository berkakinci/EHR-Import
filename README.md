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
python auth.py "Boston Children's Hospital"

# 5. Pull your records (all authorized patients at this provider)
python pull_data.py "Boston Children's Hospital"

# 6. Pull a specific patient only
python pull_data.py "Boston Children's Hospital" --patient <patient_id>

# 7. Pull only new records since a date
python pull_data.py "Boston Children's Hospital" --since 2024-06-01
```

## Authentication

The app auto-detects which auth method to use based on what credential files exist:

| Credential files present | Auth method | Refresh tokens |
|--------------------------|-------------|----------------|
| None (default) | Public client + PKCE | ✗ (re-login each session) |
| `client_secret.txt` | Client secret | ✓ |
| `jwk_private.pem` | JWT assertion | ✓ |

For most users, the default public client works — just clone and run. For persistent
access without re-login, see [DEVELOPMENT.md](docs/DEVELOPMENT.md) for confidential client setup.

## What You Get

- **Patient demographics** — name, date of birth
- **Labs** — all lab results with values, units, reference ranges, dates
- **Clinical notes** — visit notes, consult notes, discharge summaries (full text content)
- **Diagnostic reports** — imaging, pathology, lab panels with presentedForm content
- Stored in a local SQLite database you can query however you like
- Failed content fetches are tracked (status + URL) for easy retry
- Multi-patient support — pull records for family members from the same provider

## Supported Providers

Any Epic-based health system with MyChart. Pre-configured:

| Provider | MyChart URL |
|----------|-------------|
| Boston Children's Hospital | mychart.childrenshospital.org |
| Tufts Medicine | mytuftsmed.org |
| BCH Primary Care (CHPPOC) | mychart.chppoc.org |

Add your own by editing `config.json`.

## Privacy

All private data (tokens, database, raw API responses) is stored in a separate
directory outside this repo (`../EHR Import Private/` by default). Nothing
sensitive is committed to git. Override the location with `DATA_DIR` in `.env`.
