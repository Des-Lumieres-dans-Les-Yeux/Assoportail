#!/bin/sh
set -e

flask db upgrade
flask seed-admin

exec gunicorn wsgi:app \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --timeout 60 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --graceful-timeout 30 \
    --access-logfile -
