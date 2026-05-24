# EHR Import — Specification

## Purpose

Enable patients to programmatically export their health records from Epic-based
EHRs into a local, queryable database for personal health tracking and research.

## Goals

1. **One-time bulk export** — pull full history of labs and notes
2. **Incremental updates** — pull only new data since last sync
3. **Multi-provider** — support multiple Epic health systems from one tool
4. **Multi-patient** — support family members via proxy access (same provider, different patients)
5. **Local-first** — all data stays on the user's machine; no cloud dependency
6. **Open source** — shareable client ID; anyone with MyChart can use it

## Non-Goals

- Not a patient portal replacement (no appointment scheduling, messaging, etc.)
- Not a clinical tool (no decision support, no alerts)
- Not a data sharing platform (no upload, no multi-user)

## Data Scope

### Implemented

| Data Type | FHIR Resource | Storage |
|-----------|--------------|---------|
| Lab results | Observation (category: laboratory) | `labs` table |
| Vital signs | Observation (category: vital-signs) | `vitals` table |
| Clinical notes | DocumentReference (category: clinical-note) | `notes` table (with content fetch) |
| Diagnostic reports | DiagnosticReport | `diagnostic_reports` table (with content fetch) |
| Conditions | Condition | `conditions` table |
| Allergies | AllergyIntolerance | `allergies` table (case-deduped) |
| Encounters | Encounter | `encounters` table |
| Medications | MedicationRequest | `medications` table |
| Social history | Observation (category: social-history) | `social_history` table |
| Assessments | Observation (category: survey) | `assessments` table |
| Immunizations | Immunization | `immunizations` table |
| Medication dispenses | MedicationDispense | `medication_dispenses` table |
| Procedures | Procedure | `procedures` table |
| Care plans | CarePlan | `care_plans` table |
| Goals | Goal | `goals` table |

### Not yet implemented

| Data Type | FHIR Resource | Notes |
|-----------|--------------|-------|
| Imaging studies | Media (Study), Binary (Study) | DICOM images if available |
| Study findings | Observation (Study Finding) | Structured imaging findings |

## Authentication

- SMART on FHIR standalone patient launch (OAuth2 authorization code)
- Three auth methods supported (auto-detected from credential files):
  - **Public client** (default): PKCE (S256), no secrets, no refresh tokens — for open-source use
  - **Client secret**: confidential client with shared secret, refresh tokens
  - **JWT assertion** (private_key_jwt): confidential client with RSA key pair, refresh tokens
- HTTPS localhost callback (self-signed cert)
- Two registered apps: public (shared client ID) and confidential (personal use)

## Data Storage

- SQLite database in private directory
- Raw FHIR JSON preserved alongside structured tables
- Per-provider partitioning via `provider` column
- Per-patient partitioning via `patient_id` column (supports family members)
- Sync log for tracking pull history
- Content fetch status tracking on notes and reports (status, detail, URL for retry)
- OperationOutcome resources filtered from FHIR Bundle entries before storage

## Privacy Model

- Source code is public (GitHub)
- Client ID is public (identifies the app, not the user)
- All patient data, tokens, and credentials stored in a private sibling directory
- `.gitignore` prevents accidental commits of private data
- No network calls except to the user's own EHR endpoints

## Future Considerations

- ~~C-CDA XML import (from MyChart "computer-readable export")~~ — partially done: `ehi_import.py` ingests the full EHI export into a separate SQLite DB (raw Clarity schema, not unified with FHIR DB)
- Apple Health integration
- Visualization/analysis tools on top of the local DB
- TEFCA IAS for broader provider coverage
- Async/parallel fetching for large histories
