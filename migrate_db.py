#!/usr/bin/env python3
"""Migrate ehr_data.db schema for multi-source support.

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


def get_tables(conn):
    """Return set of table names."""
    return {row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


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
        existing_tables = get_tables(conn)
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

    if current_version < 2:
        # --- Migration 2: EHI unified import support ---
        print(f"\n  Applying migration 2: EHI unified import support...")

        # 1. Add content_rtf to notes
        notes_cols = get_columns(conn, "notes")
        if "content_rtf" not in notes_cols:
            print(f"    Adding 'content_rtf' column to notes...")
            conn.execute("ALTER TABLE notes ADD COLUMN content_rtf TEXT")

        # 2. Add ordering_provider and panel_name to labs
        labs_cols = get_columns(conn, "labs")
        if "ordering_provider" not in labs_cols:
            print(f"    Adding 'ordering_provider' column to labs...")
            conn.execute("ALTER TABLE labs ADD COLUMN ordering_provider TEXT")
        if "panel_name" not in labs_cols:
            print(f"    Adding 'panel_name' column to labs...")
            conn.execute("ALTER TABLE labs ADD COLUMN panel_name TEXT")

        # 3. Add lot_number, manufacturer, administering_location to immunizations
        imm_cols = get_columns(conn, "immunizations")
        if "lot_number" not in imm_cols:
            print(f"    Adding 'lot_number' column to immunizations...")
            conn.execute("ALTER TABLE immunizations ADD COLUMN lot_number TEXT")
        if "manufacturer" not in imm_cols:
            print(f"    Adding 'manufacturer' column to immunizations...")
            conn.execute("ALTER TABLE immunizations ADD COLUMN manufacturer TEXT")
        if "administering_location" not in imm_cols:
            print(f"    Adding 'administering_location' column to immunizations...")
            conn.execute("ALTER TABLE immunizations ADD COLUMN administering_location TEXT")

        # 4. Create messages table
        existing_tables = get_tables(conn)
        if "messages" not in existing_tables:
            print(f"    Creating 'messages' table...")
            conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fhir_id TEXT NOT NULL,
                    patient_id TEXT NOT NULL,
                    provider TEXT,
                    source TEXT NOT NULL,
                    sent_date TEXT,
                    received_date TEXT,
                    subject TEXT,
                    sender TEXT,
                    recipient TEXT,
                    body TEXT,
                    status TEXT,
                    category TEXT,
                    medium TEXT,
                    encounter_id TEXT,
                    in_response_to TEXT,
                    raw_json TEXT,
                    effective_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(fhir_id, patient_id)
                )
            """)
            conn.execute("CREATE INDEX idx_messages_date ON messages(sent_date)")
            conn.execute("CREATE INDEX idx_messages_patient ON messages(patient_id)")

        # 5. Create family_history table
        if "family_history" not in existing_tables:
            print(f"    Creating 'family_history' table...")
            conn.execute("""
                CREATE TABLE family_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fhir_id TEXT NOT NULL,
                    patient_id TEXT NOT NULL,
                    provider TEXT,
                    source TEXT NOT NULL,
                    status TEXT,
                    relation TEXT,
                    relation_name TEXT,
                    relation_sex TEXT,
                    condition TEXT,
                    condition_code TEXT,
                    onset_age TEXT,
                    outcome TEXT,
                    contributed_to_death INTEGER,
                    date TEXT,
                    note TEXT,
                    raw_json TEXT,
                    effective_date TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(fhir_id, patient_id)
                )
            """)
            conn.execute("CREATE INDEX idx_family_history_patient ON family_history(patient_id)")

        # 6. Add source to allergies if not present (may have been missed in migration 1)
        if "allergies" in existing_tables:
            allergy_cols = get_columns(conn, "allergies")
            if "source" not in allergy_cols:
                print(f"    Adding 'source' column to allergies...")
                conn.execute("ALTER TABLE allergies ADD COLUMN source TEXT DEFAULT 'fhir_epic'")

        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (2,))
        conn.commit()
        print(f"    → Schema version: 2")

    if current_version >= 2:
        print(f"  Already at version {current_version}, nothing to do.")

    conn.commit()
    conn.close()
    print("Done.")


def backfill_labs(db_path: Path):
    """Backfill ordering_provider and panel_name for existing FHIR labs from raw_json.

    - ordering_provider: extracted from Observation.performer[0].display
    - panel_name: extracted from DiagnosticReport.code.text where the report's
      result[] references point to the lab's fhir_id
    """
    import json

    conn = sqlite3.connect(db_path)
    print(f"Backfilling labs: {db_path}")

    # 1. Backfill ordering_provider from Observation.performer
    rows = conn.execute(
        "SELECT id, raw_json FROM labs "
        "WHERE source = 'fhir_epic' AND ordering_provider IS NULL AND raw_json IS NOT NULL"
    ).fetchall()

    provider_count = 0
    for row_id, raw in rows:
        try:
            obs = json.loads(raw)
            performers = obs.get("performer", [])
            if performers:
                display = performers[0].get("display")
                if display:
                    conn.execute(
                        "UPDATE labs SET ordering_provider = ? WHERE id = ?",
                        (display, row_id)
                    )
                    provider_count += 1
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"  ordering_provider: backfilled {provider_count} / {len(rows)} rows")

    # 2. Backfill panel_name from DiagnosticReport
    # Build fhir_id → lab row id mapping
    lab_ids = {}
    for row in conn.execute(
        "SELECT id, fhir_id FROM labs WHERE source = 'fhir_epic' AND panel_name IS NULL"
    ).fetchall():
        lab_ids[row[1]] = row[0]

    # Parse DiagnosticReports for result references
    panel_count = 0
    dr_rows = conn.execute(
        "SELECT raw_json FROM diagnostic_reports WHERE raw_json IS NOT NULL"
    ).fetchall()

    for (raw,) in dr_rows:
        try:
            dr = json.loads(raw)
            code_text = dr.get("code", {}).get("text")
            if not code_text:
                # Try coding display
                codings = dr.get("code", {}).get("coding", [])
                for c in codings:
                    if c.get("display"):
                        code_text = c["display"]
                        break
            if not code_text:
                continue

            results = dr.get("result", [])
            for ref in results:
                ref_str = ref.get("reference", "")
                # Reference format: "Observation/{fhir_id}"
                if ref_str.startswith("Observation/"):
                    obs_id = ref_str[len("Observation/"):]
                    if obs_id in lab_ids:
                        conn.execute(
                            "UPDATE labs SET panel_name = ? WHERE id = ?",
                            (code_text, lab_ids[obs_id])
                        )
                        panel_count += 1
        except (json.JSONDecodeError, KeyError):
            pass

    conn.commit()
    conn.close()
    print(f"  panel_name: backfilled {panel_count} rows from {len(dr_rows)} DiagnosticReports")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate ehr_data.db for multi-source support")
    parser.add_argument("--db", type=Path, required=True, help="Path to ehr_data.db")
    parser.add_argument("--backfill-labs", action="store_true",
                        help="Backfill ordering_provider and panel_name from raw_json")
    args = parser.parse_args()

    if args.backfill_labs:
        backfill_labs(args.db)
    else:
        migrate(args.db)
