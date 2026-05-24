"""
Import an Epic EHI (Electronic Health Information) export into SQLite.

The EHI export is a collection of TSV files (one per Clarity/Caboodle table)
produced by Epic's "Requested Record" feature under the 21st Century Cures Act.
Every Epic customer produces this format when a patient requests their full
electronic health record.

This tool imports the TSV files into a queryable SQLite database for analysis,
comparison with FHIR API pulls, or personal health data exploration.

Usage:
    python ehi_import.py --source /path/to/EHITables --db ./ehi_export.db
    python ehi_import.py --source /path/to/EHITables --all   # import ALL tables
    python ehi_import.py --source /path/to/EHITables --tables ORDER_RESULTS PAT_ENC

Defaults (when run from the data directory):
    --source  ./Extracted/EHITables
    --db      ./ehi_export.db
"""

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path


def sanitize_column_name(name: str) -> str:
    """Make a column name safe for SQLite (no spaces, special chars)."""
    # Replace problematic characters
    safe = name.strip().replace(" ", "_").replace("-", "_").replace(".", "_")
    safe = safe.replace("(", "").replace(")", "").replace("/", "_")
    # If it starts with a digit, prefix with underscore
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    # If empty after sanitization, use a placeholder
    if not safe:
        safe = "_unnamed"
    return safe


def import_tsv(db: sqlite3.Connection, tsv_path: Path, table_name: str) -> int:
    """
    Import a single TSV file into a SQLite table.
    
    Returns the number of rows imported, or -1 on error.
    """
    try:
        with open(tsv_path, "r", encoding="utf-8", errors="replace") as f:
            # Sniff the first line for headers
            first_line = f.readline()
            if not first_line.strip():
                return 0
            
            # Parse header
            headers = first_line.rstrip("\n").split("\t")
            columns = [sanitize_column_name(h) for h in headers]
            
            # Deduplicate column names
            seen = {}
            deduped = []
            for col in columns:
                if col in seen:
                    seen[col] += 1
                    deduped.append(f"{col}_{seen[col]}")
                else:
                    seen[col] = 0
                    deduped.append(col)
            columns = deduped
            
            # Create table
            col_defs = ", ".join(f'"{c}" TEXT' for c in columns)
            db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            db.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
            
            # Read remaining lines
            placeholders = ", ".join(["?"] * len(columns))
            insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'
            
            rows = []
            for line in f:
                values = line.rstrip("\n").split("\t")
                # Pad or truncate to match column count
                if len(values) < len(columns):
                    values.extend([""] * (len(columns) - len(values)))
                elif len(values) > len(columns):
                    values = values[:len(columns)]
                rows.append(values)
                
                # Batch insert every 1000 rows
                if len(rows) >= 1000:
                    db.executemany(insert_sql, rows)
                    rows = []
            
            # Insert remaining
            if rows:
                db.executemany(insert_sql, rows)
            
            db.commit()
            return db.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    
    except Exception as e:
        print(f"  ERROR importing {table_name}: {e}", file=sys.stderr)
        return -1


