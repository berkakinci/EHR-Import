"""
Pull FHIR R4 resources from Epic-based EHRs into a local SQLite database.

Uses resource_config.py to determine what to fetch and how to store it.
All resources go into a generic `resources` table; configured resource types
also get materialized into convenience tables with curated columns.
"""

import json
import sys
import base64
from datetime import datetime

import requests

from config import RAW_PULLS_DIR, DB_PATH
from auth import load_tokens, load_all_tokens_for_provider, refresh_access_token
from db import get_db, init_db
from resource_config import RESOURCES


# =============================================================================
# FHIR HTTP helpers
# =============================================================================

def fhir_get(base_url: str, resource_path: str, token: str, params: dict = None) -> dict:
    """Make an authenticated GET request to a FHIR endpoint."""
    url = f"{base_url.rstrip('/')}/{resource_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/fhir+json",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 401:
        raise PermissionError("Token expired — refresh needed")
    resp.raise_for_status()
    return resp.json()


def get_all_pages(base_url: str, resource_path: str, token: str, params: dict = None) -> tuple[list, list]:
    """Follow FHIR pagination to get all results.

    Returns (entries, warnings) where warnings is a list of OperationOutcome issue dicts.
    """
    entries = []
    warnings = []
    bundle = fhir_get(base_url, resource_path, token, params)

    while True:
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", entry)
            if resource.get("resourceType") == "OperationOutcome":
                for issue in resource.get("issue", []):
                    warnings.append(issue)
                continue
            entries.append(resource)

        next_link = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                next_link = link.get("url")
                break
        if not next_link:
            break

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"}
        resp = requests.get(next_link, headers=headers, timeout=30)
        resp.raise_for_status()
        bundle = resp.json()

    return entries, warnings


# =============================================================================
# Field extraction engine
# =============================================================================

def resolve_path(resource: dict, path: str):
    """Resolve a dotted path with optional array indexing against a resource dict.

    Supports: "field.subfield", "field[0].subfield", "field.coding[0].code"
    Returns None if any part of the path is missing.
    """
    parts = path.strip().split(".")
    current = resource
    for part in parts:
        if current is None:
            return None
        # Handle array index: "field[0]"
        if "[" in part:
            field, idx_str = part.split("[", 1)
            idx = int(idx_str.rstrip("]"))
            current = current.get(field) if isinstance(current, dict) else None
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                return None
        else:
            current = current.get(part) if isinstance(current, dict) else None
    return current


def extract_field(resource: dict, spec: str) -> str | int | None:
    """Extract a field value from a FHIR resource using the config spec syntax.

    Supports fallback chains ("|"), special extractors ("@prefix:"), and plain paths.
    """
    # Handle fallback chains
    if "|" in spec and not spec.startswith("@"):
        for path in spec.split("|"):
            val = resolve_path(resource, path.strip())
            if val is not None:
                return str(val) if not isinstance(val, (int, float, bool)) else val
        return None

    # Special extractors
    if spec.startswith("@coding_display:"):
        field_path = spec[len("@coding_display:"):]
        obj = resolve_path(resource, field_path) if field_path else resource
        return _get_coding_display(obj) if obj else None

    if spec == "@value:":
        return _extract_value(resource)

    if spec == "@unit:":
        return _extract_unit(resource)

    if spec == "@ref_range:":
        return _extract_reference_range(resource)

    if spec == "@author:":
        return _extract_author(resource)

    if spec == "@reactions:":
        return _extract_reactions(resource)

    if spec == "@dosage:":
        return _extract_dosage(resource)

    if spec == "@med_name:":
        return _extract_med_name(resource)

    if spec.startswith("@join:"):
        field_path = spec[len("@join:"):]
        val = resolve_path(resource, field_path)
        if isinstance(val, list):
            return ", ".join(str(v) for v in val if v)
        return str(val) if val else None

    # Plain path
    val = resolve_path(resource, spec)
    if isinstance(val, bool):
        return 1 if val else 0
    if val is not None:
        return str(val)
    return None


def extract_effective_date(resource: dict, date_paths: list[str] | None) -> str | None:
    """Extract the best effective date from a resource using priority list."""
    if not date_paths:
        return None
    for path in date_paths:
        val = resolve_path(resource, path)
        if val:
            return str(val)
    return None


