#!/usr/bin/env python3
"""Migrate ehr_data.db schema for multi-source support.

Adds:
  - `source` column (TEXT, default 'fhir_epic') to all clinical tables
  - `content_html` column to notes table
  - `assessments` table (new, for C-CDA treatment plan data)

Safe to run multiple times — checks for existing columns/tables before altering.

Usage:
    python migrate_db.py --db "../EHR Import Private/ehr_data.db"
"""

import argparse
import sqlite3
from pathlib import Path


def get_columns(conn, table):
    """Return set of column names for a table."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def migrate(db_path: Path):
    """Run all migrations."""
    conn = sqlite3.connect(db_path)
    print(f"Migrating: {db_path}")

    # Schema version tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    current_version = row[0] if row else 0
    print(f"  Current schema version: {current_version}")

    target_version = 1

    if current_version < 1:
        # --- Migration 1: Multi-source support ---
        print(f"\n  Applying migration 1: multi-source support...")

        # 1. Add `source` column to clinical tables
        tables_needing_source = [
            "labs", "encounters", "vitals", "conditions",
            "immunizations", "medications", "notes",
        ]
        for table in tables_needing_source:
            cols = get_columns(conn, table)
            if "source" not in cols:
                print(f"    Adding 'source' column to {table}...")
                conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT DEFAULT 'fhir_epic'")

        # 2. Add content_html to notes
        notes_cols = get_columns(conn, "notes")
        if "content_html" not in notes_cols:
            print(f"    Adding 'content_html' column to notes...")
            conn.execute("ALTER TABLE notes ADD COLUMN content_html TEXT")

        # 3. Create treatment_plans table
        existing_tables = {row[0] for row in
                           conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "treatment_plans" not in existing_tables:
            print(f"    Creating 'treatment_plans' table...")
            conn.execute("""
                CREATE TABLE treatment_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fhir_id TEXT NOT NULL,
                    patient_id TEXT NOT NULL,
                    provider TEXT,
                    source TEXT NOT NULL,
                    date TEXT,
                    diagnosis TEXT,
                    treatment_notes TEXT,
                    section_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(fhir_id, patient_id)
                )
            """)
            conn.execute("CREATE INDEX idx_treatment_plans_date ON treatment_plans(date)")
            conn.execute("CREATE INDEX idx_treatment_plans_patient ON treatment_plans(patient_id)")

        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (1,))
        conn.commit()
        print(f"    → Schema version: 1")
    else:
        print(f"  Already at version {current_version}, nothing to do.")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate ehr_data.db for multi-source support")
    parser.add_argument("--db", type=Path, required=True, help="Path to ehr_data.db")
    args = parser.parse_args()
    migrate(args.db)
