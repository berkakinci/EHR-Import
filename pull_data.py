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
from auth import load_tokens, load_all_tokens_for_provider, refresh_access_token
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
            # Collect OperationOutcome warnings instead of silently dropping
            if resource.get("resourceType") == "OperationOutcome":
                for issue in resource.get("issue", []):
                    warnings.append(issue)
                continue
            entries.append(resource)

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

    return entries, warnings


def handle_warnings(warnings: list, resource_type: str, provider: str, patient_id: str, db=None):
    """Print and store OperationOutcome warnings from a FHIR search.

    All issues are stored unconditionally — this is a forensic log.

    - Appends to pull_warnings (historical log, never deleted).
    - Upserts data_status to reflect current completeness per resource_type.
    """
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
            print(f"  ⚠ {resource_type}: INCOMPLETE — server indicates more data exists "
                  f"but is not available to this app")
        elif code == "4101":
            pass  # "no results" is normal, don't spam
        elif severity in ("error", "warning"):
            display = text or diagnostics
            if display:
                print(f"  ⚠ {resource_type}: {display}")

        # Append to historical log — store everything
        if db:
            try:
                db.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, severity, warning_code,
                     warning_text, diagnostics, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (provider, patient_id, resource_type, severity, code,
                      text, diagnostics, json.dumps(issue)))
                db.commit()
            except Exception:
                pass

    # Update current status
    if db:
        try:
            # Check previous state to detect transitions
            prev = db.execute("""
                SELECT complete FROM data_status
                WHERE provider = ? AND patient_id = ? AND resource_type = ?
            """, (provider, patient_id, resource_type)).fetchone()

            was_incomplete = prev and prev[0] == 0

            db.execute("""
                INSERT INTO data_status (provider, patient_id, resource_type, complete, last_pulled_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider, patient_id, resource_type) DO UPDATE SET
                    complete = excluded.complete,
                    last_pulled_at = CURRENT_TIMESTAMP
            """, (provider, patient_id, resource_type, 0 if has_incomplete else 1))

            # Log the transition back to complete
            if was_incomplete and not has_incomplete:
                db.execute("""
                    INSERT INTO pull_warnings
                    (provider, patient_id, resource_type, warning_code, warning_text)
                    VALUES (?, ?, ?, ?, ?)
                """, (provider, patient_id, resource_type, "resolved",
                      "Previously incomplete data now returning successfully"))

            db.commit()
        except Exception:
            pass


def fetch_patient_demographics(base_url: str, patient_id: str, token: str) -> dict | None:
    """Fetch the Patient resource to get name and demographics."""
    try:
        patient = fhir_get(base_url, f"Patient/{patient_id}", token)
        return patient
    except Exception as e:
        print(f"  ⚠ Could not fetch Patient resource: {e}")
        return None


