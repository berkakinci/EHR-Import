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
        CREATE TABLE IF NOT EXISTS labs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT UNIQUE NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            value TEXT,
            unit TEXT,
            reference_range TEXT,
            status TEXT,
            effective_date TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT UNIQUE NOT NULL,
            provider TEXT NOT NULL,
            doc_type TEXT,
            author TEXT,
            date TEXT,
            status TEXT,
            content_text TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS diagnostic_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fhir_id TEXT UNIQUE NOT NULL,
            provider TEXT NOT NULL,
            code_display TEXT,
            order_name TEXT,
            effective_date TEXT,
            value_text TEXT,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        CREATE INDEX IF NOT EXISTS idx_labs_provider ON labs(provider);
        CREATE INDEX IF NOT EXISTS idx_labs_date ON labs(effective_date);
        CREATE INDEX IF NOT EXISTS idx_labs_code ON labs(code_display);
        CREATE INDEX IF NOT EXISTS idx_notes_provider ON notes(provider);
        CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);
        CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(doc_type);
        CREATE INDEX IF NOT EXISTS idx_diag_provider ON diagnostic_reports(provider);
        CREATE INDEX IF NOT EXISTS idx_diag_date ON diagnostic_reports(effective_date);
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
