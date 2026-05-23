"""
SQLite database for storing EHR data (labs and clinical notes).
"""

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
    """Create tables if they don't exist."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            given_name TEXT,
            family_name TEXT,
            birth_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS labs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            value TEXT,
            unit TEXT,
            reference_range TEXT,
            status TEXT,
            effective_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            doc_type TEXT,
            author TEXT,
            date TEXT,
            status TEXT,
            content_text TEXT,
            content_fetch_status TEXT DEFAULT 'ok',
            content_fetch_detail TEXT,
            content_fetch_url TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS diagnostic_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            status TEXT,
            effective_date TEXT,
            result_observation_ids TEXT,
            content_text TEXT,
            content_fetch_status TEXT DEFAULT 'ok',
            content_fetch_detail TEXT,
            content_fetch_url TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            sync_type TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            records_fetched INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            clinical_status TEXT,
            verification_status TEXT,
            category TEXT,
            onset_date TEXT,
            abatement_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS vitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            value TEXT,
            unit TEXT,
            status TEXT,
            effective_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS allergies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            clinical_status TEXT,
            verification_status TEXT,
            type TEXT,
            category TEXT,
            criticality TEXT,
            onset_date TEXT,
            recorded_date TEXT,
            reaction_text TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            encounter_type TEXT,
            status TEXT,
            class TEXT,
            start_date TEXT,
            end_date TEXT,
            reason TEXT,
            participant_name TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            medication_name TEXT,
            status TEXT,
            intent TEXT,
            authored_on TEXT,
            dosage_text TEXT,
            requester TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS social_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            value TEXT,
            status TEXT,
            effective_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            value TEXT,
            status TEXT,
            effective_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fhir_id, patient_id)
        );

        CREATE TABLE IF NOT EXISTS pull_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            warning_code TEXT,
            warning_text TEXT,
            pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS data_status (
            provider TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            complete INTEGER NOT NULL DEFAULT 1,
            last_pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider, patient_id, resource_type)
        );

        CREATE INDEX IF NOT EXISTS idx_labs_provider ON labs(provider);
        CREATE INDEX IF NOT EXISTS idx_labs_patient ON labs(patient_id);
        CREATE INDEX IF NOT EXISTS idx_labs_date ON labs(effective_date);
        CREATE INDEX IF NOT EXISTS idx_labs_code ON labs(code_display);
        CREATE INDEX IF NOT EXISTS idx_notes_provider ON notes(provider);
        CREATE INDEX IF NOT EXISTS idx_notes_patient ON notes(patient_id);
        CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);
        CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(doc_type);
        CREATE INDEX IF NOT EXISTS idx_diag_provider ON diagnostic_reports(provider);
        CREATE INDEX IF NOT EXISTS idx_diag_patient ON diagnostic_reports(patient_id);
        CREATE INDEX IF NOT EXISTS idx_diag_date ON diagnostic_reports(effective_date);
        CREATE INDEX IF NOT EXISTS idx_conditions_provider ON conditions(provider);
        CREATE INDEX IF NOT EXISTS idx_conditions_patient ON conditions(patient_id);
        CREATE INDEX IF NOT EXISTS idx_conditions_status ON conditions(clinical_status);
        CREATE INDEX IF NOT EXISTS idx_vitals_provider ON vitals(provider);
        CREATE INDEX IF NOT EXISTS idx_vitals_patient ON vitals(patient_id);
        CREATE INDEX IF NOT EXISTS idx_vitals_date ON vitals(effective_date);
        CREATE INDEX IF NOT EXISTS idx_vitals_code ON vitals(code_display);
        CREATE INDEX IF NOT EXISTS idx_allergies_provider ON allergies(provider);
        CREATE INDEX IF NOT EXISTS idx_allergies_patient ON allergies(patient_id);
        CREATE INDEX IF NOT EXISTS idx_encounters_provider ON encounters(provider);
        CREATE INDEX IF NOT EXISTS idx_encounters_patient ON encounters(patient_id);
        CREATE INDEX IF NOT EXISTS idx_encounters_date ON encounters(start_date);
        CREATE INDEX IF NOT EXISTS idx_medications_provider ON medications(provider);
        CREATE INDEX IF NOT EXISTS idx_medications_patient ON medications(patient_id);
        CREATE INDEX IF NOT EXISTS idx_medications_status ON medications(status);
        CREATE INDEX IF NOT EXISTS idx_social_history_provider ON social_history(provider);
        CREATE INDEX IF NOT EXISTS idx_social_history_patient ON social_history(patient_id);
        CREATE INDEX IF NOT EXISTS idx_assessments_provider ON assessments(provider);
        CREATE INDEX IF NOT EXISTS idx_assessments_patient ON assessments(patient_id);
        CREATE INDEX IF NOT EXISTS idx_assessments_date ON assessments(effective_date);
    """)

    conn.commit()
    conn.close()


def get_incomplete_resources(db=None) -> list[dict]:
    """Return a list of provider/patient/resource_type combos currently marked incomplete.

    Each entry: {provider, patient_id, patient_name, resource_type, last_pulled_at}
    """
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
