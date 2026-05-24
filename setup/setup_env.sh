#!/bin/bash
# Setup script for EHR Import
# Installs Python dependencies into the currently active environment.
#
# Usage:
#   cd "EHR Import"
#   bash setup/setup_env.sh
#
# Assumes you already have a Python 3.11+ environment active
# (conda, venv, system Python — your choice).

set -e

echo "=== EHR Import Setup ==="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1)
echo "Python: $PYTHON_VERSION"

MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]); then
    echo "ERROR: Python 3.11+ required (found $PYTHON_VERSION)"
    exit 1
fi

echo ""
echo "Installing dependencies..."
echo ""
echo "Dependencies will be installed into your current Python environment:"
echo "  $(which python3)"
echo ""
echo "If you'd prefer to use a virtual environment, exit now (Ctrl+C) and set one up:"
echo "  conda create -n ehr-import python=3.12 && conda activate ehr-import"
echo "  — or —"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo ""
read -p "Press Enter to continue, or Ctrl+C to abort... "

pip install -r requirements.txt

echo ""
echo "Initializing data directory..."
python3 config.py

echo ""
echo "Generating TLS certificate..."
python3 setup/generate_cert.py

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Run: python discover.py                 (find provider FHIR URLs)"
echo "  2. Run: python auth.py \"<provider>\"         (authenticate via MyChart)"
echo "  3. Run: python pull.py \"<provider>\"         (fetch your records)"
echo ""
echo "Run 'python setup/verify_setup.py' at any time to check status."