# =============================================================================
# Special extractors (complex logic that can't be expressed as paths)
# =============================================================================

def _get_coding_display(codeable_concept) -> str | None:
    """Get display text from a CodeableConcept or similar."""
    if not codeable_concept or not isinstance(codeable_concept, dict):
        return None
    text = codeable_concept.get("text")
    if text:
        return text
    for coding in codeable_concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return None


def _extract_value(observation: dict) -> str | None:
    """Extract the value from an Observation resource."""
    if "valueQuantity" in observation:
        return str(observation["valueQuantity"].get("value", ""))
    if "valueString" in observation:
        return observation["valueString"]
    if "valueCodeableConcept" in observation:
        return _get_coding_display(observation["valueCodeableConcept"])
    if "component" in observation:
        parts = []
        for comp in observation["component"]:
            name = _get_coding_display(comp.get("code", {}))
            val = _extract_value(comp)
            parts.append(f"{name}: {val}")
        return "; ".join(parts)
    return None


def _extract_unit(observation: dict) -> str | None:
    """Extract unit from an Observation."""
    if "valueQuantity" in observation:
        return observation["valueQuantity"].get("unit") or observation["valueQuantity"].get("code")
    return None


def _extract_reference_range(observation: dict) -> str | None:
    """Extract reference range text."""
    ranges = observation.get("referenceRange", [])
    if not ranges:
        return None
    r = ranges[0]
    low = r.get("low", {}).get("value")
    high = r.get("high", {}).get("value")
    if low is not None and high is not None:
        return f"{low}-{high}"
    return r.get("text")


def _extract_author(doc_ref: dict) -> str | None:
    """Extract author name from DocumentReference."""
    authors = doc_ref.get("author", [])
    return authors[0].get("display") if authors else None


def _extract_reactions(allergy: dict) -> str | None:
    """Extract reactions summary from AllergyIntolerance."""
    reactions = allergy.get("reaction", [])
    if not reactions:
        return None
    parts = []
    for r in reactions:
        for m in r.get("manifestation", []):
            text = m.get("text") or _get_coding_display(m)
            if text:
                parts.append(text)
    return "; ".join(parts) if parts else None


def _extract_dosage(med: dict) -> str | None:
    """Extract dosage text from MedicationRequest."""
    dosage_list = med.get("dosageInstruction", [])
    if not dosage_list:
        return None
    texts = [d.get("text", "") for d in dosage_list if d.get("text")]
    return "; ".join(texts) if texts else None


def _extract_med_name(med: dict) -> str | None:
    """Extract medication name from MedicationRequest or MedicationDispense."""
    concept = med.get("medicationCodeableConcept", {})
    name = concept.get("text") or _get_coding_display(concept)
    if not name and med.get("medicationReference"):
        name = med["medicationReference"].get("display")
    return name


# =============================================================================
# Content fetching (for DocumentReference, DiagnosticReport)
# =============================================================================

def fetch_attachment_content(resource: dict, content_field: str, base_url: str, token: str
                             ) -> tuple[str | None, str, str | None, str | None]:
    """Fetch text content from a resource's attachment field.

    content_field is either "content" (DocumentReference) or "presentedForm" (DiagnosticReport).

    Returns (content_text, fetch_status, fetch_detail, fetch_url).
    """
    if content_field == "content":
        # DocumentReference: content[].attachment
        items = resource.get("content", [])
        attachments = [item.get("attachment", {}) for item in items]
    elif content_field == "presentedForm":
        # DiagnosticReport: presentedForm[]
        attachments = resource.get("presentedForm", [])
    else:
        return None, "no_attachment", None, None

    if not attachments:
        return None, "no_attachment", None, None

    for attachment in attachments:
        # Inline base64 data
        if "data" in attachment:
            decoded = base64.b64decode(attachment["data"])
            text = decoded.decode("utf-8", errors="replace")
            if text.strip():
                return text, "ok", None, None
            else:
                return None, "empty", "Inline data decoded but was empty/whitespace", None

        # URL reference — fetch Binary
        if "url" in attachment:
            fetch_url = attachment["url"]
            if not fetch_url.startswith("http"):
                fetch_url = f"{base_url.rstrip('/')}/{fetch_url}"
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": attachment.get("contentType", "text/plain"),
                }
                resp = requests.get(fetch_url, headers=headers, timeout=30)
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text, "ok", None, None
                elif resp.status_code == 200:
                    return None, "empty", "Binary fetched OK but body was empty", fetch_url
                else:
                    error_body = _extract_error_body(resp)
                    detail = f"HTTP {resp.status_code}"
                    if error_body:
                        detail += f" — {error_body}"
                    return None, "fetch_failed", detail, fetch_url
            except requests.RequestException as e:
                return None, "fetch_failed", f"Request error: {e}", fetch_url

    return None, "no_attachment", "Attachments present but no data or url fields found", None


