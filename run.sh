#!/usr/bin/env bash
cd "$(dirname "$0")"
if [[ ! -x venv/bin/python3 ]]; then
    echo "Virtual environment missing - run ./setup.sh first." >&2
    exit 1
fi
exec venv/bin/python3 -m mimir "$@"
