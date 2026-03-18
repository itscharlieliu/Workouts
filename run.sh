#!/usr/bin/env bash
set -euo pipefail
export PYTHONUSERBASE=/home/node/.openclaw/workspace/.pythonlocal
exec python3 /home/node/.openclaw/workspace/workout-tracker/app.py