def _extract_error_body(resp) -> str:
    """Try to extract a useful error message from a failed response."""
    try:
        outcome = resp.json()
        issues = outcome.get("issue", [])
        if issues:
            return "; ".join(
                i.get("diagnostics", i.get("details", {}).get("text", ""))
                for i in issues if i.get("diagnostics") or i.get("details")
            )
    except (ValueError, AttributeError):
        pass
    body = resp.text.strip()
    return body[:200] if body else ""


# =============================================================================
# Deduplication hooks
# =============================================================================

def should_skip_dedup(resource: dict, config: dict, seen: set, db, patient_id: str, provider: str, table: str) -> bool:
    """Check if a resource should be skipped based on dedup config.

    Returns True if the resource is a duplicate and should be skipped.
    """
    dedup = config.get("dedup")
    if not dedup:
        return False

    if dedup.startswith("case_insensitive:"):
        field_name = dedup.split(":", 1)[1]
        col_spec = config["columns"].get(field_name)
        if not col_spec:
            return False
        val = extract_field(resource, col_spec)
        normalized = val.strip().lower() if val else ""
        if normalized in seen:
            return True
        seen.add(normalized)

    return False


def load_existing_dedup_keys(config: dict, db, patient_id: str, provider: str, table: str) -> set:
    """Load existing dedup keys from the database for incremental dedup."""
    dedup = config.get("dedup")
    if not dedup:
        return set()

    if dedup.startswith("case_insensitive:"):
        field_name = dedup.split(":", 1)[1]
        try:
            rows = db.execute(
                f'SELECT {field_name} FROM "{table}" WHERE patient_id=? AND provider=?',
                (patient_id, provider),
            ).fetchall()
            return {row[0].strip().lower() for row in rows if row[0]}
        except Exception:
            return set()

    return set()


# =============================================================================
# Warning handling
# =============================================================================

def handle_warnings(warnings: list, label: str, provider: str, patient_id: str, db=None):
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

        if db:
            try:
                db.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, severity, warning_code,
                     warning_text, diagnostics, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (provider, patient_id, label, severity, code,
                      text, diagnostics, json.dumps(issue)))
                db.commit()
            except Exception:
                pass

    if db:
        try:
            prev = db.execute("""
                SELECT complete FROM data_status
                WHERE provider = ? AND patient_id = ? AND resource_type = ?
            """, (provider, patient_id, label)).fetchone()
            was_incomplete = prev and prev[0] == 0

            db.execute("""
                INSERT INTO data_status (provider, patient_id, resource_type, complete, last_pulled_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider, patient_id, resource_type) DO UPDATE SET
                    complete = excluded.complete, last_pulled_at = CURRENT_TIMESTAMP
            """, (provider, patient_id, label, 0 if has_incomplete else 1))

            if was_incomplete and not has_incomplete:
                db.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, warning_code, warning_text)
                    VALUES (?, ?, ?, ?, ?)
                """, (provider, patient_id, label, "resolved",
                      "Previously incomplete data now returning successfully"))
            db.commit()
        except Exception:
            pass


# =============================================================================
# Generic pull + store
# =============================================================================

def pull_and_store(config: dict, base_url: str, patient_id: str, token: str,
                   provider: str, db, since: str = None) -> tuple[list, list]:
    """Pull a single resource type and store into generic + convenience tables.

    Returns (entries, warnings) for raw data archival.
    """
    fhir_type = config["fhir_type"]
    label = config["label"]
    table = config.get("table")
    search_params = dict(config.get("search_params", {}))
    search_params["patient"] = patient_id
    search_params["_count"] = "100"
    if since and "date" not in search_params:
        search_params["date"] = f"ge{since}"

    print(f"  Fetching {label}...")
    try:
        entries, warnings = get_all_pages(base_url, fhir_type, token, search_params)
    except requests.exceptions.HTTPError as e:
        print(f"  ⚠ {label}: server returned {e.response.status_code} — skipping")
        entries, warnings = [], []
    print(f"  → {len(entries)} {label}")

    handle_warnings(warnings, label, provider, patient_id, db)

    # --- Store into generic resources table ---
    date_paths = config.get("effective_date")
    for resource in entries:
        fhir_id = resource.get("id", "")
        eff_date = extract_effective_date(resource, date_paths)
        db.execute("""
            INSERT OR REPLACE INTO resources
            (fhir_id, resource_type, label, patient_id, provider, effective_date, reinterpreted, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (fhir_id, fhir_type, label, patient_id, provider, eff_date,
              1 if table else 0, json.dumps(resource)))
    db.commit()

    # --- Store into convenience table (if configured) ---
    if table:
        _store_convenience(config, entries, provider, patient_id, base_url, token, db)

    return entries, warnings


