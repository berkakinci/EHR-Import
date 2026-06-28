#!/usr/bin/env python3
"""Import Epic EHI exports — dual-output (raw + unified).

Produces two databases:
  1. Raw DB (ehi_raw.db) — all tables from the export, losslessly preserved
  2. Unified DB (ehr_data.db) — clinically mappable tables normalized into
     the shared schema (same as FHIR pulls and C-CDA imports)

Usage:
    # Minimal — auto-detects patient, defaults DBs
    python ehi_import.py --source /path/to/Extracted

    # Explicit
    python ehi_import.py --source /path/to/Extracted --db ./ehr_data.db \\
        --raw-db ./ehi_raw.db --provider "Boston Children's" --patient-id <id>

    # Raw-only mode (legacy behavior — just import everything into one DB)
    python ehi_import.py --source /path/to/Extracted --raw-only --raw-db ./ehi_export.db
"""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(
    description="Import an Epic EHI export (dual-output: raw + unified)"
)
parser.add_argument(
    "--source",
    type=Path,
    required=True,
    help="Path to the Extracted/ directory (or EHITables/ directly)",
)
parser.add_argument(
    "--db",
    type=Path,
    default=Path("ehr_data.db"),
    help="Unified database path (default: ./ehr_data.db)",
)
parser.add_argument(
    "--raw-db",
    type=Path,
    default=None,
    help="Raw database path (default: ehi_raw.db next to --source)",
)
parser.add_argument(
    "--provider",
    type=str,
    default=None,
    help="Provider name (auto-detected if not given)",
)
parser.add_argument(
    "--patient-id",
    type=str,
    default=None,
    help="Patient ID in unified DB (auto-detected from PATIENT table)",
)
parser.add_argument(
    "--raw-only",
    action="store_true",
    help="Raw-only mode: import all tables into a single DB (legacy behavior)",
)

args = parser.parse_args()

if args.raw_only:
    from ehr_import.tools.ehi_import import build_database
    db_path = args.raw_db or Path("ehi_export.db")
    build_database(args.source, db_path)
else:
    from ehr_import.tools.ehi_unified_import import build_unified_import
    build_unified_import(
        source_dir=args.source,
        unified_db_path=args.db,
        raw_db_path=args.raw_db,
        provider=args.provider,
        patient_id=args.patient_id,
    )
