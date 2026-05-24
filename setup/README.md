# Setup Scripts

Helper scripts for getting EHR Import running from scratch.

## Quick Start

```bash
# 1. (Optional) Set up a virtual environment first
python3 -m venv .venv && source .venv/bin/activate
# — or —
conda create -n ehr-import python=3.12 && conda activate ehr-import

# 2. Run setup (installs deps, generates cert, verifies config)
bash setup/setup_env.sh

# 3. (Optional) Add your providers to config.json — see below

# 4. Discover your providers' FHIR endpoints
python discover.py
```

Then follow the [main README](../README.md) for authentication and pulling data.

## Adding Your Providers

The repo ships with a few pre-configured providers. To add your own, edit the `providers` section of `config.json`:

```json
{
    "providers": {
        "My Hospital": {
            "portal_url": "https://mychart.myhospital.org/MyChart"
        },
        "My Clinic": {
            "hint": "Springfield Medical Group"
        }
    }
}
```

The key (`"My Hospital"`) is the name you'll use in `auth.py` and `pull.py` commands.

**Discovery options** (provide one):

| Field | When to use | Example |
|-------|-------------|---------|
| `portal_url` | You know the MyChart URL | `"https://mychart.childrenshospital.org/MyChart"` |
| `hint` | Search by organization name | `"Pediatric Physicians"` |
| `fhir_base` | You already know the FHIR endpoint | `"https://fhir.epic.com/.../api/FHIR/R4"` |

After adding a provider, run `python discover.py` to find its FHIR API endpoints.

## What Each Script Does

| Script | Purpose |
|--------|---------|
| `setup_env.sh` | Installs Python packages, initializes data dir, generates TLS cert |
| `generate_cert.py` | Creates self-signed TLS cert for localhost callback (also run by setup_env.sh) |
| `generate_jwk.py` | Generates RSA key pair for JWT auth (confidential client only) |
| `verify_setup.py` | Checks that everything is configured correctly |

## Prerequisites

- Python 3.11+
- A patient portal account with a provider that supports FHIR R4 (currently tested with Epic MyChart systems)
- The app's client ID is already in `config.json` (included in the repo)

## Data Directory

All private data (tokens, database, raw API responses) is stored in a sibling
directory called `EHR Import Private/` by default. This is created automatically.
Override by setting `DATA_DIR` in a `.env` file (see `.env.example`).

## Troubleshooting

Run `python setup/verify_setup.py` to diagnose issues. Common problems:

- **"cryptography not installed"** — run `pip install -r requirements.txt`
- **"No discovered endpoints"** — run `python discover.py`
- **Browser cert warning** — expected for self-signed certs; click "Advanced" → "Proceed"
- **"Token expired"** — for public clients, re-run `python auth.py "<provider>"` (no refresh tokens)
- **Want refresh tokens?** — set up a confidential client (see `docs/registration-guide.md`)
