"""
SQLite database schema and management.

Two-tier storage:
  1. `resources` — generic table holding every FHIR resource as raw JSON
  2. Convenience tables — curated columns for resource types in RESOURCE_CONFIG

The convenience tables are auto-created from resource_config.py definitions.
"""

import json
import sqlite3

from config import DB_PATH


def get_db() -> sqlite3.Connection:
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables (generic + convenience + operational)."""
    from resource_config import RESOURCES

    conn = get_db()
    cursor = conn.cursor()

    # --- Generic resource store ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            label TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            effective_date TEXT,
            reinterpreted INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id, label)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(resource_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_label ON resources(label)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_patient ON resources(patient_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_provider ON resources(provider)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_date ON resources(effective_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_reinterpreted ON resources(reinterpreted)")

    # --- Patients (special — not a generic FHIR search) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            given_name TEXT,
            family_name TEXT,
            birth_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- Convenience tables (auto-generated from config) ---
    seen_tables = set()
    for res in RESOURCES:
        table = res.get("table")
        if not table or table in seen_tables:
            continue
        seen_tables.add(table)

        columns = res.get("columns", {})
        # Build column definitions
        col_defs = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "fhir_id TEXT NOT NULL",
            "patient_id TEXT NOT NULL",
            "provider TEXT NOT NULL",
        ]
        for col_name, _ in columns.items():
            # 'reported' is INTEGER (boolean), everything else is TEXT
            if col_name == "reported":
                col_defs.append(f"{col_name} INTEGER")
            else:
                col_defs.append(f"{col_name} TEXT")

        # Content fetch columns (for resources with attachments)
        if res.get("content_fetch"):
            col_defs.extend([
                "content_text TEXT",
                "content_fetch_status TEXT DEFAULT 'pending'",
                "content_fetch_detail TEXT",
                "content_fetch_url TEXT",
            ])

        col_defs.extend([
            "effective_date TEXT",
            "raw_json TEXT",
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            f"UNIQUE(fhir_id, patient_id)",
        ])

        create_sql = f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)})'
        cursor.execute(create_sql)

        # Standard indexes
        cursor.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_patient" ON "{table}"(patient_id)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_provider" ON "{table}"(provider)')
        cursor.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_date" ON "{table}"(effective_date)')

    # --- Operational tables ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pull_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            severity TEXT,
            warning_code TEXT,
            warning_text TEXT,
            diagnostics TEXT,
            raw_json TEXT,
            pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data_status (
            provider TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            complete INTEGER NOT NULL DEFAULT 1,
            last_pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider, patient_id, resource_type)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            sync_type TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            records_fetched INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_incomplete_resources(db=None) -> list[dict]:
    """Return provider/patient/resource_type combos currently marked incomplete."""
    if db is None:
        db = get_db()
    rows = db.execute("""
        SELECT s.provider, s.patient_id, s.resource_type, s.last_pulled_at,
               COALESCE(p.given_name || ' ' || p.family_name, s.patient_id) as patient_name
        FROM data_status s
        LEFT JOIN patients p ON p.patient_id = s.patient_id
        WHERE s.complete = 0
        ORDER BY s.provider, patient_name, s.resource_type
    """).fetchall()
    return [dict(r) for r in rows]


def print_data_completeness():
    """Print a summary of data completeness across all providers/patients."""
    db = get_db()
    incomplete = get_incomplete_resources(db)

    if not incomplete:
        print("✓ No incomplete data warnings recorded.")
        return

    print("⚠ Incomplete data (server withheld records — likely app registration gap):\n")
    current_provider = None
    for row in incomplete:
        if row["provider"] != current_provider:
            current_provider = row["provider"]
            print(f"  {current_provider}:")
        print(f"    {row['patient_name']:20s}  {row['resource_type']}")
    print(f"\n  Total: {len(incomplete)} gaps across "
          f"{len(set(r['provider'] for r in incomplete))} provider(s)")
    db.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print_data_completeness()
    else:
        init_db()
        print(f"Database initialized at {DB_PATH}")
        print("\nTip: run 'python db.py status' to check data completeness")