def _store_convenience(config: dict, entries: list, provider: str, patient_id: str,
                       base_url: str, token: str, db):
    """Store resources into the convenience table with curated columns."""
    table = config["table"]
    columns = config.get("columns", {})
    content_field = config.get("content_fetch")
    date_paths = config.get("effective_date")

    # Dedup setup
    seen = load_existing_dedup_keys(config, db, patient_id, provider, table)

    stored = 0
    skipped = 0
    fetch_failures = 0

    for resource in entries:
        # Dedup check
        if should_skip_dedup(resource, config, seen, db, patient_id, provider, table):
            skipped += 1
            continue

        fhir_id = resource.get("id", "")
        eff_date = extract_effective_date(resource, date_paths)

        # Extract curated columns
        col_names = ["fhir_id", "patient_id", "provider"]
        col_values = [fhir_id, patient_id, provider]

        for col_name, spec in columns.items():
            col_names.append(col_name)
            col_values.append(extract_field(resource, spec))

        # Content fetch (if applicable)
        if content_field:
            content_text, fetch_status, fetch_detail, fetch_url = fetch_attachment_content(
                resource, content_field, base_url, token
            )
            col_names.extend(["content_text", "content_fetch_status", "content_fetch_detail", "content_fetch_url"])
            col_values.extend([content_text, fetch_status, fetch_detail, fetch_url])
            if fetch_status == "fetch_failed":
                fetch_failures += 1

        col_names.extend(["effective_date", "raw_json"])
        col_values.extend([eff_date, json.dumps(resource)])

        placeholders = ", ".join(["?"] * len(col_names))
        col_list = ", ".join(f'"{c}"' for c in col_names)
        db.execute(
            f'INSERT OR REPLACE INTO "{table}" ({col_list}) VALUES ({placeholders})',
            col_values,
        )
        stored += 1

    db.commit()

    # Print summary
    parts = [f"  → Stored {stored} into {table}"]
    if skipped:
        parts.append(f"(skipped {skipped} case-duplicates)")
    if fetch_failures:
        parts.append(f"({fetch_failures} fetch failures)")
    print(" ".join(parts))


# =============================================================================
# Patient demographics (special case — not a search)
# =============================================================================

def fetch_and_store_patient(base_url: str, patient_id: str, token: str, provider: str, db):
    """Fetch and store patient demographics."""
    try:
        patient = fhir_get(base_url, f"Patient/{patient_id}", token)
    except Exception as e:
        print(f"  ⚠ Could not fetch Patient resource: {e}")
        return

    given_name = None
    family_name = None
    for name in patient.get("name", []):
        given_parts = name.get("given", [])
        family = name.get("family")
        if given_parts or family:
            given_name = " ".join(given_parts) if given_parts else None
            family_name = family
            if name.get("use") == "official":
                break

    birth_date = patient.get("birthDate")

    db.execute("""
        INSERT INTO patients (patient_id, provider, given_name, family_name, birth_date, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(patient_id) DO UPDATE SET
            given_name = excluded.given_name, family_name = excluded.family_name,
            birth_date = excluded.birth_date, raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
    """, (patient_id, provider, given_name, family_name, birth_date, json.dumps(patient)))
    db.commit()
    print(f"  Patient: {given_name or '?'} {family_name or '?'} (DOB: {birth_date or 'unknown'})")


