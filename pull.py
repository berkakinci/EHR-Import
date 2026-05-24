#!/usr/bin/env python3
"""Pull FHIR resources from a provider into the local database."""
import sys

from ehr_import.auth import load_tokens, load_all_tokens_for_provider
from ehr_import.pull import pull_for_patient

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