def build_database(source_dir: Path, db_path: Path, tables: list[str] | None = None):
    """
    Build the SQLite database from all TSV files in source_dir.
    
    Args:
        source_dir: Path to the EHITables directory containing .tsv files
        db_path: Path for the output SQLite database
        tables: Optional list of specific table names to import (without .tsv).
                If None, imports all tables with data.
    """
    if not source_dir.exists():
        print(f"ERROR: Source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Find all TSV files
    tsv_files = sorted(source_dir.glob("*.tsv"))
    if not tsv_files:
        print(f"ERROR: No .tsv files found in {source_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Filter to requested tables if specified
    if tables:
        table_set = {t.upper() for t in tables}
        tsv_files = [f for f in tsv_files if f.stem.upper() in table_set]
        if not tsv_files:
            print(f"ERROR: None of the requested tables found", file=sys.stderr)
            sys.exit(1)
    
    print(f"Source: {source_dir}")
    print(f"Database: {db_path}")
    print(f"TSV files found: {len(tsv_files)}")
    print()
    
    # Remove existing DB
    if db_path.exists():
        db_path.unlink()
    
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    
    imported = 0
    skipped = 0
    total_rows = 0
    
    for tsv_path in tsv_files:
        table_name = tsv_path.stem
        
        # Skip empty files (header only)
        size = tsv_path.stat().st_size
        if size < 10:
            skipped += 1
            continue
        
        row_count = import_tsv(db, tsv_path, table_name)
        
        if row_count > 0:
            imported += 1
            total_rows += row_count
            print(f"  ✓ {table_name}: {row_count} rows")
        elif row_count == 0:
            skipped += 1
        else:
            skipped += 1
    
    # Create a metadata table
    db.execute("DROP TABLE IF EXISTS _ehi_metadata")
    db.execute("""
        CREATE TABLE _ehi_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.executemany("INSERT INTO _ehi_metadata VALUES (?, ?)", [
        ("source_dir", str(source_dir)),
        ("tables_imported", str(imported)),
        ("tables_skipped", str(skipped)),
        ("total_rows", str(total_rows)),
        ("total_tsv_files", str(len(tsv_files))),
    ])
    db.commit()
    
    print()
    print(f"Done. Imported {imported} tables ({total_rows} total rows), skipped {skipped} empty tables.")
    print(f"Database: {db_path} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")
    
    db.close()


# --- Clinically relevant tables for comparison with FHIR API ---
# These are the tables most useful for comparing against FHIR R4 resources.
CLINICAL_TABLES = [
    # Labs / Results
    "ORDER_RESULTS",
    "RES_COMPONENTS",
    "ORDER_PROC",
    "ORDER_PROC_2",
    "ORDER_PROC_3",
    
    # Medications
    "ORDER_MED",
    "ORDER_MED_2",
    "ORDER_MED_3",
    "ORDER_MED_4",
    "MEDICATION_NOTES",
    
    # Encounters
    "PAT_ENC",
    "PAT_ENC_2",
    "PAT_ENC_3",
    "PAT_ENC_DX",
    "PAT_ENC_RSN_VISIT",
    "CLARITY_ADT",
    
    # Conditions / Problems
    "PROBLEM_LIST",
    "PROBLEM_LIST_HX",
    "PROBLEM",
    
    # Allergies
    "ALLERGY",
    "ALLERGY_REACTIONS",
    "PAT_ALLERGIES",
    
    # Vitals / Flowsheets
    "IP_FLWSHT_MEAS",
    "IP_FLWSHT_REC",
    "IP_FLOWSHEET_ROWS",
    
    # Notes / Documents
    "HNO_INFO",
    "HNO_INFO_2",
    "NOTE_ENC_INFO",
    
    # Social History
    "SOCIAL_HX",
    "SOCIAL_HX_ALC_USE",
    
    # Immunizations
    "PAT_IMMUNIZATIONS",
    "IMMUNE",
    "IMMUNE_HISTORY",
    
    # Family History
    "FAMILY_HX",
    
    # Demographics
    "PATIENT",
    "PATIENT_2",
    "PATIENT_3",
    "PATIENT_MYC",
    
    # Surgical History
    "SURGICAL_HX",
    "MEDICAL_HX",
]


def main():
    parser = argparse.ArgumentParser(
        description="Build SQLite DB from Epic EHI export TSV files"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parent / "Extracted" / "EHITables",
        help="Path to EHITables directory (default: ./Extracted/EHITables)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).parent / "ehi_export.db",
        help="Output database path (default: ./ehi_export.db)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Import ALL tables (not just clinical subset). Warning: may be large.",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        help="Specific table names to import (space-separated)",
    )
    
    args = parser.parse_args()
    
    if args.tables:
        tables = args.tables
    elif args.all:
        tables = None  # None means all
    else:
        tables = CLINICAL_TABLES
    
    build_database(args.source, args.db, tables)


if __name__ == "__main__":
    main()
