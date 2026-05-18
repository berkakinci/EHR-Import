# Setup Scripts

Helper scripts for getting EHR Import running from scratch.

## Quick Start

```bash
# 1. Create conda environment and install dependencies
bash setup/setup_env.sh

# 2. Activate the environment
conda activate ehr-import

# 3. Verify everything is configured correctly
python setup/verify_setup.py

# 4. Generate the HTTPS certificate for the OAuth callback
python setup/generate_cert.py

# 5. Discover your providers' FHIR endpoints
python discover_endpoints.py

# 6. Authenticate with a provider (opens browser)
python auth.py "Boston Children's Hospital"

# 7. Pull your data
python pull_data.py "Boston Children's Hospital"
```

## What Each Script Does

| Script | Purpose |
|--------|---------|
| `setup_env.sh` | Creates conda env, installs Python packages |
| `generate_cert.py` | Creates self-signed TLS cert for localhost callback |
| `verify_setup.py` | Checks that everything is configured correctly |

## Prerequisites

- [Anaconda](https://www.anaconda.com/) or [Miniconda](https://docs.conda.io/en/latest/miniconda.html)
- A MyChart account with one of the configured providers
- The app's client ID is already in `config.json` (included in the repo)

## Data Directory

All private data (tokens, database, raw API responses) is stored in a sibling
directory called `EHR Import Private/` by default. This is created automatically.
Override by setting `DATA_DIR` in a `.env` file (see `.env.example`).

## Troubleshooting

Run `python setup/verify_setup.py` to diagnose issues. Common problems:

- **"cryptography not installed"** — run `pip install -r requirements.txt`
- **"No discovered endpoints"** — run `python discover_endpoints.py`
- **"No client secret"** — create `../EHR Import Private/client_secret.txt` with your secret
- **Browser cert warning** — expected for self-signed certs; click "Advanced" → "Proceed"
