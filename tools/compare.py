"""
Compare record counts across data sources: EHI export, FHIR API pulls.

Helps answer: "How complete is my FHIR API pull compared to the full record?"

Usage:
    # Compare an EHI export DB against a FHIR pull DB
    python compare.py --ehi ./ehi_export.db --fhir ./ehr_data.db --provider "Boston Children's" --patient <patient_id>

    # Compare two FHIR pull DBs (e.g., proxy vs direct login)
    python compare.py --fhir ./proxy_pull.db --fhir2 ./direct_pull.db --provider "Boston Children's" --patient <patient_id>

    # All three sources
    python compare.py --ehi ./ehi_export.db --fhir ./proxy_pull.db --fhir2 ./direct_pull.db --provider "Boston Children's" --patient <patient_id>

The EHI export DB is produced by ehi_import.py. The FHIR DBs are produced by pull.py.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser(
        description="Compare record counts across EHI export and FHIR API pulls"
    )
    parser.add_argument("--ehi", type=Path, help="EHI export database (from ehi_import.py)")
    parser.add_argument("--fhir", type=Path, help="FHIR pull database (from pull.py)")
    parser.add_argument("--fhir2", type=Path, help="Second FHIR pull database (e.g., different login)")
    parser.add_argument("--provider", required=True, help="Provider name (must match config.json)")
    parser.add_argument("--patient", required=True, help="FHIR patient ID")
    parser.add_argument("--label1", default="FHIR Pull 1", help="Label for --fhir source")
    parser.add_argument("--label2", default="FHIR Pull 2", help="Label for --fhir2 source")

    args = parser.parse_args()

    if not args.ehi and not args.fhir and not args.fhir2:
        parser.error("Provide at least two sources to compare (--ehi, --fhir, --fhir2)")

    # Open databases
    ehi_db = sqlite3.connect(str(args.ehi)) if args.ehi else None
    fhir_db = sqlite3.connect(str(args.fhir)) if args.fhir else None
    fhir2_db = sqlite3.connect(str(args.fhir2)) if args.fhir2 else None

    # Determine columns
    columns = []
    if ehi_db:
        columns.append(("EHI Export", "ehi"))
    if fhir_db:
        columns.append((args.label1, "fhir"))
    if fhir2_db:
        columns.append((args.label2, "fhir2"))

    if len(columns) < 2:
        print("ERROR: Need at least two sources to compare.", file=sys.stderr)
        sys.exit(1)

    # Collect counts
    results = []
    for name, fhir_table, ehi_query in RESOURCE_MAP:
        row = {"name": name}
        if ehi_db:
            row["ehi"] = count_ehi(ehi_db, ehi_query)
        if fhir_db:
            row["fhir"] = count_fhir(fhir_db, fhir_table, args.provider, args.patient) if fhir_table else "—"
        if fhir2_db:
            row["fhir2"] = count_fhir(fhir2_db, fhir_table, args.provider, args.patient) if fhir_table else "—"
        results.append(row)

    # Print table
    col_width = 14
    header_parts = [f"{'Resource':<16}"]
    for label, _ in columns:
        header_parts.append(f"{label:<{col_width}}")
    header_parts.append("Delta")

    print()
    print(f"Comparison: {args.provider} / patient {args.patient[:20]}...")
    print("=" * (16 + col_width * len(columns) + 10))
    print("".join(header_parts))
    print("-" * (16 + col_width * len(columns) + 10))

    for row in results:
        parts = [f"{row['name']:<16}"]
        values = []
        for _, key in columns:
            val = row.get(key, "—")
            parts.append(f"{str(val):<{col_width}}")
            values.append(val)

        # Delta: compare first two numeric columns
        if len(values) >= 2:
            delta = format_delta(values[0], values[1])
        else:
            delta = ""
        parts.append(delta)

        print("".join(parts))

    print("-" * (16 + col_width * len(columns) + 10))
    print()

    # Print notes about interpretation
    if ehi_db and fhir_db:
        print("Notes:")
        print("  • EHI 'native' counts are from this institution's own records")
        print("  • '+Rcvd' rows show data received from external providers (C-CDA)")
        print("  • FHIR may return a mix of native + received depending on resource type")
        print("  • Conditions: EHI = PROBLEM_LIST + PAT_ENC_DX (FHIR deduplicates less)")
        print("  • Medications: EHI = unique meds from PAT_ENC_CURR_MEDS")
        print()

    if fhir_db and fhir2_db:
        print("  • Differences between FHIR pulls indicate user-level access restrictions")
        print("  • Proxy (guardian) accounts may see fewer meds, allergies, social history")
        print()

    # Cleanup
    if ehi_db:
        ehi_db.close()
    if fhir_db:
        fhir_db.close()
    if fhir2_db:
        fhir2_db.close()


if __name__ == "__main__":
    main()
