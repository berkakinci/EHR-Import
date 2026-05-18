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

### Primary (implemented)

| Data Type | FHIR Resource | Storage |
|-----------|--------------|---------|
| Lab results | Observation (category: laboratory) | `labs` table |
| Clinical notes | DocumentReference (category: clinical-note) | `notes` table (with content fetch tracking) |
| Diagnostic reports | DiagnosticReport (presentedForm + result refs) | `diagnostic_reports` table (with content fetch tracking) |

### Secondary (registered, not yet pulled)

| Data Type | FHIR Resource | Notes |
|-----------|--------------|-------|
| Vital signs | Observation (category: vital-signs) | BP, HR, temp, weight |
| Conditions | Condition | Problem list, diagnoses |
| Encounters | Encounter | Visit history |
| Social history | Observation (category: social-history) | Smoking, alcohol, etc. |
| Assessments | Observation (category: survey) | PHQ-9, GAD-7, etc. |
| Allergies | AllergyIntolerance | If registered |
| Medications | MedicationRequest | If registered |

## Authentication

- SMART on FHIR standalone patient launch (OAuth2 authorization code)
- Confidential client with client secret
- Rolling refresh tokens for persistent access
- HTTPS localhost callback (self-signed cert)

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

- C-CDA XML import (from MyChart "computer-readable export")
- Apple Health integration
- Visualization/analysis tools on top of the local DB
- TEFCA IAS for broader provider coverage
- Async/parallel fetching for large histories