# =============================================================================
# Main pull orchestration
# =============================================================================

def pull_for_patient(provider_name: str, tokens: dict, since: str = None):
    """Pull all configured resources for a single patient."""
    patient_id = tokens.get("patient")

    if not patient_id:
        print("No patient ID in token response.")
        return

    # Proactive token refresh — ensures we have a fresh access token before starting.
    # Skipped for public clients (no refresh token).
    if tokens.get("refresh_token"):
        try:
            tokens = refresh_access_token(provider_name, patient_id)
        except Exception as e:
            print(f"  ⚠ Token refresh failed ({e}), trying with existing token...")

    access_token = tokens["access_token"]
    base_url = tokens["fhir_base_url"]

    print(f"\n{'='*60}")
    print(f"Pulling data from: {provider_name}")
    print(f"FHIR Base: {base_url}")
    print(f"Patient ID: {patient_id}")
    if since:
        print(f"Since: {since}")
    print(f"{'='*60}\n")

    init_db()
    db = get_db()

    try:
        fetch_and_store_patient(base_url, patient_id, access_token, provider_name, db)

        # Pull each configured resource type
        all_raw = {}
        all_warnings = {}

        for config in RESOURCES:
            label = config["label"]
            entries, warnings = pull_and_store(
                config, base_url, patient_id, access_token, provider_name, db, since
            )
            all_raw[label] = entries
            all_warnings[label] = warnings

        # Save raw data archive
        RAW_PULLS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = RAW_PULLS_DIR / f"{provider_name}_{patient_id[:8]}_{timestamp}.json"
        with open(archive_path, "w") as f:
            json.dump({"resources": all_raw, "warnings": all_warnings}, f, indent=2)

        print(f"\n✓ Done. Raw data saved to {archive_path}")
        print(f"  Database: {DB_PATH}")

        # Show completeness summary
        incomplete = db.execute(
            "SELECT DISTINCT resource_type FROM data_status "
            "WHERE provider = ? AND patient_id = ? AND complete = 0",
            (provider_name, patient_id),
        ).fetchall()
        if incomplete:
            print(f"\n  ⚠ Incomplete data ({len(incomplete)} resource types withheld):")
            for (rt,) in incomplete:
                print(f"    - {rt}")

    except PermissionError:
        print("\n✗ Token rejected by server. Re-authenticate:")
        print(f"  python auth.py \"{provider_name}\"")

    db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python pull_data.py <provider_name> [--patient <patient_id>] [--since YYYY-MM-DD]")
        sys.exit(1)

    provider_name = sys.argv[1]
    since = None
    target_patient = None

    if "--since" in sys.argv:
        since_idx = sys.argv.index("--since") + 1
        if since_idx < len(sys.argv):
            since = sys.argv[since_idx]

    if "--patient" in sys.argv:
        patient_idx = sys.argv.index("--patient") + 1
        if patient_idx < len(sys.argv):
            target_patient = sys.argv[patient_idx]

    if target_patient:
        tokens = load_tokens(provider_name, target_patient)
        if not tokens:
            print(f"No tokens for '{provider_name}' patient '{target_patient}'.")
            print(f"Run: python auth.py \"{provider_name}\"")
            sys.exit(1)
        pull_for_patient(provider_name, tokens, since)
    else:
        all_tokens = load_all_tokens_for_provider(provider_name)
        if not all_tokens:
            print(f"No tokens for '{provider_name}'. Run: python auth.py \"{provider_name}\"")
            sys.exit(1)
        print(f"Found {len(all_tokens)} patient(s) for {provider_name}")
        for tokens in all_tokens:
            pull_for_patient(provider_name, tokens, since)


if __name__ == "__main__":
    main()
