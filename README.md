# EHR Import

Download your health records from any Epic MyChart system to your own computer.

You log in with your MyChart credentials, and the tool pulls your data into a local SQLite database. No cloud, no third parties — your records stay on your machine.

Works out of the box. No app registration needed.

> **[View on GitHub](https://github.com/berkakinci/EHR-Import)** — source code, issues, and setup instructions

## Who This Is For

- Patients who want a local copy of their health data
- Parents/guardians pulling records for family members
- Anyone building personal health tools on top of their own EHR data

## What You Get

Labs, clinical notes, medications, conditions, vitals, allergies, encounters, immunizations, procedures, diagnostic reports, and more — 15 resource types total. Stored in a queryable SQLite database.

## Quick Start

```bash
# Set up (one time)
bash setup/setup_env.sh
python setup/verify_setup.py

# Find your provider's API endpoint
python discover.py

# Log in (opens browser)
python auth.py "Your Provider Name"

# Download your records
python pull.py "Your Provider Name"
```

That's it. Your data is now in `ehr_data.db`.

## Adding Your Provider

Any Epic-based health system with MyChart works. Add it to `config.json` and run `python discover.py`. See the [setup guide](setup/README.md) for the config format.

## Full Record Export (EHI)

The FHIR API doesn't return everything. Under the Cures Act, you can request your complete record through MyChart (Menu → Health → Request My Records → Computer-Readable Format). Once you have the ZIP:

```bash
python ehi_import.py --source /path/to/Extracted --db ./ehi_export.db
```

See [docs/ehi-import.md](docs/ehi-import.md) for details.

## C-CDA Import

Some providers export records as C-CDA XML (common with eClinicalWorks/healow). Import them into the same unified database:

```bash
python ccda_import.py --source /path/to/ccda-files/
```

Patient identity is auto-detected from the C-CDA demographics. Records are deduplicated by content — safe to re-run on the same files.

## Privacy

All tokens, databases, and API responses are stored outside this repo in a private sibling directory. Nothing sensitive touches git.

---

## Reference

### All Commands

```bash
# Discover endpoints for all configured providers
python discover.py

# Authenticate (opens browser for MyChart login)
python auth.py "Boston Children's"

# Pull all authorized patients at a provider
python pull.py "Boston Children's"

# Pull a specific patient only
python pull.py "Boston Children's" --patient <patient_id>

# Pull only records since a date
python pull.py "Boston Children's" --since 2024-06-01

# Check database status (record counts, completeness)
python db.py status

# Import an EHI export
python ehi_import.py --source /path/to/Extracted --db ./ehi_export.db

# Import C-CDA XML documents into unified DB
python ccda_import.py --source /path/to/ccda-files/

# Run schema migrations (safe to re-run)
python migrate_db.py --db ./ehr_data.db

# Compare FHIR pull vs EHI export
python compare_sources.py --ehi ./ehi_export.db --fhir ./ehr_data.db \
    --provider "Boston Children's" --patient <patient_id>

# Probe access restrictions per sub-resource
python probe_subresources.py "Boston Children's" --patient <patient_id>
```

### Multi-Patient / Family Members

Re-authenticating at the same provider with a different account accumulates tokens (doesn't overwrite). `pull.py` pulls all authorized patients by default; use `--patient` to target one.

This supports family members via proxy access — log in as yourself, select the family member at the account selection screen, and their records are pulled under their own `patient_id`.

### Pre-Configured Providers

| Config key | Organization | MyChart URL |
|------------|--------------|-------------|
| Boston Children's | Boston Children's Hospital | mychart.childrenshospital.org |
| Tufts | Tufts Medicine | mytuftsmed.org |
| Andover Pedi | Pediatric Physicians' Organization at Children's | mychart.chppoc.org |
| Brigham | Mass General Brigham | patientgateway.massgeneralbrigham.org |

Add your own by editing `config.json` — provide a `portal_url` or `hint` for endpoint discovery.

### Authentication Options

| App | Auth methods | Refresh tokens | Use case |
|-----|-------------|----------------|----------|
| `public` (default) | PKCE | ✗ (re-login each session) | No secrets needed — just clone and run |
| `confidential` | JWT assertion, client secret | ✓ | Persistent access without re-login |

### What Gets Pulled

| Data | FHIR Resource | DB Table |
|------|--------------|----------|
| Lab results | Observation (laboratory) | `labs` |
| Vital signs | Observation (vital-signs) | `vitals` |
| Clinical notes | DocumentReference | `notes` |
| Diagnostic reports | DiagnosticReport | `diagnostic_reports` |
| Conditions | Condition | `conditions` |
| Allergies | AllergyIntolerance | `allergies` |
| Encounters | Encounter | `encounters` |
| Medications | MedicationRequest | `medications` |
| Social history | Observation (social-history) | `social_history` |
| Assessments | Observation (survey) | `assessments` |
| Immunizations | Immunization | `immunizations` |
| Medication dispenses | MedicationDispense | `medication_dispenses` |
| Procedures | Procedure | `procedures` |
| Care plans | CarePlan | `care_plans` |
| Goals | Goal | `goals` |

All resources are also stored as raw JSON in a generic `resources` table — query it directly if you need fields not in the convenience tables.

The C-CDA importer also writes to `treatment_plans` (encounter-linked diagnosis and plan text) — a table with no FHIR equivalent.

## Documentation

- [Setup guide](setup/README.md)
- [For Developers](docs/DEVELOPMENT.md)
- [Project spec](docs/SPEC.md)
- [Unified database schema](docs/unified-db-spec.md)
- [Epic app registration](docs/registration-guide.md) (only needed for advanced/confidential client use)
- [EHI export guide](docs/ehi-import.md)
- [eClinicalWorks integration](docs/eclinicalworks-integration.md)
