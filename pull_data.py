"""
Pull labs and clinical notes from a FHIR R4 endpoint.

Fetches Observations (labs), DiagnosticReports, and DocumentReferences (notes),
then stores them in a local SQLite database.
"""

import json
import sys
import base64
from datetime import datetime

import requests

from config import RAW_PULLS_DIR, DB_PATH
from auth import load_tokens, refresh_access_token
from db import get_db, init_db


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


def get_all_pages(base_url: str, resource_path: str, token: str, params: dict = None) -> list:
    """Follow FHIR pagination to get all results."""
    entries = []
    bundle = fhir_get(base_url, resource_path, token, params)

    while True:
        for entry in bundle.get("entry", []):
            entries.append(entry.get("resource", entry))

        # Check for next page
        next_link = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                next_link = link.get("url")
                break

        if not next_link:
            break

        # Fetch next page
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }
        resp = requests.get(next_link, headers=headers, timeout=30)
        resp.raise_for_status()
        bundle = resp.json()

    return entries


def pull_labs(base_url: str, patient_id: str, token: str, since: str = None) -> list:
    """Pull laboratory Observations."""
    params = {
        "patient": patient_id,
        "category": "laboratory",
        "_sort": "-date",
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching lab observations...")
    labs = get_all_pages(base_url, "Observation", token, params)
    print(f"  → {len(labs)} lab results (raw)")
    return labs


def pull_diagnostic_reports(base_url: str, patient_id: str, token: str, since: str = None) -> list:
    """Pull DiagnosticReports (lab panels, pathology, etc.)."""
    params = {
        "patient": patient_id,
        "_sort": "-date",
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching diagnostic reports...")
    reports = get_all_pages(base_url, "DiagnosticReport", token, params)
    print(f"  → {len(reports)} diagnostic reports")
    return reports


def deduplicate_labs_and_reports(
    labs: list, reports: list, base_url: str, token: str
) -> tuple[list, list]:
    """
    Deduplicate labs vs diagnostic reports.

    Epic sometimes returns the same observation in both the lab Observation list
    and as a result reference inside a DiagnosticReport. This function:
    1. Removes from labs any Observation whose code text contains "report" or "path"
       (these are really diagnostic/pathology reports).
    2. Fetches Observation references from DiagnosticReports that aren't already in
       the lab set, and adds non-lab ones to the reports list.

    Returns (cleaned_labs, enriched_reports) where enriched_reports includes
    the text-based diagnostic observations separated out from labs.
    """
    # Build set of known lab observation IDs
    lab_ids = set()
    for obs in labs:
        if obs.get("resourceType") == "Observation":
            lab_ids.add(f"Observation/{obs['id']}")

    # Find DiagnosticReport result references not already in our lab set
    missing_refs = []
    for report in reports:
        if report.get("resourceType") != "DiagnosticReport":
            continue
        for result_ref in report.get("result", []):
            ref = result_ref.get("reference", "")
            if "/" in ref and ref not in lab_ids:
                missing_refs.append(ref)

    # Fetch missing observations from DiagnosticReports
    diagnostic_obs = []
    for ref in missing_refs:
        try:
            obs = fhir_get(base_url, ref, token)
            # Skip if it's actually a lab (category code == "Lab")
            is_lab = any(
                coding.get("code") == "Lab"
                for cat in obs.get("category", [])
                for coding in cat.get("coding", [])
            )
            if not is_lab:
                diagnostic_obs.append(obs)
        except Exception:
            continue

    # Separate pathology/report observations out of the lab list
    cleaned_labs = []
    for obs in labs:
        code_text = obs.get("code", {}).get("text", "").lower()
        if "report" in code_text or "path" in code_text:
            diagnostic_obs.append(obs)
        elif code_text:
            cleaned_labs.append(obs)
        else:
            cleaned_labs.append(obs)

    print(f"  → Deduplication: {len(labs)} raw labs → {len(cleaned_labs)} labs + {len(diagnostic_obs)} diagnostic obs")
    return cleaned_labs, diagnostic_obs


def pull_notes(base_url: str, patient_id: str, token: str, since: str = None) -> list:
    """Pull DocumentReferences (clinical notes)."""
    params = {
        "patient": patient_id,
        "category": "clinical-note",
        "_sort": "-date",
        "_count": "50",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching clinical notes...")
    notes = get_all_pages(base_url, "DocumentReference", token, params)
    print(f"  → {len(notes)} clinical notes")
    return notes


def extract_note_content(doc_ref: dict, base_url: str, token: str) -> str | None:
    """Extract text content from a DocumentReference."""
    for content in doc_ref.get("content", []):
        attachment = content.get("attachment", {})

        # Inline data (base64 encoded)
        if "data" in attachment:
            decoded = base64.b64decode(attachment["data"])
            content_type = attachment.get("contentType", "")
            if "text" in content_type or "html" in content_type:
                return decoded.decode("utf-8", errors="replace")
            # Binary content — store raw
            return decoded.decode("utf-8", errors="replace")

        # URL reference — fetch it
        if "url" in attachment:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": attachment.get("contentType", "text/plain"),
                }
                resp = requests.get(attachment["url"], headers=headers, timeout=30)
                if resp.status_code == 200:
                    return resp.text
            except requests.RequestException:
                pass

    return None


def store_labs(db, labs: list, provider: str):
    """Store lab observations in the database."""
    cursor = db.cursor()
    stored = 0

    for lab in labs:
        fhir_id = lab.get("id", "")
        code = lab.get("code", {}).get("text") or _get_coding_display(lab.get("code", {}))
        value = _extract_value(lab)
        unit = _extract_unit(lab)
        ref_range = _extract_reference_range(lab)
        status = lab.get("status", "")
        effective_date = lab.get("effectiveDateTime") or _get_period_start(lab.get("effectivePeriod"))

        cursor.execute("""
            INSERT OR REPLACE INTO labs
            (fhir_id, provider, code_display, value, unit, reference_range, status, effective_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, provider, code, value, unit, ref_range, status,
            effective_date, json.dumps(lab),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} lab results")


def store_diagnostic_obs(db, diagnostic_obs: list, provider: str):
    """Store diagnostic/pathology text observations in the database."""
    cursor = db.cursor()
    stored = 0

    for obs in diagnostic_obs:
        fhir_id = obs.get("id", "")
        code = obs.get("code", {}).get("text") or _get_coding_display(obs.get("code", {}))
        # These are typically text-based reports (pathology, radiology narratives)
        value_text = obs.get("valueString", "")
        effective_date = obs.get("effectiveDateTime") or _get_period_start(obs.get("effectivePeriod"))
        order = ""
        based_on = obs.get("basedOn", [])
        if based_on:
            order = based_on[0].get("display", "")

        cursor.execute("""
            INSERT OR REPLACE INTO diagnostic_reports
            (fhir_id, provider, code_display, order_name, effective_date, value_text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, provider, code, order, effective_date, value_text, json.dumps(obs),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} diagnostic observations")


def store_notes(db, notes: list, provider: str, base_url: str, token: str):
    """Store clinical notes in the database."""
    cursor = db.cursor()
    stored = 0

    for note in notes:
        fhir_id = note.get("id", "")
        doc_type = _get_coding_display(note.get("type", {}))
        date = note.get("date") or note.get("context", {}).get("period", {}).get("start")
        status = note.get("status", "")
        author = _extract_author(note)

        # Extract the actual note text
        content_text = extract_note_content(note, base_url, token)

        cursor.execute("""
            INSERT OR REPLACE INTO notes
            (fhir_id, provider, doc_type, author, date, status, content_text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, provider, doc_type, author, date, status,
            content_text, json.dumps(note),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} clinical notes")


# --- Helper functions ---

def _get_coding_display(codeable_concept: dict) -> str:
    """Get display text from a CodeableConcept."""
    for coding in codeable_concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return codeable_concept.get("text", "Unknown")


def _extract_value(observation: dict) -> str | None:
    """Extract the value from an Observation resource."""
    if "valueQuantity" in observation:
        return str(observation["valueQuantity"].get("value", ""))
    if "valueString" in observation:
        return observation["valueString"]
    if "valueCodeableConcept" in observation:
        return _get_coding_display(observation["valueCodeableConcept"])
    if "component" in observation:
        # Multi-component (e.g., blood pressure)
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
    if r.get("text"):
        return r["text"]
    return None


def _get_period_start(period: dict | None) -> str | None:
    if period:
        return period.get("start")
    return None


def _extract_author(doc_ref: dict) -> str | None:
    """Extract author name from DocumentReference."""
    authors = doc_ref.get("author", [])
    if authors:
        return authors[0].get("display")
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python pull_data.py <provider_name> [--since YYYY-MM-DD]")
        sys.exit(1)

    provider_name = sys.argv[1]
    since = None
    if "--since" in sys.argv:
        since_idx = sys.argv.index("--since") + 1
        if since_idx < len(sys.argv):
            since = sys.argv[since_idx]

    # Load tokens
    tokens = load_tokens(provider_name)
    if not tokens:
        print(f"No tokens for '{provider_name}'. Run: python auth.py \"{provider_name}\"")
        sys.exit(1)

    access_token = tokens["access_token"]
    base_url = tokens["fhir_base_url"]
    patient_id = tokens.get("patient")

    if not patient_id:
        print("No patient ID in token response. Fetching from Patient resource...")
        # Try to get patient ID from the token's fhirUser claim
        patient_id = "self"  # Some Epic endpoints accept 'self'

    print(f"\n{'='*60}")
    print(f"Pulling data from: {provider_name}")
    print(f"FHIR Base: {base_url}")
    print(f"Patient ID: {patient_id}")
    if since:
        print(f"Since: {since}")
    print(f"{'='*60}\n")

    # Initialize database
    init_db()
    db = get_db()

    try:
        # Pull labs
        labs = pull_labs(base_url, patient_id, access_token, since)

        # Pull diagnostic reports
        reports = pull_diagnostic_reports(base_url, patient_id, access_token, since)

        # Deduplicate: separate true labs from pathology/diagnostic text observations
        labs, diagnostic_obs = deduplicate_labs_and_reports(labs, reports, base_url, access_token)

        store_labs(db, labs, provider_name)

        # Store diagnostic observations (text-based reports like pathology)
        store_diagnostic_obs(db, diagnostic_obs, provider_name)

        # Pull notes
        notes = pull_notes(base_url, patient_id, access_token, since)
        store_notes(db, notes, provider_name, base_url, access_token)

        # Save raw data
        RAW_PULLS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(RAW_PULLS_DIR / f"{provider_name}_{timestamp}.json", "w") as f:
            json.dump({
                "labs": labs,
                "notes": notes,
                "reports": reports,
                "diagnostic_obs": diagnostic_obs,
            }, f, indent=2)

        print(f"\n✓ Done. Raw data saved to {RAW_PULLS_DIR}/")
        print(f"  Database: {DB_PATH}")

    except PermissionError:
        print("\nToken expired. Attempting refresh...")
        try:
            tokens = refresh_access_token(provider_name)
            print("Token refreshed. Please run again.")
        except Exception as e:
            print(f"Refresh failed: {e}")
            print(f"Re-authenticate: python auth.py \"{provider_name}\"")

    finally:
        db.close()


if __name__ == "__main__":
    main()
