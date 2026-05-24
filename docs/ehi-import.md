# EHI Import — Epic Requested Record Export

## What is an EHI Export?

Under the 21st Century Cures Act, patients can request their complete Electronic Health Information (EHI) from any healthcare provider. Epic-based hospitals produce this as a ZIP file containing:

- **EHITables/** — TSV files (one per Clarity/Caboodle database table)
- **EHITables Schema/** — HTML documentation for each table's columns
- **Rich Text/** — Clinical notes in RTF format
- **Received C-CDA/** — External records received from other providers (XML)
- **Media/** — PDFs, images, scanned documents

This is the *complete* record — far more comprehensive than what the FHIR API returns. It includes billing, scheduling, internal audit trails, and clinical data that the FHIR API may withhold.

## How to Request

1. Log into MyChart
2. Navigate to: Menu → Health → Request My Records (or similar)
3. Select "Computer-Readable Format" or "Electronic Health Information"
4. Wait for processing (typically 1–7 days)
5. Download the ZIP file

## Usage

```bash
# Import clinically relevant tables (default — ~40 key tables)
python ehi_import.py --source /path/to/Extracted/EHITables --db ./ehi_export.db

# Import ALL tables (7000+ tables, larger DB)
python ehi_import.py --source /path/to/Extracted/EHITables --db ./ehi_export.db --all

# Import specific tables only
python ehi_import.py --source /path/to/EHITables --tables ORDER_RESULTS PAT_ENC HNO_INFO
```

## Output

A SQLite database with one table per imported TSV file. All columns are stored as TEXT (the TSV files don't include type information). A `_ehi_metadata` table records import details.

## Key Tables for Clinical Data

| Epic Table | FHIR Equivalent | Contains |
|-----------|----------------|----------|
| `ORDER_RESULTS` | Observation (labs) | Lab result values |
| `RES_COMPONENTS` | Observation (components) | Individual result components |
| `ORDER_PROC` | DiagnosticReport | Orders and procedures |
| `ORDER_MED` | MedicationRequest | Medication orders |
| `PAT_ENC` | Encounter | All patient encounters |
| `HNO_INFO` | DocumentReference | Clinical notes metadata |
| `IP_FLWSHT_MEAS` | Observation (vitals) | Flowsheet measurements |
| `PROBLEM_LIST` | Condition | Problem list |
| `ALLERGY` | AllergyIntolerance | Allergies |
| `PAT_IMMUNIZATIONS` | Immunization | Vaccination records |
| `SOCIAL_HX` | Observation (social) | Social history |
| `FAMILY_HX` | FamilyMemberHistory | Family history |

## Relationship to FHIR API Pull

The EHI export and FHIR API pull produce **different database schemas** and are not directly interchangeable:

- **`pull_data.py`** → `ehr_data.db` — Normalized, curated tables (`labs`, `notes`, `conditions`). Designed for ongoing incremental pulls. Clean column names, FHIR resource IDs.
- **`ehi_import.py`** → `ehi_export.db` — Raw Epic Clarity tables with original column names. One-time bulk import. Complete record but requires Epic schema knowledge to navigate.

The EHI export is useful for:
- **Auditing completeness** — comparing what the FHIR API returns vs. the full record
- **Accessing data the API withholds** — notes, encounters, and other resources flagged with OperationOutcome 4119
- **One-time archival** — a complete snapshot of the record at a point in time

## Comparing Sources

Use `compare_sources.py` to see how complete your FHIR API pull is relative to the full EHI export:

```bash
# Compare EHI export against a FHIR pull
python compare_sources.py \
    --ehi ./ehi_export.db \
    --fhir ../EHR\ Import\ Private/ehr_data.db \
    --provider "Boston Children's" \
    --patient <patient_id>

# Compare two FHIR pulls (e.g., proxy vs direct login)
python compare_sources.py \
    --fhir ./proxy_pull.db \
    --fhir2 ./direct_pull.db \
    --provider "Boston Children's" \
    --patient <patient_id> \
    --label1 "Proxy (guardian)" \
    --label2 "Direct (patient)"

# All three sources
python compare_sources.py \
    --ehi ./ehi_export.db \
    --fhir ./proxy_pull.db \
    --fhir2 ./direct_pull.db \
    --provider "Boston Children's" \
    --patient <patient_id> \
    --label1 "Proxy" --label2 "Direct"
```

## Schema Documentation

Each table's columns are documented in the `EHITables Schema/` directory of the export (HTML files). These describe column names, data types, and relationships. The `ehi_import.py` script imports all columns as TEXT since the TSV format doesn't encode types.
