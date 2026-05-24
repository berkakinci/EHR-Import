"""
Probe individual FHIR subresources to identify exactly which ones trigger warnings.

Unlike the main pull which does broad searches, this script queries each subresource
category individually so we can see precisely which ones succeed, which return data,
and which return OperationOutcome warnings.

Usage:
    python probe_subresources.py <provider_name> [--patient <patient_id>]

If --patient is omitted, uses the first patient found for that provider.
"""

import json
from datetime import datetime

import requests

from .. import config
from ..auth import refresh_access_token


# Each entry: (resource_path, params_override, label)
# These map to the specific subresource APIs registered on open.epic.com
PROBES = [
    # --- Observation subresources ---
    ("Observation", {"category": "laboratory"}, "Observation (Labs)"),
    ("Observation", {"category": "vital-signs"}, "Observation (Vital Signs)"),
    ("Observation", {"category": "social-history"}, "Observation (Social History)"),
    ("Observation", {"category": "survey"}, "Observation (Assessments)"),
    # SmartData Elements uses a different category code
    ("Observation", {"category": "smartdata"}, "Observation (SmartData Elements)"),

    # --- Condition subresources ---
    ("Condition", {"category": "problem-list-item"}, "Condition (Problems)"),
    ("Condition", {"category": "encounter-diagnosis"}, "Condition (Encounter Diagnosis)"),
    ("Condition", {"category": "health-concern"}, "Condition (Health Concerns)"),
    # Unfiltered — returns all categories
    ("Condition", {}, "Condition (ALL - no category filter)"),

    # --- DocumentReference subresources ---
    ("DocumentReference", {"category": "clinical-note"}, "DocumentReference (Clinical Notes)"),
    # Unfiltered
    ("DocumentReference", {}, "DocumentReference (ALL - no category filter)"),

    # --- DiagnosticReport ---
    ("DiagnosticReport", {}, "DiagnosticReport (Results)"),

    # --- Encounter ---
    ("Encounter", {}, "Encounter (Patient Chart)"),

    # --- MedicationRequest ---
    ("MedicationRequest", {}, "MedicationRequest (Signed Medication Order)"),

    # --- AllergyIntolerance ---
    ("AllergyIntolerance", {}, "AllergyIntolerance (Patient Chart)"),

    # --- CarePlan ---
    ("CarePlan", {}, "CarePlan"),

    # --- CareTeam ---
    ("CareTeam", {}, "CareTeam"),

    # --- Immunization ---
    ("Immunization", {}, "Immunization"),

    # --- Goal ---
    ("Goal", {}, "Goal"),

    # --- Procedure ---
    ("Procedure", {}, "Procedure"),

    # --- Coverage ---
    ("Coverage", {}, "Coverage"),

    # --- Device ---
    ("Device", {}, "Device"),

    # --- ServiceRequest ---
    ("ServiceRequest", {}, "ServiceRequest"),
]


def fhir_search(base_url: str, resource_path: str, token: str, params: dict) -> dict:
    """Single FHIR search request (no pagination — just first page)."""
    url = f"{base_url.rstrip('/')}/{resource_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/fhir+json",
    }
    # Always limit to 1 result — we care about warnings, not data volume
    params = {**params, "_count": "1"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code == 401:
        raise PermissionError("Token expired — refresh needed")
    if resp.status_code == 403:
        return {"_http_status": 403, "_body": resp.text[:500]}
    if resp.status_code == 400:
        return {"_http_status": 400, "_body": resp.text[:500]}

    resp.raise_for_status()
    return resp.json()


def extract_warnings(bundle: dict) -> list[dict]:
    """Pull OperationOutcome issues from a FHIR Bundle response."""
    warnings = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", entry)
        if resource.get("resourceType") == "OperationOutcome":
            for issue in resource.get("issue", []):
                warnings.append(issue)
    return warnings


def count_entries(bundle: dict) -> int:
    """Count non-OperationOutcome entries in a Bundle."""
    count = 0
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", entry)
        if resource.get("resourceType") != "OperationOutcome":
            count += 1
    return count


def classify_warnings(warnings: list) -> dict:
    """Summarize warnings by code."""
    summary = {}
    for w in warnings:
        code = None
        for coding in w.get("details", {}).get("coding", []):
            code = coding.get("code")
            break
        diag = w.get("diagnostics", "")
        text = w.get("details", {}).get("text", "")
        key = code or "unknown"
        if key not in summary:
            summary[key] = []
        summary[key].append(diag or text)
    return summary


def run_probes(provider_name: str, tokens: dict):
    """Run all probes against a provider and print results."""
    access_token = tokens["access_token"]
    base_url = tokens["fhir_base_url"]
    patient_id = tokens.get("patient")

    print(f"\n{'='*70}")
    print(f"Probing subresources: {provider_name}")
    print(f"FHIR Base: {base_url}")
    print(f"Patient: {patient_id}")
    print(f"{'='*70}\n")

    results = []

    for resource_path, extra_params, label in PROBES:
        params = {"patient": patient_id, **extra_params}
        try:
            bundle = fhir_search(base_url, resource_path, access_token, params)
        except PermissionError:
            print(f"  Token expired. Attempting refresh...")
            try:
                refreshed = refresh_access_token(provider_name, patient_id)
                access_token = refreshed["access_token"]
                bundle = fhir_search(base_url, resource_path, access_token, params)
            except Exception as e:
                print(f"  Refresh failed: {e}")
                return

        # Handle 403/400
        if isinstance(bundle, dict) and bundle.get("_http_status") in (403, 400):
            status = f"{bundle['_http_status']} ERROR"
            entry_count = 0
            warnings = []
            warning_summary = {}
        else:
            total = bundle.get("total")
            entry_count = count_entries(bundle)
            warnings = extract_warnings(bundle)
            warning_summary = classify_warnings(warnings)

            if warning_summary:
                codes = ", ".join(warning_summary.keys())
                status = f"⚠ {codes}"
            else:
                status = "✓ clean"

        # Format output
        total_str = f"total={bundle.get('total', '?')}" if isinstance(bundle, dict) and 'total' in bundle else f"entries={entry_count}"
        print(f"  {label:45s} {total_str:12s} {status}")

        # Show diagnostics for non-Outside-Record, non-4119 warnings
        for code, messages in warning_summary.items():
            if code in ("4119",):
                continue
            for msg in messages:
                if "Outside Record" in msg:
                    continue
                print(f"    → [{code}] {msg}")

        results.append({
            "label": label,
            "resource_path": resource_path,
            "params": extra_params,
            "entry_count": entry_count,
            "total": bundle.get("total") if isinstance(bundle, dict) else None,
            "warnings": warnings,
            "warning_summary": warning_summary,
        })

    # Save results
    config.raw_pulls_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = config.raw_pulls_dir / f"probe_{provider_name}_{patient_id[:8]}_{timestamp}.json"
    with open(outfile, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved: {outfile}")