def store_patient(db, patient_resource: dict, provider: str, patient_id: str):
    """Store or update patient demographics in the database."""
    if not patient_resource:
        return

    # Extract name (use first "official" or first available name)
    given_name = None
    family_name = None
    for name in patient_resource.get("name", []):
        given_parts = name.get("given", [])
        family = name.get("family")
        if given_parts or family:
            given_name = " ".join(given_parts) if given_parts else None
            family_name = family
            if name.get("use") == "official":
                break  # Prefer official name

    birth_date = patient_resource.get("birthDate")

    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO patients (patient_id, provider, given_name, family_name, birth_date, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(patient_id) DO UPDATE SET
            given_name = excluded.given_name,
            family_name = excluded.family_name,
            birth_date = excluded.birth_date,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
    """, (
        patient_id, provider, given_name, family_name, birth_date,
        json.dumps(patient_resource),
    ))
    db.commit()
    print(f"  Patient: {given_name or '?'} {family_name or '?'} (DOB: {birth_date or 'unknown'})")


def pull_labs(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull laboratory Observations."""
    params = {
        "patient": patient_id,
        "category": "laboratory",
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching lab observations...")
    labs, warnings = get_all_pages(base_url, "Observation", token, params)
    print(f"  → {len(labs)} lab results (raw)")
    return labs, warnings


def pull_diagnostic_reports(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull DiagnosticReports (lab panels, pathology, etc.)."""
    params = {
        "patient": patient_id,
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching diagnostic reports...")
    reports, warnings = get_all_pages(base_url, "DiagnosticReport", token, params)
    print(f"  → {len(reports)} diagnostic reports")
    return reports, warnings


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


def pull_notes(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull DocumentReferences (clinical notes)."""
    params = {
        "patient": patient_id,
        "category": "clinical-note",
        "_count": "50",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching clinical notes...")
    notes, warnings = get_all_pages(base_url, "DocumentReference", token, params)
    print(f"  → {len(notes)} clinical notes")
    return notes, warnings


def extract_note_content(doc_ref: dict, base_url: str, token: str) -> tuple[str | None, str, str | None, str | None]:
    """
    Extract text content from a DocumentReference.

    Returns (content, fetch_status, fetch_detail, fetch_url) where:
      - content: the text, or None if unavailable
      - fetch_status: 'ok', 'fetch_failed', 'no_attachment', or 'empty'
      - fetch_detail: human-readable explanation of what happened
      - fetch_url: the resolved URL we attempted (for retry), or None
    """
    contents = doc_ref.get("content", [])
    if not contents:
        return None, "no_attachment", "DocumentReference has no content array", None

    for content in contents:
        attachment = content.get("attachment", {})

        # Inline data (base64 encoded)
        if "data" in attachment:
            decoded = base64.b64decode(attachment["data"])
            content_type = attachment.get("contentType", "")
            if "text" in content_type or "html" in content_type:
                text = decoded.decode("utf-8", errors="replace")
                if text.strip():
                    return text, "ok", None, None
                else:
                    return None, "empty", "Inline data decoded but was empty/whitespace", None
            text = decoded.decode("utf-8", errors="replace")
            if text.strip():
                return text, "ok", None, None
            else:
                return None, "empty", "Inline data decoded but was empty/whitespace", None

        # URL reference — fetch it
        if "url" in attachment:
            fetch_url = attachment["url"]
            # Resolve relative URLs against the FHIR base
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
                    # Try to extract OperationOutcome diagnostic from error response
                    error_body = ""
                    try:
                        outcome = resp.json()
                        issues = outcome.get("issue", [])
                        if issues:
                            error_body = "; ".join(
                                i.get("diagnostics", i.get("details", {}).get("text", ""))
                                for i in issues if i.get("diagnostics") or i.get("details")
                            )
                    except (ValueError, AttributeError):
                        body_text = resp.text.strip()
                        if body_text:
                            error_body = body_text[:200]

                    detail = f"HTTP {resp.status_code}"
                    if error_body:
                        detail += f" — {error_body}"
                    print(f"    ⚠ Note content fetch failed: {detail} ({attachment['url']})")
                    return None, "fetch_failed", detail, fetch_url
            except requests.RequestException as e:
                detail = f"Request error: {e}"
                print(f"    ⚠ Note content fetch failed: {detail} ({attachment['url']})")
                return None, "fetch_failed", detail, fetch_url

    return None, "no_attachment", "Attachments present but no data or url fields found", None


def store_labs(db, labs: list, provider: str, patient_id: str):
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
            (fhir_id, patient_id, provider, code_display, value, unit, reference_range, status, effective_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, value, unit, ref_range, status,
            effective_date, json.dumps(lab),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} lab results")


def store_diagnostic_reports(db, reports: list, provider: str, patient_id: str, base_url: str, token: str):
    """Store DiagnosticReport resources in the database, fetching presentedForm content."""
    cursor = db.cursor()
    stored = 0
    fetch_failures = 0

    for report in reports:
        if report.get("resourceType") != "DiagnosticReport":
            continue

        fhir_id = report.get("id", "")
        code = report.get("code", {}).get("text") or _get_coding_display(report.get("code", {}))
        status = report.get("status", "")
        effective_date = report.get("effectiveDateTime") or _get_period_start(report.get("effectivePeriod"))

        # Collect result observation references
        result_refs = [ref.get("reference", "") for ref in report.get("result", [])]
        result_obs_ids = json.dumps(result_refs) if result_refs else None

        # Fetch presentedForm content (similar to note attachments)
        content_text, fetch_status, fetch_detail, fetch_url = _extract_report_content(
            report, base_url, token
        )

        if fetch_status == "fetch_failed":
            fetch_failures += 1

        cursor.execute("""
            INSERT OR REPLACE INTO diagnostic_reports
            (fhir_id, patient_id, provider, code_display, status, effective_date,
             result_observation_ids, content_text,
             content_fetch_status, content_fetch_detail, content_fetch_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, status, effective_date,
            result_obs_ids, content_text,
            fetch_status, fetch_detail, fetch_url, json.dumps(report),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} diagnostic reports", end="")
    if fetch_failures:
        print(f" ({fetch_failures} with failed content fetch)")
    else:
        print()


def _extract_report_content(report: dict, base_url: str, token: str) -> tuple[str | None, str, str | None, str | None]:
    """
    Extract presentedForm content from a DiagnosticReport.

    Returns (content, fetch_status, fetch_detail, fetch_url).
    """
    presented_forms = report.get("presentedForm", [])
    if not presented_forms:
        return None, "no_attachment", None, None

    for attachment in presented_forms:
        # Inline data
        if "data" in attachment:
            decoded = base64.b64decode(attachment["data"])
            text = decoded.decode("utf-8", errors="replace")
            if text.strip():
                return text, "ok", None, None
            else:
                return None, "empty", "Inline data decoded but was empty/whitespace", None

        # URL reference
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
                    error_body = ""
                    try:
                        outcome = resp.json()
                        issues = outcome.get("issue", [])
                        if issues:
                            error_body = "; ".join(
                                i.get("diagnostics", i.get("details", {}).get("text", ""))
                                for i in issues if i.get("diagnostics") or i.get("details")
                            )
                    except (ValueError, AttributeError):
                        body_text = resp.text.strip()
                        if body_text:
                            error_body = body_text[:200]

                    detail = f"HTTP {resp.status_code}"
                    if error_body:
                        detail += f" — {error_body}"
                    print(f"    ⚠ Report content fetch failed: {detail} ({attachment['url']})")
                    return None, "fetch_failed", detail, fetch_url
            except requests.RequestException as e:
                detail = f"Request error: {e}"
                print(f"    ⚠ Report content fetch failed: {detail} ({attachment['url']})")
                return None, "fetch_failed", detail, fetch_url

    return None, "no_attachment", "presentedForm present but no data or url fields found", None


def store_notes(db, notes: list, provider: str, patient_id: str, base_url: str, token: str):
    """Store clinical notes in the database."""
    cursor = db.cursor()
    stored = 0
    fetch_failures = 0

    for note in notes:
        fhir_id = note.get("id", "")
        doc_type = _get_coding_display(note.get("type", {}))
        date = note.get("date") or note.get("context", {}).get("period", {}).get("start")
        status = note.get("status", "")
        author = _extract_author(note)

        # Extract the actual note text (with status tracking)
        content_text, fetch_status, fetch_detail, fetch_url = extract_note_content(note, base_url, token)

        if fetch_status == "fetch_failed":
            fetch_failures += 1

        cursor.execute("""
            INSERT OR REPLACE INTO notes
            (fhir_id, patient_id, provider, doc_type, author, date, status, content_text,
             content_fetch_status, content_fetch_detail, content_fetch_url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, doc_type, author, date, status,
            content_text, fetch_status, fetch_detail, fetch_url, json.dumps(note),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} clinical notes", end="")
    if fetch_failures:
        print(f" ({fetch_failures} with failed content fetch)")
    else:
        print()


def pull_conditions(base_url: str, patient_id: str, token: str) -> tuple[list, list]:
    """Pull Condition resources (diagnoses, problems)."""
    params = {
        "patient": patient_id,
        "_count": "100",
    }

    print(f"  Fetching conditions...")
    conditions, warnings = get_all_pages(base_url, "Condition", token, params)
    print(f"  → {len(conditions)} conditions")
    return conditions, warnings


def store_conditions(db, conditions: list, provider: str, patient_id: str):
    """Store conditions in the database."""
    cursor = db.cursor()
    stored = 0

    for cond in conditions:
        fhir_id = cond.get("id", "")
        code = cond.get("code", {}).get("text") or _get_coding_display(cond.get("code", {}))

        clinical_status_codings = cond.get("clinicalStatus", {}).get("coding", [])
        clinical_status = clinical_status_codings[0].get("code") if clinical_status_codings else None

        verification_codings = cond.get("verificationStatus", {}).get("coding", [])
        verification_status = verification_codings[0].get("code") if verification_codings else None

        category_list = cond.get("category", [])
        category = _get_coding_display(category_list[0]) if category_list else None

        onset_date = (
            cond.get("onsetDateTime")
            or _get_period_start(cond.get("onsetPeriod"))
        )
        abatement_date = (
            cond.get("abatementDateTime")
            or _get_period_start(cond.get("abatementPeriod"))
        )

        cursor.execute("""
            INSERT OR REPLACE INTO conditions
            (fhir_id, patient_id, provider, code_display, clinical_status, verification_status,
             category, onset_date, abatement_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, clinical_status, verification_status,
            category, onset_date, abatement_date, json.dumps(cond),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} conditions")


def pull_vitals(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull vital signs Observations."""
    params = {
        "patient": patient_id,
        "category": "vital-signs",
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching vital signs...")
    vitals, warnings = get_all_pages(base_url, "Observation", token, params)
    print(f"  → {len(vitals)} vital signs")
    return vitals, warnings


def store_vitals(db, vitals: list, provider: str, patient_id: str):
    """Store vital sign observations in the database."""
    cursor = db.cursor()
    stored = 0

    for obs in vitals:
        fhir_id = obs.get("id", "")
        code = obs.get("code", {}).get("text") or _get_coding_display(obs.get("code", {}))
        value = _extract_value(obs)
        unit = _extract_unit(obs)
        status = obs.get("status", "")
        effective_date = obs.get("effectiveDateTime") or _get_period_start(obs.get("effectivePeriod"))

        cursor.execute("""
            INSERT OR REPLACE INTO vitals
            (fhir_id, patient_id, provider, code_display, value, unit, status, effective_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, value, unit, status,
            effective_date, json.dumps(obs),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} vital signs")


def pull_allergies(base_url: str, patient_id: str, token: str) -> tuple[list, list]:
    """Pull AllergyIntolerance resources."""
    params = {
        "patient": patient_id,
        "_count": "100",
    }

    print(f"  Fetching allergies...")
    allergies, warnings = get_all_pages(base_url, "AllergyIntolerance", token, params)
    print(f"  → {len(allergies)} allergies")
    return allergies, warnings


def store_allergies(db, allergies: list, provider: str, patient_id: str):
    """Store allergy/intolerance records in the database."""
    cursor = db.cursor()
    stored = 0

    for allergy in allergies:
        fhir_id = allergy.get("id", "")
        code = allergy.get("code", {}).get("text") or _get_coding_display(allergy.get("code", {}))

        clinical_status_codings = allergy.get("clinicalStatus", {}).get("coding", [])
        clinical_status = clinical_status_codings[0].get("code") if clinical_status_codings else None

        verification_codings = allergy.get("verificationStatus", {}).get("coding", [])
        verification_status = verification_codings[0].get("code") if verification_codings else None

        allergy_type = allergy.get("type")
        category_list = allergy.get("category", [])
        category = ", ".join(category_list) if category_list else None

        criticality = allergy.get("criticality")
        onset_date = allergy.get("onsetDateTime") or _get_period_start(allergy.get("onsetPeriod"))
        recorded_date = allergy.get("recordedDate")

        # Extract reactions summary
        reactions = allergy.get("reaction", [])
        reaction_text = None
        if reactions:
            parts = []
            for r in reactions:
                manifestations = [
                    m.get("text") or _get_coding_display(m)
                    for m in r.get("manifestation", [])
                ]
                if manifestations:
                    parts.extend(manifestations)
            reaction_text = "; ".join(parts) if parts else None

        cursor.execute("""
            INSERT OR REPLACE INTO allergies
            (fhir_id, patient_id, provider, code_display, clinical_status, verification_status,
             type, category, criticality, onset_date, recorded_date, reaction_text, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, clinical_status, verification_status,
            allergy_type, category, criticality, onset_date, recorded_date,
            reaction_text, json.dumps(allergy),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} allergies")


def pull_encounters(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull Encounter resources."""
    params = {
        "patient": patient_id,
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching encounters...")
    encounters, warnings = get_all_pages(base_url, "Encounter", token, params)
    print(f"  → {len(encounters)} encounters")
    return encounters, warnings


def store_encounters(db, encounters: list, provider: str, patient_id: str):
    """Store encounter records in the database."""
    cursor = db.cursor()
    stored = 0

    for enc in encounters:
        fhir_id = enc.get("id", "")

        type_list = enc.get("type", [])
        encounter_type = _get_coding_display(type_list[0]) if type_list else None

        status = enc.get("status", "")
        enc_class = enc.get("class", {}).get("display") or enc.get("class", {}).get("code")

        period = enc.get("period", {})
        start_date = period.get("start")
        end_date = period.get("end")

        reason_list = enc.get("reasonCode", [])
        reason = _get_coding_display(reason_list[0]) if reason_list else None

        # Extract primary participant/provider
        participants = enc.get("participant", [])
        participant_name = None
        for p in participants:
            individual = p.get("individual", {})
            if individual.get("display"):
                participant_name = individual["display"]
                break

        cursor.execute("""
            INSERT OR REPLACE INTO encounters
            (fhir_id, patient_id, provider, encounter_type, status, class, start_date, end_date,
             reason, participant_name, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, encounter_type, status, enc_class,
            start_date, end_date, reason, participant_name, json.dumps(enc),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} encounters")


def pull_medications(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull MedicationRequest resources."""
    params = {
        "patient": patient_id,
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching medication requests...")
    meds, warnings = get_all_pages(base_url, "MedicationRequest", token, params)
    print(f"  → {len(meds)} medication requests")
    return meds, warnings


def store_medications(db, medications: list, provider: str, patient_id: str):
    """Store medication request records in the database."""
    cursor = db.cursor()
    stored = 0

    for med in medications:
        fhir_id = med.get("id", "")

        # Medication name from medicationCodeableConcept or medicationReference
        med_concept = med.get("medicationCodeableConcept", {})
        medication_name = med_concept.get("text") or _get_coding_display(med_concept)
        if medication_name == "Unknown" and med.get("medicationReference"):
            medication_name = med["medicationReference"].get("display", "Unknown")

        status = med.get("status", "")
        intent = med.get("intent", "")
        authored_on = med.get("authoredOn")

        # Dosage instructions
        dosage_list = med.get("dosageInstruction", [])
        dosage_text = None
        if dosage_list:
            texts = [d.get("text", "") for d in dosage_list if d.get("text")]
            dosage_text = "; ".join(texts) if texts else None

        # Requester (prescriber)
        requester = med.get("requester", {}).get("display")

        cursor.execute("""
            INSERT OR REPLACE INTO medications
            (fhir_id, patient_id, provider, medication_name, status, intent, authored_on,
             dosage_text, requester, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, medication_name, status, intent,
            authored_on, dosage_text, requester, json.dumps(med),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} medication requests")


def pull_social_history(base_url: str, patient_id: str, token: str) -> tuple[list, list]:
    """Pull social history Observations."""
    params = {
        "patient": patient_id,
        "category": "social-history",
        "_count": "100",
    }

    print(f"  Fetching social history...")
    obs, warnings = get_all_pages(base_url, "Observation", token, params)
    print(f"  → {len(obs)} social history observations")
    return obs, warnings


def store_social_history(db, observations: list, provider: str, patient_id: str):
    """Store social history observations in the database."""
    cursor = db.cursor()
    stored = 0

    for obs in observations:
        fhir_id = obs.get("id", "")
        code = obs.get("code", {}).get("text") or _get_coding_display(obs.get("code", {}))
        value = _extract_value(obs)
        status = obs.get("status", "")
        effective_date = obs.get("effectiveDateTime") or _get_period_start(obs.get("effectivePeriod"))

        cursor.execute("""
            INSERT OR REPLACE INTO social_history
            (fhir_id, patient_id, provider, code_display, value, status, effective_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, value, status,
            effective_date, json.dumps(obs),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} social history observations")


def pull_assessments(base_url: str, patient_id: str, token: str, since: str = None) -> tuple[list, list]:
    """Pull assessment/survey Observations."""
    params = {
        "patient": patient_id,
        "category": "survey",
        "_count": "100",
    }
    if since:
        params["date"] = f"ge{since}"

    print(f"  Fetching assessments...")
    obs, warnings = get_all_pages(base_url, "Observation", token, params)
    print(f"  → {len(obs)} assessments")
    return obs, warnings


def store_assessments(db, observations: list, provider: str, patient_id: str):
    """Store assessment/survey observations in the database."""
    cursor = db.cursor()
    stored = 0

    for obs in observations:
        fhir_id = obs.get("id", "")
        code = obs.get("code", {}).get("text") or _get_coding_display(obs.get("code", {}))
        value = _extract_value(obs)
        status = obs.get("status", "")
        effective_date = obs.get("effectiveDateTime") or _get_period_start(obs.get("effectivePeriod"))

        cursor.execute("""
            INSERT OR REPLACE INTO assessments
            (fhir_id, patient_id, provider, code_display, value, status, effective_date, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fhir_id, patient_id, provider, code, value, status,
            effective_date, json.dumps(obs),
        ))
        stored += 1

    db.commit()
    print(f"  → Stored {stored} assessments")


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


def pull_for_patient(provider_name: str, tokens: dict, since: str = None):
    """Pull all data for a single patient using the given tokens."""
    access_token = tokens["access_token"]
    base_url = tokens["fhir_base_url"]
    patient_id = tokens.get("patient")

    if not patient_id:
        print("No patient ID in token response.")
        return

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
        # Fetch and store patient demographics
        patient_resource = fetch_patient_demographics(base_url, patient_id, access_token)
        store_patient(db, patient_resource, provider_name, patient_id)

        # Collect warnings per resource type for raw storage
        all_warnings = {}

        # Pull labs
        labs, w = pull_labs(base_url, patient_id, access_token, since)
        all_warnings["labs"] = w
        handle_warnings(w, "Observation (labs)", provider_name, patient_id, db)

        # Pull diagnostic reports
        reports, w = pull_diagnostic_reports(base_url, patient_id, access_token, since)
        all_warnings["reports"] = w
        handle_warnings(w, "DiagnosticReport", provider_name, patient_id, db)

        # Deduplicate: separate true labs from pathology/diagnostic text observations
        labs, diagnostic_obs = deduplicate_labs_and_reports(labs, reports, base_url, access_token)

        store_labs(db, labs, provider_name, patient_id)

        # Store diagnostic reports (metadata + presentedForm content)
        store_diagnostic_reports(db, reports, provider_name, patient_id, base_url, access_token)

        # Pull notes
        notes, w = pull_notes(base_url, patient_id, access_token, since)
        all_warnings["notes"] = w
        handle_warnings(w, "DocumentReference (notes)", provider_name, patient_id, db)
        store_notes(db, notes, provider_name, patient_id, base_url, access_token)

        # Pull conditions
        conditions, w = pull_conditions(base_url, patient_id, access_token)
        all_warnings["conditions"] = w
        handle_warnings(w, "Condition", provider_name, patient_id, db)
        store_conditions(db, conditions, provider_name, patient_id)

        # Pull vitals
        vitals, w = pull_vitals(base_url, patient_id, access_token, since)
        all_warnings["vitals"] = w
        handle_warnings(w, "Observation (vitals)", provider_name, patient_id, db)
        store_vitals(db, vitals, provider_name, patient_id)

        # Pull allergies
        allergies, w = pull_allergies(base_url, patient_id, access_token)
        all_warnings["allergies"] = w
        handle_warnings(w, "AllergyIntolerance", provider_name, patient_id, db)
        store_allergies(db, allergies, provider_name, patient_id)

        # Pull encounters
        encounters, w = pull_encounters(base_url, patient_id, access_token, since)
        all_warnings["encounters"] = w
        handle_warnings(w, "Encounter", provider_name, patient_id, db)
        store_encounters(db, encounters, provider_name, patient_id)

        # Pull medications
        medications, w = pull_medications(base_url, patient_id, access_token, since)
        all_warnings["medications"] = w
        handle_warnings(w, "MedicationRequest", provider_name, patient_id, db)
        store_medications(db, medications, provider_name, patient_id)

        # Pull social history
        social_history, w = pull_social_history(base_url, patient_id, access_token)
        all_warnings["social_history"] = w
        handle_warnings(w, "Observation (social history)", provider_name, patient_id, db)
        store_social_history(db, social_history, provider_name, patient_id)

        # Pull assessments
        assessments, w = pull_assessments(base_url, patient_id, access_token, since)
        all_warnings["assessments"] = w
        handle_warnings(w, "Observation (assessments)", provider_name, patient_id, db)
        store_assessments(db, assessments, provider_name, patient_id)

        # Save raw data (entries + warnings — full OperationOutcome issues preserved)
        RAW_PULLS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(RAW_PULLS_DIR / f"{provider_name}_{patient_id[:8]}_{timestamp}.json", "w") as f:
            json.dump({
                "labs": labs,
                "notes": notes,
                "reports": reports,
                "conditions": conditions,
                "vitals": vitals,
                "allergies": allergies,
                "encounters": encounters,
                "medications": medications,
                "social_history": social_history,
                "assessments": assessments,
                "warnings": all_warnings,
            }, f, indent=2)

        print(f"\n✓ Done. Raw data saved to {RAW_PULLS_DIR}/")
        print(f"  Database: {DB_PATH}")

        # Show completeness warnings for this patient
        warnings_for_patient = db.execute(
            "SELECT resource_type FROM pull_warnings "
            "WHERE provider = ? AND patient_id = ? AND warning_code = '4119'",
            (provider_name, patient_id),
        ).fetchall()
        if warnings_for_patient:
            incomplete_types = [r[0] for r in warnings_for_patient]
            print(f"\n  ⚠ Incomplete data ({len(incomplete_types)} resource types withheld by server):")
            for rt in incomplete_types:
                print(f"    - {rt}")
            print("    → Check app registration / org activation on open.epic.com")

    except PermissionError:
        print("\nToken expired. Attempting refresh...")
        try:
            refreshed = refresh_access_token(provider_name, patient_id)
            print("Token refreshed. Please run again.")
        except Exception as e:
            print(f"Refresh failed: {e}")
            print(f"Re-authenticate: python auth.py \"{provider_name}\"")

    finally:
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
        # Pull for a specific patient
        tokens = load_tokens(provider_name, target_patient)
        if not tokens:
            print(f"No tokens for '{provider_name}' patient '{target_patient}'.")
            print(f"Run: python auth.py \"{provider_name}\"")
            sys.exit(1)
        pull_for_patient(provider_name, tokens, since)
    else:
        # Pull for all patients at this provider
        all_tokens = load_all_tokens_for_provider(provider_name)
        if not all_tokens:
            print(f"No tokens for '{provider_name}'. Run: python auth.py \"{provider_name}\"")
            sys.exit(1)

        print(f"Found {len(all_tokens)} patient(s) for {provider_name}")
        for tokens in all_tokens:
            pull_for_patient(provider_name, tokens, since)


if __name__ == "__main__":
    main()
