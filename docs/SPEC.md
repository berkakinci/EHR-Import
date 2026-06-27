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

15 FHIR resource types stored in convenience tables (labs, vitals, notes, diagnostic
reports, conditions, allergies, encounters, medications, social history, assessments,
immunizations, medication dispenses, procedures, care plans, goals). See the
[README](../README.md#what-gets-pulled) for the full mapping table.

Additionally, C-CDA imports write to `treatment_plans` (no FHIR equivalent).

### Not yet implemented

| Data Type | FHIR Resource | Notes |
|-----------|--------------|-------|
| Imaging studies | Media (Study), Binary (Study) | DICOM images if available |
| Study findings | Observation (Study Finding) | Structured imaging findings |

## Authentication

SMART on FHIR standalone patient launch (OAuth2). Public client (PKCE) for open-source
use; confidential client (JWT assertion) for persistent access with refresh tokens.
See [Authentication](authentication.md) for full details.

## Data Storage

- SQLite database (`ehr_data.db`), local-first (private sibling directory)
- Multi-source (`source` column), multi-provider, multi-patient
- Two-tier: generic `resources` table (raw JSON) + convenience tables (curated columns)
- Schema versioned with forward-only migrations

See [Unified Database Spec](unified-db-spec.md) for full schema, field mappings,
and deduplication strategy. See [DEVELOPMENT.md](DEVELOPMENT.md#database-schema)
for the convenience table definitions.

## Privacy Model

- Source code is public (GitHub)
- Client ID is public (identifies the app, not the user)
- All patient data, tokens, and credentials stored in a private sibling directory
- `.gitignore` prevents accidental commits of private data
- No network calls except to the user's own EHR endpoints

## Multi-Source Import

The unified database accepts data from multiple source types, not just FHIR:

| # | Source | Format | Importer | Status |
|---|--------|--------|----------|--------|
| 1 | Epic FHIR (BCH, Andover Pedi, Tufts, Brigham) | FHIR R4 JSON | `pull.py` | ✅ Live |
| 2 | eClinicalWorks C-CDA (Allergy & Asthma) | C-CDA R2.1 XML | `ccda_import.py` | ✅ Live |
| 3 | BCH EHI export | Clarity TSV dump | `ehi_import.py` | ✅ Separate DB (ad-hoc reference) |
| 4 | eClinicalWorks FHIR (healow) | FHIR R4 JSON | `pull.py` | 🔜 Planned |

Schema design, field mappings, and deduplication strategy are documented in the
[Unified Database Spec](unified-db-spec.md). The eClinicalWorks FHIR integration
plan (auth abstraction, config changes, testing) is in the
[eClinicalWorks Integration Plan](eclinicalworks-integration.md).

## Future Considerations

- Apple Health integration
- Visualization/analysis tools on top of the local DB
- TEFCA IAS for broader provider coverage (Correspondences, Radiology Results)
- Async/parallel fetching for large histories
- healow/eClinicalWorks FHIR registration and pull (see [integration plan](eclinicalworks-integration.md))

## Related Specs

| Document | Scope |
|----------|-------|
| [Unified Database Spec](unified-db-spec.md) | Multi-source schema design, field mappings, deduplication strategy, migration history |
| [eClinicalWorks Integration Plan](eclinicalworks-integration.md) | healow FHIR auth, config changes, implementation phases, open questions |
| [Access Restrictions](access-restrictions.md) | OperationOutcome codes, data gaps, FHIR vs EHI comparison |
| [Authentication](authentication.md) | OAuth2 methods, JWT setup, scope behavior, production distribution |
