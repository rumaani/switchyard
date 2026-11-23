#!/bin/sh
# Container entrypoint. SWITCHYARD_ROLE=mock runs the demo vendor servers instead of the app.
set -e
if [ "${SWITCHYARD_ROLE:-app}" = "mock" ]; then
  cd /app/mock
  exec uvicorn mock_servers:app --host 0.0.0.0 --port "${PORT:-9100}"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips "*"
