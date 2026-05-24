"""
SQLite database schema and management.

Two-tier storage:
  1. `resources` — generic table holding every FHIR resource as raw JSON
  2. Convenience tables — curated columns for resource types in RESOURCES config
"""

import json
import sqlite3

from . import config
from .resources import RESOURCES
from .extract import extract_field, extract_effective_date


class Database:
    """Manages the EHR SQLite database lifecycle and storage operations."""

    def __init__(self, path=None):
        self.path = path or config.db_path
        self.conn = None

    def open(self) -> "Database":
        """Open the database connection."""
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        return self

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()

    def init_tables(self):
        """Create all tables (generic + convenience + operational)."""
        cursor = self.conn.cursor()

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
            col_defs = [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "fhir_id TEXT NOT NULL",
                "patient_id TEXT NOT NULL",
                "provider TEXT NOT NULL",
            ]
            for col_name, _ in columns.items():
                if col_name == "reported":
                    col_defs.append(f"{col_name} INTEGER")
                else:
                    col_defs.append(f"{col_name} TEXT")

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
                "UNIQUE(fhir_id, patient_id)",
            ])

            create_sql = f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(col_defs)})'
            cursor.execute(create_sql)

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

        self.conn.commit()

    # --- Storage operations ---

    def store_resource(self, resource: dict, resource_spec: dict, patient_id: str,
                       provider: str):
        """Store a single FHIR resource into the generic resources table.

        Args:
            resource: The FHIR resource dict
            resource_spec: The resource config entry from RESOURCES
            patient_id: Patient FHIR ID
            provider: Provider name
        """
        fhir_type = resource_spec["fhir_type"]
        label = resource_spec["label"]
        table = resource_spec.get("table")
        date_paths = resource_spec.get("effective_date")

        fhir_id = resource.get("id", "")
        eff_date = extract_effective_date(resource, date_paths)

        # Generic table
        self.conn.execute("""
            INSERT OR REPLACE INTO resources
            (fhir_id, resource_type, label, patient_id, provider, effective_date, reinterpreted, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (fhir_id, fhir_type, label, patient_id, provider, eff_date,
              1 if table else 0, json.dumps(resource)))

    def store_convenience(self, resource: dict, resource_spec: dict, patient_id: str,
                          provider: str, client=None) -> str:
        """Store a resource into its convenience table.

        Returns: "stored", "skipped" (dedup), or "fetch_failed" (content fetch issue).
        """
        table = resource_spec["table"]
        columns = resource_spec.get("columns", {})
        content_field = resource_spec.get("content_fetch")
        date_paths = resource_spec.get("effective_date")

        fhir_id = resource.get("id", "")
        eff_date = extract_effective_date(resource, date_paths)

        col_names = ["fhir_id", "patient_id", "provider"]
        col_values = [fhir_id, patient_id, provider]

        for col_name, spec in columns.items():
            col_names.append(col_name)
            col_values.append(extract_field(resource, spec))

        # Content fetch (if applicable)
        result = "stored"
        if content_field and client:
            content_text, fetch_status, fetch_detail, fetch_url = client.fetch_attachment(
                resource, content_field
            )
            col_names.extend(["content_text", "content_fetch_status", "content_fetch_detail", "content_fetch_url"])
            col_values.extend([content_text, fetch_status, fetch_detail, fetch_url])
            if fetch_status == "fetch_failed":
                result = "fetch_failed"

        col_names.extend(["effective_date", "raw_json"])
        col_values.extend([eff_date, json.dumps(resource)])

        placeholders = ", ".join(["?"] * len(col_names))
        col_list = ", ".join(f'"{c}"' for c in col_names)
        self.conn.execute(
            f'INSERT OR REPLACE INTO "{table}" ({col_list}) VALUES ({placeholders})',
            col_values,
        )
        return result

    def store_patient(self, patient_resource: dict, provider: str, patient_id: str):
        """Store patient demographics."""
        given_name = None
        family_name = None
        for name in patient_resource.get("name", []):
            given_parts = name.get("given", [])
            family = name.get("family")
            if given_parts or family:
                given_name = " ".join(given_parts) if given_parts else None
                family_name = family
                if name.get("use") == "official":
                    break

        birth_date = patient_resource.get("birthDate")

        self.conn.execute("""
            INSERT INTO patients (patient_id, provider, given_name, family_name, birth_date, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(patient_id) DO UPDATE SET
                given_name = excluded.given_name, family_name = excluded.family_name,
                birth_date = excluded.birth_date, raw_json = excluded.raw_json,
                updated_at = CURRENT_TIMESTAMP
        """, (patient_id, provider, given_name, family_name, birth_date, json.dumps(patient_resource)))
        self.conn.commit()
        print(f"  Patient: {given_name or '?'} {family_name or '?'} (DOB: {birth_date or 'unknown'})")

    def handle_warnings(self, warnings: list, label: str, provider: str, patient_id: str):
        """Print and store OperationOutcome warnings from a FHIR search."""
        has_incomplete = False

        for issue in warnings:
            severity = issue.get("severity")
            code = None
            details = issue.get("details", {})
            for coding in details.get("coding", []):
                code = coding.get("code")
                break
            text = details.get("text", "")
            diagnostics = issue.get("diagnostics", "")

            if code == "4119":
                has_incomplete = True
                print(f"  ⚠ {label}: INCOMPLETE — server withholds data")
            elif code == "4101":
                pass
            elif severity in ("error", "warning"):
                display = text or diagnostics
                if display:
                    print(f"  ⚠ {label}: {display}")

            try:
                self.conn.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, severity, warning_code,
                     warning_text, diagnostics, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (provider, patient_id, label, severity, code,
                      text, diagnostics, json.dumps(issue)))
                self.conn.commit()
            except Exception:
                pass

        try:
            prev = self.conn.execute("""
                SELECT complete FROM data_status
                WHERE provider = ? AND patient_id = ? AND resource_type = ?
            """, (provider, patient_id, label)).fetchone()
            was_incomplete = prev and prev[0] == 0

            self.conn.execute("""
                INSERT INTO data_status (provider, patient_id, resource_type, complete, last_pulled_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider, patient_id, resource_type) DO UPDATE SET
                    complete = excluded.complete, last_pulled_at = CURRENT_TIMESTAMP
            """, (provider, patient_id, label, 0 if has_incomplete else 1))

            if was_incomplete and not has_incomplete:
                self.conn.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, warning_code, warning_text)
                    VALUES (?, ?, ?, ?, ?)
                """, (provider, patient_id, label, "resolved",
                      "Previously incomplete data now returning successfully"))
            self.conn.commit()
        except Exception:
            pass

    def load_dedup_keys(self, resource_spec: dict, patient_id: str, provider: str) -> set:
        """Load existing dedup keys from the database for incremental dedup."""
        dedup = resource_spec.get("dedup")
        if not dedup:
            return set()

        if dedup.startswith("case_insensitive:"):
            field_name = dedup.split(":", 1)[1]
            table = resource_spec["table"]
            try:
                rows = self.conn.execute(
                    f'SELECT {field_name} FROM "{table}" WHERE patient_id=? AND provider=?',
                    (patient_id, provider),
                ).fetchall()
                return {row[0].strip().lower() for row in rows if row[0]}
            except Exception:
                return set()

        return set()

    def commit(self):
        """Commit the current transaction."""
        self.conn.commit()

    def get_incomplete_resources(self) -> list[dict]:
        """Return provider/patient/resource_type combos currently marked incomplete."""
        rows = self.conn.execute("""
            SELECT s.provider, s.patient_id, s.resource_type, s.last_pulled_at,
                   COALESCE(p.given_name || ' ' || p.family_name, s.patient_id) as patient_name
            FROM data_status s
            LEFT JOIN patients p ON p.patient_id = s.patient_id
            WHERE s.complete = 0
            ORDER BY s.provider, patient_name, s.resource_type
        """).fetchall()
        return [dict(r) for r in rows]


def should_skip_dedup(resource: dict, resource_spec: dict, seen: set) -> bool:
    """Check if a resource should be skipped based on dedup config.

    Returns True if the resource is a duplicate and should be skipped.
    """
    dedup = resource_spec.get("dedup")
    if not dedup:
        return False

    if dedup.startswith("case_insensitive:"):
        field_name = dedup.split(":", 1)[1]
        col_spec = resource_spec["columns"].get(field_name)
        if not col_spec:
            return False
        val = extract_field(resource, col_spec)
        normalized = val.strip().lower() if val else ""
        if normalized in seen:
            return True
        seen.add(normalized)

    return False


def print_data_completeness():
    """Print a summary of data completeness across all providers/patients."""
    db = Database()
    with db:
        incomplete = db.get_incomplete_resources()

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
