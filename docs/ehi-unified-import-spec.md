# Spec: EHI Unified Import (Dual-Output)

## Goal

Revise `ehi_import.py` to produce two outputs:

1. **Unified DB** (`ehr_data.db`) — EHI data that maps to our schema, with content-based dedup IDs, idempotent re-imports, source = `ehi_epic`.
2. **Raw DB** (`ehi_raw.db`) — everything else (billing, scheduling, audit, workflow tables). Same raw import as today but only for non-mappable tables.

## Mappable Tables

| EHI Table(s) | → Unified Table | fhir_id | Notes |
|---|---|---|---|
| `ORDER_RESULTS` | `labs` | `ehi:{ORDER_PROC_ID}:{LINE}` | Join `CLARITY_COMPONENT` for display if available. Join `ORDER_PROC` + `CLARITY_SER` to populate `ordering_provider` and `panel_name`. Preserve per-component result dates as-is. |
| `ALLERGY` | `allergies` | `ehi:allergy:{ALLERGY_ID}` | |
| `ORDER_MED` | `medications` | `ehi:med:{ORDER_MED_ID}` | |
| `PROBLEM_LIST` | `conditions` | `ehi:problem:{PROBLEM_LIST_ID}` | Join `CLARITY_EDG` for DX_ID → display name. |
| `IP_FLWSHT_MEAS` + `V_EHI_FLO_MEAS_VALUE` | `vitals` | `ehi:vital:{FSD_ID}:{LINE}` | Filter to real vitals only: BP, Pulse, Temp, Height, Weight, SpO2. |
| `HNO_PLAIN_TEXT` + Rich Text/ files | `notes` | `ehi:note:{NOTE_ID}` | RTF stored in `content_rtf`, plain text in `content_text`. |
| `PAT_ENC` (completed only) | `encounters` | `ehi:enc:{PAT_ENC_CSN_ID}` | Only rows with APPT_STATUS = Completed. |
| `IMMUNE` | `immunizations` | `ehi:imm:{IMMUNE_ID}` | Richer than FHIR: includes lot number, manufacturer, administering site/pharmacy. |
| `IB_MESSAGES` + `IB_NOTES` | `messages` (new) | `ehi:msg:{MSG_ID}` | MyChart message threads — clinical communications blocked by FHIR. |
| `FAMILY_HX` | `family_history` (new) | `ehi:fhx:{LINE}:{PAT_ENC_CSN_ID}` | Relation + condition. Join `CLARITY_EDG` if DX_ID present. Also parsed from C-CDA section `10157-6` (Received C-CDAs and standalone imports). |
| `SOCIAL_HX` | `social_history` | `ehi:shx:{field}:{PAT_ENC_CSN_ID}` | Map checkbox fields to code_display/value pairs (same format as FHIR). |
| `Received C-CDA/` files | multiple | Delegated to `ccda_import` | Uses ccda_import's own `ccda:{sha256}` IDs. |

Everything else → raw DB.

**Raw DB strategy**: ALL tables from the EHI go into the raw DB (including the ones we map to unified). The raw DB is the complete, lossless archive. The unified DB extracts and normalizes the clinically useful subset. This means no data is lost even if our mapping misses something — we can always go back to the raw DB.

## Schema Change (Migration 2)

Add to `notes` table:
- `content_rtf TEXT` — original RTF from EHI Rich Text files

Add to `labs` table:
- `ordering_provider TEXT` — name of the provider who ordered the test
- `panel_name TEXT` — parent order description (e.g., "CBC WITH AUTO DIFFERENTIAL")

Add to `immunizations` table:
- `lot_number TEXT`
- `manufacturer TEXT`
- `administering_location TEXT` — pharmacy/clinic name where it was given

New table: `messages`

