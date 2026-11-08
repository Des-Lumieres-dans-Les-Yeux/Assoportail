#!/bin/sh
set -e

flask db upgrade
flask seed-admin

# Worker timeout must stay above the heaviest in-request workload: CERFA
# "nature" generation runs LibreOffice headless (60 s) before the SMTP send.
exec gunicorn wsgi:app \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --timeout 180 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --graceful-timeout 30 \
    --access-logfile -
