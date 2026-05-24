#!/usr/bin/env python3
"""Compare record counts across EHI export and FHIR API pulls."""
import argparse
import sqlite3
import sys
from pathlib import Path

from ehr_import.tools.compare import RESOURCE_MAP, count_fhir, count_ehi, format_delta

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
