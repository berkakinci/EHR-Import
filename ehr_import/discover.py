"""
Discover FHIR endpoints for configured providers.

Resolution order for each provider:
1. If fhir_base is configured, probe its .well-known/smart-configuration directly.
2. Otherwise, search the Epic Brands Bundle by provider name/hint/portal-derived org name.

The Brands Bundle (open.epic.com/Endpoints/Brands) is cached locally and refreshed
every 3 days. It contains ~800 FHIR endpoints searchable by organization name.
"""

import json
import os
import time

import requests

from . import config


BRANDS_FILE = config.data_dir / "epic_brands_bundle.json"
BRANDS_URL = "https://open.epic.com/Endpoints/Brands"
BRANDS_MAX_AGE = 3 * 86400  # 3 days


def load_brands_bundle() -> dict:
    """Load the Brands Bundle, downloading if missing or stale."""
    if BRANDS_FILE.exists() and (time.time() - os.path.getmtime(BRANDS_FILE)) < BRANDS_MAX_AGE:
        with open(BRANDS_FILE) as f:
            return json.load(f)
    print("  Downloading Epic Brands Bundle (~85MB, cached for 3 days)...")
    resp = requests.get(BRANDS_URL, timeout=120)
    resp.raise_for_status()
    BRANDS_FILE.write_text(resp.text)
    return resp.json()


def search_brands(bundle: dict, query: str) -> list[dict]:
    """Search the Brands Bundle for endpoints matching a query string.

    Searches brand-level Organizations (those with endpoint references) and their
    child Organizations (clinics/practices that partOf a brand). Returns matches
    with the resolved FHIR base URL.
    """
    entries = bundle.get("entry", [])
    by_url = {e.get("fullUrl"): e["resource"] for e in entries if "resource" in e}

    # Index: brand orgs (have endpoint refs) and their endpoints
    brands = []  # (org_name, fhir_base, org_resource)
    for e in entries:
        r = e.get("resource", {})
        if r.get("resourceType") == "Organization" and r.get("endpoint"):
            for ep_ref in r["endpoint"]:
                ep = by_url.get(ep_ref.get("reference"))
                if ep and ep.get("address"):
                    brands.append((r.get("name", ""), ep["address"], e.get("fullUrl")))

    # Index: child orgs → parent brand URL
    child_to_brand = {}
    for e in entries:
        r = e.get("resource", {})
        if r.get("resourceType") == "Organization" and r.get("partOf"):
            parent_ref = r["partOf"].get("reference", "")
            child_to_brand[r.get("name", "")] = parent_ref

    query_lower = query.lower()
    results = []
    seen_addresses = set()

    # Search brand names directly
    for name, address, brand_url in brands:
        if query_lower in name.lower():
            if address not in seen_addresses:
                results.append({"name": name, "fhir_base": address, "match": "brand"})
                seen_addresses.add(address)

    # Search child org names, resolve to parent brand
    if not results:
        brand_url_to_endpoint = {url: (name, addr) for name, addr, url in brands}
        for child_name, parent_ref in child_to_brand.items():
            if query_lower in child_name.lower():
                if parent_ref in brand_url_to_endpoint:
                    brand_name, address = brand_url_to_endpoint[parent_ref]
                    if address not in seen_addresses:
                        results.append({
                            "name": brand_name,
                            "fhir_base": address,
                            "match": f"child org: {child_name}",
                        })
                        seen_addresses.add(address)

    return results


def fetch_smart_config(fhir_base: str) -> dict | None:
    """Fetch .well-known/smart-configuration from a FHIR base URL."""
    url = f"{fhir_base}/.well-known/smart-configuration"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def derive_hint_from_portal(portal_url: str) -> str | None:
    """Fetch a portal page and extract og:site_name as a search hint."""
    try:
        resp = requests.get(portal_url, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return None
        import re
        match = re.search(r'og:site_name[^>]*content="([^"]+)"', resp.text, re.IGNORECASE)
        if match:
            import html
            return html.unescape(match.group(1))
    except requests.RequestException:
        pass
    return None


def discover_provider(name: str, config: dict, brands: dict | None) -> dict | None:
    """Discover FHIR endpoints for a single provider. Returns endpoint dict or None."""
    fhir_base = config.get("fhir_base")

    # 1. If fhir_base is configured, use it directly
    if fhir_base:
        print(f"  Using configured fhir_base: {fhir_base}")
        smart = fetch_smart_config(fhir_base)
        if smart:
            return _build_result(fhir_base, smart)
        print(f"  ⚠ No SMART config at configured fhir_base")

    # 2. Gather search hints from all available sources
    hints = set()
    hints.add(name)  # Always try the provider key itself

    if config.get("hint"):
        hints.add(config["hint"])

    portal_url = config.get("portal_url")
    if portal_url:
        derived = derive_hint_from_portal(portal_url)
        if derived:
            print(f"  Derived hint from portal: \"{derived}\"")
            hints.add(derived)

    # 3. Search Brands Bundle with all hints, combine results
    if brands:
        all_matches = {}  # fhir_base → match info (dedup by endpoint)
        for hint in hints:
            for match in search_brands(brands, hint):
                all_matches[match["fhir_base"]] = match

        if len(all_matches) == 1:
            match = list(all_matches.values())[0]
            fhir_base = match["fhir_base"]
            print(f"  Found: {match['name']} ({match['match']})")
            smart = fetch_smart_config(fhir_base)
            if smart:
                return _build_result(fhir_base, smart)
        elif len(all_matches) > 1:
            print(f"  Multiple endpoints found (searched: {hints}):")
            for i, match in enumerate(all_matches.values(), 1):
                print(f"    {i}. {match['name']} → {match['fhir_base']}")
            print(f"  → Add 'hint' or 'fhir_base' to config.json to disambiguate")
        else:
            print(f"  No match in Brands Bundle (searched: {hints})")

    # 3. Fallback: nothing worked
    return None


def _build_result(fhir_base: str, smart_config: dict) -> dict:
    return {
        "fhir_base_url": fhir_base,
        "authorization_endpoint": smart_config.get("authorization_endpoint"),
        "token_endpoint": smart_config.get("token_endpoint"),
    }
