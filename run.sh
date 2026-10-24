#!/bin/sh
# Start Dose-D on http://localhost:8000  (doctor: /d, patient: /p)
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/uvicorn app.main:app --reload --reload-include '*.yaml' --host 0.0.0.0 --port "${PORT:-8000}"
