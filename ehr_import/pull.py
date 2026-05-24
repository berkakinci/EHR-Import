"""
Pull orchestration — fetches all configured FHIR resources for a patient.

Coordinates FHIRClient, Database, and resource config to pull, store, and archive data.
"""

import json
import sys
from datetime import datetime

import requests

from . import config
from .auth import load_tokens, load_all_tokens_for_provider, refresh_access_token
from .client import FHIRClient
from .store import Database, should_skip_dedup
from .resources import RESOURCES
from .extract import extract_field


def pull_resource(resource_spec: dict, client: FHIRClient, db: Database,
                  provider: str, since: str = None) -> tuple[list, list]:
    """Pull a single resource type and store into generic + convenience tables.

    Returns (entries, warnings) for raw data archival.
    """
    fhir_type = resource_spec["fhir_type"]
    label = resource_spec["label"]
    table = resource_spec.get("table")
    search_params = dict(resource_spec.get("search_params", {}))
    search_params["patient"] = client.patient_id
    search_params["_count"] = "100"
    if since and "date" not in search_params:
        search_params["date"] = f"ge{since}"

    print(f"  Fetching {label}...")
    try:
        entries, warnings = client.get_all_pages(fhir_type, search_params)
    except requests.exceptions.HTTPError as e:
        print(f"  ⚠ {label}: server returned {e.response.status_code} — skipping")
        entries, warnings = [], []
    print(f"  → {len(entries)} {label}")

    db.handle_warnings(warnings, label, provider, client.patient_id)

    # --- Store into generic resources table ---
    for resource in entries:
        db.store_resource(resource, resource_spec, client.patient_id, provider)
    db.commit()

    # --- Store into convenience table (if configured) ---
    if table:
        _store_convenience_batch(resource_spec, entries, provider, client, db)

    return entries, warnings


def _store_convenience_batch(resource_spec: dict, entries: list, provider: str,
                             client: FHIRClient, db: Database):
    """Store a batch of resources into the convenience table."""
    table = resource_spec["table"]

    # Dedup setup
    seen = db.load_dedup_keys(resource_spec, client.patient_id, provider)

    stored = 0
    skipped = 0
    fetch_failures = 0

    for resource in entries:
        # Dedup check
        if should_skip_dedup(resource, resource_spec, seen):
            skipped += 1
            continue

        result = db.store_convenience(resource, resource_spec, client.patient_id, provider, client)
        if result == "fetch_failed":
            fetch_failures += 1
        stored += 1

    db.commit()

    # Print summary
    parts = [f"  → Stored {stored} into {table}"]
    if skipped:
        parts.append(f"(skipped {skipped} case-duplicates)")
    if fetch_failures:
        parts.append(f"({fetch_failures} fetch failures)")
    print(" ".join(parts))


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

    client = FHIRClient(base_url, access_token, provider_name, patient_id)
    db = Database()

    with db:
        db.init_tables()

        # Fetch patient demographics
        try:
            patient_resource = client.get(f"Patient/{patient_id}")
            db.store_patient(patient_resource, provider_name, patient_id)
        except Exception as e:
            print(f"  ⚠ Could not fetch Patient resource: {e}")

        # Pull each configured resource type
        all_raw = {}
        all_warnings = {}

        for resource_spec in RESOURCES:
            label = resource_spec["label"]
            entries, warnings = pull_resource(
                resource_spec, client, db, provider_name, since
            )
            all_raw[label] = entries
            all_warnings[label] = warnings

        # Save raw data archive
        config.raw_pulls_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = config.raw_pulls_dir / f"{provider_name}_{patient_id[:8]}_{timestamp}.json"
        with open(archive_path, "w") as f:
            json.dump({"resources": all_raw, "warnings": all_warnings}, f, indent=2)

        print(f"\n✓ Done. Raw data saved to {archive_path}")
        print(f"  Database: {config.db_path}")

        # Show completeness summary
        incomplete = db.conn.execute(
            "SELECT DISTINCT resource_type FROM data_status "
            "WHERE provider = ? AND patient_id = ? AND complete = 0",
            (provider_name, patient_id),
        ).fetchall()
        if incomplete:
            print(f"\n  ⚠ Incomplete data ({len(incomplete)} resource types withheld):")
            for (rt,) in incomplete:
                print(f"    - {rt}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python pull.py <provider_name> [--patient <patient_id>] [--since YYYY-MM-DD]")
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
