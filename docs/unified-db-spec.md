# Spec: Unified Database Schema for Multi-Source Import

## Goal

All clinical data sources should write into a single `ehr_data.db` so build scripts
(timeline_viz, labs_report) query one database. Each row carries a `source` field
identifying its origin.

## Data Sources

| # | Source | Format | Status | Target tables |
|---|--------|--------|--------|---------------|
| 1 | Epic FHIR (BCH, Andover, Tufts, Brigham) | FHIR R4 JSON | ✅ Live | labs, encounters, vitals, conditions, immunizations, medications, notes |
| 2 | BCH EHI export | Clarity TSV dump | ✅ Imported (separate DB) | Consulted ad-hoc, not merged |
| 3 | eCW C-CDA (Allergy & Asthma) | C-CDA R2.1 XML | ✅ Script written | labs, encounters, vitals, conditions, immunizations, medications, assessments |
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

On import, use `INSERT OR IGNORE` with the content-based synthetic fhir_id.
Re-running the C-CDA import is idempotent — duplicates are silently skipped,
even if source files are renamed or re-downloaded.

Cross-source deduplication (same lab result in both FHIR and C-CDA) is handled
by the build scripts at query time — they can `GROUP BY effective_date, code_display, value`
or prefer one source over another.

## Implementation Tasks

All items completed as of 2026-06-27:

1. ~~**Add `source` column** to existing tables (migration script)~~ — `migrate_db.py` v1
2. ~~**Refactor `ccda_import.py`** to write into `ehr_data.db` schema~~ — `ehr_import/tools/ccda_import.py`
3. ~~**Add `treatment_plans` table** to the DB schema~~ — in migration v1
4. ~~**Add patient_id config** for C-CDA sources~~ — auto-detected from C-CDA demographics, with `--patient-id` override
5. ~~**Generate synthetic fhir_ids** for C-CDA records~~ — content-based hashing (see "Unique constraint strategy" above)
6. ~~**Test**: import C-CDA, verify labs appear in build output~~ — 237 records imported from Allergy & Asthma 2008-2026
7. ~~**Update build scripts** if needed~~ — compatible, no changes required

## Non-goals

- EHI export remains a separate DB (too different, ad-hoc reference use)
- No automated cross-source dedup at import time (keep it simple, handle in queries)
- No changes to FHIR pull pipeline (it already works, just gets the `source` column default)
