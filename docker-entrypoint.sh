#!/bin/bash
set -e

echo "==> Waiting for PostgreSQL..."
# Simple wait loop — retries connecting to DB for up to 30 seconds
for i in $(seq 1 30); do
    python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/dev/null && break
    echo "    Attempt $i/30 — PostgreSQL not ready, waiting..."
    sleep 1
done

echo "==> Running migrations..."
python manage.py migrate --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo "==> Starting server..."
exec "$@"
