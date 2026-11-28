#!/bin/bash
# Starts Nyenz Agent. Checks its packages first and repairs them quietly if needed.
cd "$(dirname "$0")" || exit 1
VPY=".venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "  Nyenz Agent is not installed yet. Starting the installer..."
  exec ./install.command
fi
if ! "$VPY" bootstrap.py check; then
  echo
  echo "  Nyenz Agent stopped because of a problem. Run install.command once to repair it."
  echo "  Your settings and results are kept. See docs/TROUBLESHOOTING.md and attach fanout.log (see the README for where it is) to a bug report."
  read -r -p "  Press Enter to close. " _
  exit 1
fi
exec "$VPY" app.py
