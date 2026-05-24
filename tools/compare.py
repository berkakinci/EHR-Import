"""
Compare record counts across data sources: EHI export, FHIR API pulls.

Helps answer: "How complete is my FHIR API pull compared to the full record?"

Usage:
    # Compare an EHI export DB against a FHIR pull DB
    python compare_sources.py --ehi ./ehi_export.db --fhir ./ehr_data.db --provider "Boston Children's" --patient <patient_id>

    # Compare two FHIR pull DBs (e.g., proxy vs direct login)
    python compare_sources.py --fhir ./proxy_pull.db --fhir2 ./direct_pull.db --provider "Boston Children's" --patient <patient_id>

    # All three sources
    python compare_sources.py --ehi ./ehi_export.db --fhir ./proxy_pull.db --fhir2 ./direct_pull.db --provider "Boston Children's" --patient <patient_id>

The EHI export DB is produced by ehi_import.py. The FHIR DBs are produced by pull.py.
"""

import sqlite3


# Mapping from FHIR resource types to EHI tables.
#
# EHI data comes in two flavors:
#   - Native: data originating at this institution (ORDER_MED, PAT_ENC, etc.)
#   - Received: data from external providers via C-CDA (DOCS_RCVD_MEDS, etc.)
#
# FHIR may return a mix of both depending on the resource type and app permissions.
#
# Each entry: (display_name, fhir_table, ehi_query)
# ehi_query is a SQL expression that returns a single count from the EHI DB.
# Using raw SQL allows us to deduplicate, join, or count distinct as needed.
RESOURCE_MAP = [
    (
        "Labs",
        "labs",
        "SELECT COUNT(*) FROM ORDER_RESULTS",
    ),
    (
        "Reports",
        "diagnostic_reports",
        "SELECT COUNT(*) FROM ORDER_PROC",
    ),
    (
        "Notes",
        "notes",
        "SELECT COUNT(*) FROM HNO_INFO",
    ),
    (
        "Encounters",
        "encounters",
        "SELECT COUNT(*) FROM PAT_ENC",
    ),
    (
        "Conditions",
        "conditions",
        # FHIR Condition = Problem List + Encounter Diagnoses
        # PROBLEM_LIST has the active problem list; PAT_ENC_DX has per-encounter dx.
        # We sum both since FHIR returns both categories.
        "SELECT (SELECT COUNT(*) FROM PROBLEM_LIST) + (SELECT COUNT(*) FROM PAT_ENC_DX)",
    ),
    (
        "Allergies",
        "allergies",
        "SELECT COUNT(*) FROM ALLERGY",
    ),
    (
        "Vitals",
        "vitals",
        "SELECT COUNT(*) FROM IP_FLWSHT_MEAS",
    ),
    (
        "Medications",
        "medications",
        # FHIR MedicationRequest maps to multiple EHI sources:
        # - ORDER_MED: prescriptions written at this institution
        # - PAT_ENC_CURR_MEDS: current med list (deduplicated by med ID)
        # - PAT_MEDS_HX: medication history entries
        # We use the unique med count from PAT_ENC_CURR_MEDS as the best
        # analog to what FHIR returns (the "current medication list").
        # Also show ORDER_MED for context.
        "SELECT COUNT(DISTINCT CURRENT_MED_ID) FROM PAT_ENC_CURR_MEDS",
    ),
    (
        "Social Hx",
        "social_history",
        "SELECT COUNT(*) FROM SOCIAL_HX",
    ),
    (
        "Assessments",
        "assessments",
        None,  # No clear EHI analog
    ),
    # Additional context rows (EHI-only, no FHIR equivalent)
    (
        "  +Rcvd Meds",
        None,
        "SELECT COUNT(*) FROM DOCS_RCVD_MEDS",
    ),
    (
        "  +Rcvd Dx",
        None,
        "SELECT COUNT(*) FROM DOCS_RCVD_DX",
    ),
    (
        "  +Rcvd Notes",
        None,
        "SELECT COUNT(*) FROM DOCS_RCVD_CLINICAL_NOTES",
    ),
    (
        "  +Med Dispense",
        None,
        "SELECT COUNT(*) FROM MED_DISPENSE",
    ),
    (
        "  +Files",
        None,
        "SELECT COUNT(*) FROM _files",
    ),
]


def count_fhir(db: sqlite3.Connection, table: str, provider: str, patient_id: str) -> int | str:
    """Count rows in a FHIR pull DB table for a specific provider/patient."""
    try:
        row = db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE provider=? AND patient_id=?",
            (provider, patient_id),
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return "—"


def count_ehi(db: sqlite3.Connection, query: str | None) -> int | str:
    """Run a count query against the EHI database."""
    if not query:
        return "—"
    try:
        row = db.execute(query).fetchone()
        return row[0] if row and row[0] else "—"
    except sqlite3.OperationalError:
        return "—"


def format_delta(base, compare) -> str:
    """Format the difference between two counts."""
    if not isinstance(base, int) or not isinstance(compare, int):
        return ""
    diff = compare - base
    if diff == 0:
        return "✓"
    elif diff > 0:
        return f"+{diff}"
    else:
        return str(diff)
