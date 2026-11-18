#!/usr/bin/env bash
# =============================================================================
# setup_controller.sh  --  NO sudo needed
# -----------------------------------------------------------------------------
# Builds a Python 3.9 virtualenv at ~/ryu-venv with a WORKING Ryu controller.
#
# Why 3.9: your WSL has Python 3.14, and Ryu is unmaintained and breaks on
# Python 3.10+. We use `uv` to fetch a standalone CPython 3.9 (no sudo), then
# build Ryu with the old setuptools it needs. This exact recipe is tested and
# working on Ubuntu 26.04 / WSL2.
#
# Run once:   bash setup_controller.sh
# Then start the controller with:   ~/ryu-venv/bin/ryu-manager ryu_controller.py
# =============================================================================
set -e
export PATH="$HOME/.local/bin:$PATH"

echo "### 1/4  install uv (standalone, no sudo) if missing"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "         uv $(uv --version)"

echo "### 2/4  fetch standalone CPython 3.9 + create venv ~/ryu-venv"
uv python install 3.9
rm -rf "$HOME/ryu-venv"
uv venv --python 3.9 --seed "$HOME/ryu-venv"

PIP="$HOME/ryu-venv/bin/pip"
PY="$HOME/ryu-venv/bin/python"

echo "### 3/4  legacy build tools (Ryu needs setuptools < 58)"
"$PIP" install -q "pip==21.3.1" "setuptools==57.5.0" "wheel==0.37.1" "pbr==5.11.1"

echo "### 4/4  build + install Ryu (no build isolation, pinned deps)"
"$PIP" install -q --no-build-isolation \
    "eventlet==0.30.2" \
    "dnspython==1.16.0" \
    "ryu==4.34"

echo
echo "### verify:"
"$PY" -c "import ryu; print('     ryu', ryu.version)"
"$PY" -c "from ryu.app.wsgi import WSGIApplication; print('     ryu WSGI REST: OK')"
"$HOME/ryu-venv/bin/ryu-manager" --version
echo
echo "DONE. Start the controller with:"
echo "    ~/ryu-venv/bin/ryu-manager ryu_controller.py"
