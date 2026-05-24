#!/usr/bin/env python3
"""Import Epic EHI exports into SQLite."""
import argparse
from pathlib import Path

from tools.ehi_import import build_database

parser = argparse.ArgumentParser(
    description="Import an Epic EHI export into SQLite (imports everything)"
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
    default=Path("ehi_export.db"),
    help="Output database path (default: ./ehi_export.db)",
)

args = parser.parse_args()
build_database(args.source, args.db)
