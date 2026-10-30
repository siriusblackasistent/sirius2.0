#!/bin/sh
# Install only inside this guest repository. Does not start or install services.
set -eu
repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
setup_python=${SIRIUS_SETUP_PYTHON:-python3.12}
"$setup_python" -c 'import sys; sys.exit("Sirius HDrezka requires Python 3.12+") if sys.version_info < (3, 12) else None'
"$setup_python" -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install --no-cache-dir -r "$repo_dir/requirements.txt"
mkdir -p "$repo_dir/status" "$repo_dir/data"
if [ ! -f "$repo_dir/core.py" ]; then
    cp "$repo_dir/core" "$repo_dir/core.py"
    chmod 600 "$repo_dir/core.py"
fi
echo 'Ready. Configure core.py, then run .venv/bin/python run_sirius.py.'
