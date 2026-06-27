#!/usr/bin/env python3
"""Import C-CDA XML exports into the unified ehr_data.db."""
import argparse
from pathlib import Path

from ehr_import.tools.ccda_import import build_database

parser = argparse.ArgumentParser(
    description="Import C-CDA XML exports into unified EHR database"
)
parser.add_argument(
    "--source",
    type=Path,
    required=True,
    help="Path to directory containing C-CDA XML files",
)
parser.add_argument(
    "--db",
    type=Path,
    default=Path("ehr_data.db"),
    help="Target database path (default: ./ehr_data.db)",
)
parser.add_argument(
    "--patient-id",
    default=None,
    help="Patient identifier (auto-detected from C-CDA demographics if omitted)",
)
parser.add_argument(
    "--provider",
    default="Allergy & Asthma Specialists",
    help="Provider name for the source column (default: Allergy & Asthma Specialists)",
)

args = parser.parse_args()
build_database(args.source, args.db, args.patient_id, args.provider)
