"""
Discover FHIR endpoints from MyChart URLs.

Each Epic MyChart instance publishes a SMART configuration at a well-known URL.
This script fetches those configurations to find the FHIR base URL,
authorization endpoint, and token endpoint for each provider.
"""

import json
import requests

from config import ENDPOINTS_FILE, PROVIDERS


PROVIDER_CONFIGS = {
    name: {
        "mychart_base": info["mychart_base"],
        "fhir_base": info.get("fhir_base"),
    }
    for name, info in PROVIDERS.items()
}


def discover_smart_config(mychart_base: str) -> dict | None:
    """Fetch the SMART configuration from a MyChart instance."""
    url = f"{mychart_base}/.well-known/smart-configuration"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None


def discover_fhir_metadata(mychart_base: str) -> dict | None:
    """
    Fallback: try the FHIR metadata endpoint.
    Epic often exposes FHIR at a sibling path to MyChart.
    Common patterns:
      /MyChart -> /FHIR/api/FHIR/R4/
      /MyChart -> /FHIRProxy/api/FHIR/R4/
    """
    # Try common FHIR base URL patterns relative to the MyChart host
    from urllib.parse import urlparse

    parsed = urlparse(mychart_base)
    base_host = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [
        f"{base_host}/FHIR/api/FHIR/R4",
        f"{base_host}/FHIRProxy/api/FHIR/R4",
        f"{base_host}/fhir/api/FHIR/R4",
    ]

    for candidate in candidates:
        try:
            resp = requests.get(f"{candidate}/metadata", timeout=10)
            if resp.status_code == 200:
                return {"fhir_base_url": candidate, "source": "metadata probe"}
        except requests.RequestException:
            continue

    return None


def main():
    print("=" * 60)
    print("FHIR Endpoint Discovery")
    print("=" * 60)

    results = {}

    for name, config in PROVIDER_CONFIGS.items():
        print(f"\n--- {name} ---")
        mychart_base = config["mychart_base"]
        fhir_base_override = config.get("fhir_base")

        # If a FHIR base URL is explicitly configured, use it directly
        if fhir_base_override:
            print(f"  Using configured fhir_base: {fhir_base_override}")
            smart_url = f"{fhir_base_override}/.well-known/smart-configuration"
            try:
                resp = requests.get(smart_url, timeout=10)
                resp.raise_for_status()
                smart_config = resp.json()
            except requests.RequestException as e:
                print(f"  Error fetching SMART config from fhir_base: {e}")
                smart_config = None
        else:
            # Try SMART configuration from MyChart base
            smart_config = discover_smart_config(mychart_base)

        if smart_config:
            print(f"  ✓ SMART configuration found")
            fhir_base = smart_config.get("fhir_base_url") or smart_config.get("issuer")
            auth_endpoint = smart_config.get("authorization_endpoint")
            token_endpoint = smart_config.get("token_endpoint")

            results[name] = {
                "fhir_base_url": fhir_base,
                "authorization_endpoint": auth_endpoint,
                "token_endpoint": token_endpoint,
                "scopes_supported": smart_config.get("scopes_supported", []),
            }

            print(f"  FHIR Base URL: {fhir_base}")
            print(f"  Auth Endpoint: {auth_endpoint}")
            print(f"  Token Endpoint: {token_endpoint}")
        else:
            print(f"  ✗ No SMART configuration at well-known URL")
            print(f"  Trying metadata probe...")

            metadata = discover_fhir_metadata(mychart_base)
            if metadata:
                print(f"  ✓ Found FHIR endpoint via probe: {metadata['fhir_base_url']}")
                results[name] = metadata
            else:
                print(f"  ✗ Could not discover endpoint automatically")
                print(f"  → Try looking up on https://open.epic.com/MyApps/Endpoints")
                results[name] = None

    # Save results
    with open(ENDPOINTS_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n\nResults saved to {ENDPOINTS_FILE}")
    print("Copy the relevant URLs into your .env file.")


if __name__ == "__main__":
    main()
