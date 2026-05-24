#!/usr/bin/env python3
"""Discover FHIR endpoints for configured providers."""
from ehr_import import config
from ehr_import.discover import load_brands_bundle, discover_provider

import json

print("=" * 60)
print("FHIR Endpoint Discovery")
print("=" * 60)

# Load Brands Bundle (cached)
brands = None
try:
    brands = load_brands_bundle()
    entries = brands.get("entry", [])
    n_endpoints = sum(1 for e in entries if e.get("resource", {}).get("resourceType") == "Endpoint")
    print(f"  Brands Bundle: {n_endpoints} endpoints indexed")
except Exception as e:
    print(f"  ⚠ Could not load Brands Bundle: {e}")
    print(f"  Continuing with configured fhir_base only")

results = {}

for name, prov_config in config.providers.items():
    print(f"\n--- {name} ---")
    result = discover_provider(name, prov_config, brands)
    if result:
        print(f"  ✓ {result['fhir_base_url']}")
        results[name] = result
    else:
        print(f"  ✗ Could not discover endpoint")
        results[name] = None

# Save results
with open(config.endpoints_file, "w") as f:
    json.dump(results, f, indent=2)
print(f"\n\nResults saved to {config.endpoints_file}")