Maps to FHIR `Communication` resource. Designed to hold both EHI MyChart messages and future FHIR Communication pulls.

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhir_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    provider TEXT,
    source TEXT NOT NULL,
    sent_date TEXT,
    received_date TEXT,
    subject TEXT,
    sender TEXT,
    recipient TEXT,
    body TEXT,
    status TEXT,
    category TEXT,
    medium TEXT,
    encounter_id TEXT,
    in_response_to TEXT,
    raw_json TEXT,
    effective_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fhir_id, patient_id)
);
CREATE INDEX idx_messages_date ON messages(sent_date);
CREATE INDEX idx_messages_patient ON messages(patient_id);
```

New table: `family_history`

Maps to FHIR `FamilyMemberHistory` resource. Each row represents one condition for one family member.

```sql
CREATE TABLE family_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fhir_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    provider TEXT,
    source TEXT NOT NULL,
    status TEXT,
    relation TEXT,
    relation_name TEXT,
    relation_sex TEXT,
    condition TEXT,
    condition_code TEXT,
    onset_age TEXT,
    outcome TEXT,
    contributed_to_death INTEGER,
    date TEXT,
    note TEXT,
    raw_json TEXT,
    effective_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fhir_id, patient_id)
);
CREATE INDEX idx_family_history_patient ON family_history(patient_id);
```

Column behavior by source:
- EHI notes: `content_rtf` = original RTF, `content_html` = NULL
- C-CDA notes: `content_html` = original HTML, `content_rtf` = NULL
- FHIR notes: both NULL (content in `content_text`)
- All sources always populate `content_text` (plain text for search/display)
- EHI labs: `ordering_provider` and `panel_name` populated from `ORDER_PROC` + `CLARITY_SER` joins
- FHIR/C-CDA labs: NULL initially; one-time backfill from `raw_json` (FHIR Observation has `performer`, DiagnosticReport has panel grouping)
- EHI immunizations: `lot_number`, `manufacturer`, `administering_location` populated from `IMMUNE` table
- FHIR immunizations: these fields NULL (could backfill from raw_json if desired)

## Auto-Detection

- **Patient ID**: Read `PATIENT` table from EHI → extract name + DOB → match against `patients` table in unified DB (same approach as ccda_import's `detect_patient_id`).
- **Provider**: Infer from EHI metadata or require `--provider`. Fallback to directory name. The `_ehi_metadata` table or `CLARITY_LOC`/`CLARITY_SER` tables may help.

## Dedup Strategy

Two-layer approach:

### Import time: idempotency only

Each source uses its own native identifiers for the `fhir_id` field + `INSERT OR IGNORE`. Re-running the same import never creates duplicates, but cross-source overlap is deliberately preserved.

| Source | fhir_id format | Guarantees |
|--------|---------------|------------|
| FHIR Epic | Server-assigned FHIR ID | Native |
| C-CDA | `ccda:{sha256(content fields)}` | Content-based, idempotent |
| EHI (structured) | `ehi:{order_proc_id}:{line}` (labs), `ehi:{note_id}` (notes), etc. | Source-native IDs, never loses repeats |
| EHI (received C-CDA) | Delegated to ccda_import (`ccda:{sha256}`) | Same as row 2 |

Using source-native IDs (like `ORDER_PROC_ID + LINE` for labs) means:
- Legitimate repeat tests on the same day (e.g., two CBCs in an ER visit) are always preserved as separate rows.
- Re-importing the same EHI export is idempotent.
- The same clinical fact appearing from multiple sources (FHIR + EHI + received C-CDA) results in multiple rows — that's intentional.

### Query time: cross-source dedup

Build scripts and reports collapse cross-source duplicates using clinical content matching (e.g., `GROUP BY date_day, normalized_component, value` or source-preference rules). This logic lives in one visible place rather than spread across importers with different fuzzy matching rules.

## CLI

```bash
# Minimal — auto-detects patient, defaults DBs
python ehi_import.py --source /path/to/Extracted

# Explicit
python ehi_import.py --source /path/to/Extracted --db ./ehr_data.db --raw-db ./ehi_raw.db --provider "Boston Children's" --patient-id <id>
```

Defaults:
- `--db`: `ehr_data.db` (same as ccda_import)
- `--raw-db`: `ehi_raw.db` (in same directory as `--source`)
- `--provider`: auto-detect from EHI
- `--patient-id`: auto-detect from PATIENT table → match against unified DB

## Received C-CDA Processing

The `Received C-CDA/` directory in an EHI export contains records sent to this provider from other institutions. These are standard C-CDA XML files. The import should:

1. Extract each XML from the `_files` table (or read directly from disk if importing from directory).
2. Run each through `ccda_import.build_database()` (or the lower-level `parse_file` + `insert_rows`).
3. Provider name for received C-CDAs: extract from the C-CDA's `custodian` or `author` organization, not the EHI provider.
4. Patient ID: same as the EHI patient (it's their record).
5. Dedup: handled by ccda_import's existing content-hash IDs — re-importing the same C-CDA is idempotent.

## Implementation Order

1. Schema migration (add `content_rtf` to notes, `ordering_provider` + `panel_name` to labs)
2. Backfill `ordering_provider` and `panel_name` for existing FHIR rows from `raw_json`
3. Refactor existing `ehi_import.py` / `ehr_import/tools/ehi_import.py`:
   - Parse mappable tables → unified DB
   - Parse remaining tables → raw DB
   - RTF → plain text extraction (lightweight, no HTML conversion)
   - Received C-CDA passthrough to ccda_import
4. Update CLI entry point
5. Update docs (unified-db-spec.md, ehi-import.md, README)
