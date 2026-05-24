#!/usr/bin/env python3
"""Probe FHIR subresources to identify access restrictions."""
import sys

from ehr_import.auth import load_tokens, load_all_tokens_for_provider
from ehr_import.tools.probe import run_probes

if len(sys.argv) < 2:
    print("Usage: python probe_subresources.py <provider_name> [--patient <patient_id>]")
    sys.exit(1)

provider_name = sys.argv[1]
target_patient = None

if "--patient" in sys.argv:
    patient_idx = sys.argv.index("--patient") + 1
    if patient_idx < len(sys.argv):
        target_patient = sys.argv[patient_idx]

if target_patient:
    tokens = load_tokens(provider_name, target_patient)
    if not tokens:
        print(f"No tokens for '{provider_name}' patient '{target_patient}'.")
        sys.exit(1)
else:
    all_tokens = load_all_tokens_for_provider(provider_name)
    if not all_tokens:
        print(f"No tokens for '{provider_name}'. Run: python auth.py \"{provider_name}\"")
        sys.exit(1)
    tokens = all_tokens[0]

run_probes(provider_name, tokens)
