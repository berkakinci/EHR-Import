"""
Import an Epic EHI (Electronic Health Information) export into SQLite.

The EHI export is produced by Epic's "Requested Record" feature under the
21st Century Cures Act. It contains:

  - EHITables/         — TSV files (one per Clarity/Caboodle table)
  - EHITables Schema/  — HTML documentation for each table
  - Rich Text/         — Clinical notes in RTF format
  - Received C-CDA/    — External records from other providers (XML)
  - Media/             — PDFs, images, scanned documents

This tool walks the entire export and imports everything into a single SQLite
database. TSV files become tables; binary/text files (RTF, XML, PDF, etc.) are
stored in a unified _files table with their content as BLOBs.

Usage:
    python ehi_import.py --source /path/to/Extracted --db ./ehi_export.db
    python ehi_import.py --source /path/to/Extracted/EHITables --db ./ehi_export.db  # TSV-only

The tool auto-detects whether --source points to the top-level Extracted/
directory or directly to the EHITables/ subdirectory.
"""

import sqlite3
import sys
import time
from pathlib import Path


def sanitize_column_name(name: str) -> str:
    """Make a column name safe for SQLite (no spaces, special chars)."""
    safe = name.strip().replace(" ", "_").replace("-", "_").replace(".", "_")
    safe = safe.replace("(", "").replace(")", "").replace("/", "_")
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
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
            first_line = f.readline()
            if not first_line.strip():
                return 0

            headers = first_line.rstrip("\n").split("\t")
            columns = [sanitize_column_name(h) for h in headers]

            # Deduplicate column names
            seen: dict[str, int] = {}
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

            # Bulk insert
            placeholders = ", ".join(["?"] * len(columns))
            insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

            rows = []
            for line in f:
                values = line.rstrip("\n").split("\t")
                if len(values) < len(columns):
                    values.extend([""] * (len(columns) - len(values)))
                elif len(values) > len(columns):
                    values = values[: len(columns)]
                rows.append(values)

                if len(rows) >= 5000:
                    db.executemany(insert_sql, rows)
                    rows = []

            if rows:
                db.executemany(insert_sql, rows)

            db.commit()
            return db.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

    except Exception as e:
        print(f"  ERROR importing {table_name}: {e}", file=sys.stderr)
        return -1


def import_files(db: sqlite3.Connection, base_dir: Path, subdir: str) -> int:
    """
    Import all files from a subdirectory into the _files table.

    Stores filename, subdirectory, and raw content as a BLOB.
    Returns number of files imported.
    """
    dir_path = base_dir / subdir
    if not dir_path.exists():
        return 0

    files = sorted(f for f in dir_path.iterdir() if f.is_file())
    if not files:
        return 0

    count = 0
    for file_path in files:
        try:
            content = file_path.read_bytes()
            db.execute(
                "INSERT INTO _files (directory, filename, size_bytes, content) VALUES (?, ?, ?, ?)",
                (subdir, file_path.name, len(content), content),
            )
            count += 1
        except Exception as e:
            print(f"  ERROR reading {subdir}/{file_path.name}: {e}", file=sys.stderr)

    db.commit()
    return count


def build_database(source_dir: Path, db_path: Path):
    """
    Build the SQLite database from an EHI export directory.

    Auto-detects whether source_dir is the top-level Extracted/ directory
    or the EHITables/ subdirectory directly.
    """
    if not source_dir.exists():
        print(f"ERROR: Source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect directory structure
    if (source_dir / "EHITables").is_dir():
        # Top-level Extracted/ directory
        top_dir = source_dir
        tsv_dir = source_dir / "EHITables"
    elif any(source_dir.glob("*.tsv")):
        # Pointed directly at EHITables/
        tsv_dir = source_dir
        top_dir = source_dir.parent
    else:
        print(f"ERROR: No EHITables/ subdirectory or .tsv files found in {source_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all TSV files
    tsv_files = sorted(tsv_dir.glob("*.tsv"))
    if not tsv_files:
        print(f"ERROR: No .tsv files found in {tsv_dir}", file=sys.stderr)
        sys.exit(1)

    # Detect companion directories
    companion_dirs = []
    for name in ["Rich Text", "Received C-CDA", "Media"]:
        if (top_dir / name).is_dir():
            companion_dirs.append(name)

    print(f"Source: {top_dir}")
    print(f"TSV directory: {tsv_dir}")
    print(f"TSV files: {len(tsv_files)}")
    if companion_dirs:
        print(f"Companion dirs: {', '.join(companion_dirs)}")
    print()

    # Remove existing DB
    if db_path.exists():
        db_path.unlink()
    # Also remove WAL/SHM leftovers
    for suffix in ["-wal", "-shm"]:
        leftover = db_path.parent / (db_path.name + suffix)
        if leftover.exists():
            leftover.unlink()

    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-64000")  # 64MB cache

    # Create _files table for binary/text content
    db.execute("""
        CREATE TABLE IF NOT EXISTS _files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            directory TEXT NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER,
            content BLOB
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_files_dir ON _files(directory)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON _files(filename)")

    # --- Import TSV tables ---
    start = time.time()
    imported = 0
    skipped = 0
    total_rows = 0

    for tsv_path in tsv_files:
        table_name = tsv_path.stem

        # Skip truly empty files
        if tsv_path.stat().st_size < 10:
            skipped += 1
            continue

        row_count = import_tsv(db, tsv_path, table_name)

        if row_count > 0:
            imported += 1
            total_rows += row_count
            if row_count >= 100:
                print(f"  ✓ {table_name}: {row_count:,} rows")
        elif row_count == 0:
            skipped += 1
        else:
            skipped += 1

    tsv_elapsed = time.time() - start
    print()
    print(f"TSV import: {imported} tables ({total_rows:,} rows), {skipped} empty — {tsv_elapsed:.1f}s")

    # --- Import companion directories ---
    total_files = 0
    for subdir in companion_dirs:
        count = import_files(db, top_dir, subdir)
        if count:
            print(f"  ✓ {subdir}: {count} files")
            total_files += count

    # --- Metadata ---
    db.execute("DROP TABLE IF EXISTS _ehi_metadata")
    db.execute("""
        CREATE TABLE _ehi_metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.executemany(
        "INSERT INTO _ehi_metadata VALUES (?, ?)",
        [
            ("source_dir", str(top_dir)),
            ("tsv_dir", str(tsv_dir)),
            ("total_tsv_files", str(len(tsv_files))),
            ("tables_imported", str(imported)),
            ("tables_skipped", str(skipped)),
            ("total_rows", str(total_rows)),
            ("companion_files", str(total_files)),
            ("import_time_seconds", f"{time.time() - start:.1f}"),
        ],
    )
    db.commit()

    print()
    elapsed = time.time() - start
    db_size = db_path.stat().st_size / 1024 / 1024
    print(f"Done in {elapsed:.1f}s.")
    print(f"  Tables: {imported} ({total_rows:,} rows)")
    print(f"  Files:  {total_files}")
    print(f"  DB:     {db_path} ({db_size:.1f} MB)")

    db.close()
