# Spec: Unified Database Schema for Multi-Source Import

## Goal

All clinical data sources should write into a single `ehr_data.db` so build scripts
(timeline_viz, labs_report) query one database. Each row carries a `source` field
identifying its origin.

## Data Sources

| # | Source | Format | Status | Target tables |
|---|--------|--------|--------|---------------|
| 1 | Epic FHIR (BCH, Andover, Tufts, Brigham) | FHIR R4 JSON | ✅ Live | labs, encounters, vitals, conditions, immunizations, medications, notes |
| 2 | Epic EHI export | Clarity TSV dump | 🔜 [Dual-output redesign](ehi-unified-import-spec.md) | labs, encounters, vitals, conditions, immunizations, medications, notes, allergies, messages, family_history, social_history |
| 3 | eCW C-CDA (Allergy & Asthma) | C-CDA R2.1 XML | ✅ Script written | labs, encounters, vitals, conditions, immunizations, medications, treatment_plans, notes |
| 4 | eCW FHIR (healow) | FHIR R4 JSON | 🔜 Future | Same as #1 |

## Schema Changes to `ehr_data.db`

### Add `source` column to all clinical tables

```sql
ALTER TABLE labs ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE encounters ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE vitals ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE conditions ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE immunizations ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE medications ADD COLUMN source TEXT DEFAULT 'fhir_epic';
ALTER TABLE notes ADD COLUMN source TEXT DEFAULT 'fhir_epic';
```

Source values:
- `fhir_epic` — existing FHIR pulls from Epic providers
- `ccda_ecw` — eClinicalWorks C-CDA XML imports
- `fhir_ecw` — future healow FHIR pulls

### notes table update

Add `content_html` and `content_text` columns for resolved narrative content:

```sql
ALTER TABLE notes ADD COLUMN content_html TEXT;
ALTER TABLE notes ADD COLUMN content_text TEXT;
```

- `content_html`: Raw HTML fragment from the C-CDA narrative block (lightweight tables/paragraphs, 2-5 KB per note). Preserves structure for rendering.
- `content_text`: Plain text extraction (collapsed whitespace, tags stripped). For search and non-HTML display.
- Existing FHIR notes already have `content_text`; `content_html` will be NULL for those.

### New table: `treatment_plans`

No FHIR equivalent. Stores encounter-linked diagnosis + treatment plan text
(e.g., "Plan for prednisone 20mg x 7 days..."). Named `treatment_plans` to
avoid collision with the existing `assessments` table (which holds FHIR survey
Observations like screening questionnaires).

```sql
CREATE TABLE IF NOT EXISTS treatment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhir_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    provider TEXT,
    source TEXT NOT NULL,
    date TEXT,
    diagnosis TEXT,
    treatment_notes TEXT,
    section_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fhir_id, patient_id)
);
CREATE INDEX idx_treatment_plans_date ON treatment_plans(date);
CREATE INDEX idx_treatment_plans_patient ON treatment_plans(patient_id);
```

### Unique constraint strategy

Existing tables use `UNIQUE(fhir_id, patient_id)` for deduplication. C-CDA records
don't have FHIR IDs. Options:

**Chosen approach:** For C-CDA imports, generate a synthetic `fhir_id` from clinical
content (not filename/container). This ensures idempotent re-imports regardless of
file renames or re-downloads.

Hash inputs by table:
- **labs**: `ccda:{patient_id}:{date}:{code_display}:{value}`
- **encounters**: `ccda:{patient_id}:{date}:{provider_npi_or_name}`
- **vitals**: `ccda:{patient_id}:{date}:{code_display}:{value}`
- **conditions**: `ccda:{patient_id}:{icd_code}:{onset_date}`
- **immunizations**: `ccda:{patient_id}:{date}:{cvx_code}`
- **medications**: `ccda:{patient_id}:{med_name}:{start_date}`
- **assessments**: `ccda:{patient_id}:{date}:{diagnosis_code}`

The hash is deterministic from the data itself — importing the same C-CDA content
from renamed files, duplicate downloads, or overlapping year ranges produces
identical IDs and is silently deduplicated via `INSERT OR IGNORE`.

## Field Mapping: C-CDA → ehr_data.db

### results → labs

| C-CDA field | ehr_data.db column | Notes |
|-------------|-------------------|-------|
| — | fhir_id | Generated: `ccda:{patient_id}:{date}:{code_display}:{value}` |
| — | patient_id | Auto-detected from C-CDA demographics (or `--patient-id` override) |
| — | provider | `"Allergy & Asthma Specialists"` (or `--provider` override) |
| name | code_display | |
| value | value | |
| unit | unit | |
| reference_range | reference_range | |
| — | status | `"final"` |
| date | effective_date | |
| — | raw_json | NULL (no FHIR resource) |
| — | source | `"ccda_ecw"` |

### encounters → encounters

| C-CDA field | ehr_data.db column |
|-------------|-------------------|
| — | fhir_id | Generated: `ccda:{patient_id}:{date}:{provider_npi_or_name}` |
| — | patient_id | Auto-detected from C-CDA demographics |
| — | provider | `"Allergy & Asthma Specialists"` (or `--provider` override) |
| type | encounter_type | |
| — | status | `"finished"` |
| — | class | `"ambulatory"` |
| date | start_date, effective_date | |
| — | end_date | Same as start (single timestamp) |
| diagnoses[0].display | reason | First diagnosis as reason |
| provider (person) | participant_name | |

### vitals → vitals

| C-CDA field | ehr_data.db column |
|-------------|-------------------|
| name | code_display | |
| value | value | |
| unit | unit | |
| date | effective_date | |

### problems → conditions

| C-CDA field | ehr_data.db column |
|-------------|-------------------|
| display | code_display | |
| status | clinical_status | |
| — | verification_status | `"confirmed"` |
| — | category | `"Problem List Item"` |
| onset | onset_date | |
| resolved | abatement_date | |

### immunizations → immunizations

| C-CDA field | ehr_data.db column |
|-------------|-------------------|
| vaccine | vaccine_name | |
| status | status | |
| date | occurrence_date, effective_date | |
| provider | performer_name | |

## Deduplication

On import, use `INSERT OR IGNORE` with source-native or content-based `fhir_id`.
Re-running any import is idempotent — duplicates are silently skipped.

Cross-source deduplication (same clinical fact in FHIR, EHI, and C-CDA) is
deliberately NOT handled at import time. The same lab result may appear as
multiple rows with different `source` values, `code_display` names (e.g., "CRP"
vs "C REACTIVE PROTEIN (MG/L) IN SER/PLAS"), and slightly different dates.
This is intentional — import preserves everything faithfully.

### Query-time dedup (future: `ehr_import.dedup` module)

Downstream consumers (timeline, labs reports) need a dedup layer. Planned:

- **Component name normalization** — map LOINC long names, FHIR display names, and EHI Clarity names to a canonical form
- **Date-tolerant matching** — same calendar day = likely same draw (handles EHI's per-component result dates vs FHIR's single order timestamp)
- **Source-preference rules** — when duplicates are detected, prefer one source over another (e.g., FHIR for metadata richness, EHI for completeness)
- **Deduplicated view/query builder** — reusable function or SQL view that downstream tools call instead of raw table queries

## Non-goals

- No automated cross-source dedup at import time (keep it simple, handle in queries)
- No changes to FHIR pull pipeline (it already works, just gets the `source` column default)
