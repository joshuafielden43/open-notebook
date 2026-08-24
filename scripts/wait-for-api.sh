#!/bin/sh
# Wait for the API /health to be ready before starting the frontend.
# Fail closed: do not start the UI over a dead or degraded API/worker.
# POSIX-compliant for slim images (/bin/sh).

API_URL="${INTERNAL_API_URL:-http://localhost:5055}"
MAX_RETRIES=60
RETRY_INTERVAL=5
i=0

echo "Waiting for API to be ready at ${API_URL}/health..."

while [ $i -lt $MAX_RETRIES ]; do
    if curl -s -f "${API_URL}/health" > /dev/null 2>&1; then
        echo "API is ready! Starting frontend..."
        exit 0
    fi
    i=$((i + 1))
    echo "Attempt $i/$MAX_RETRIES: API not ready yet, waiting ${RETRY_INTERVAL}s..."
    sleep $RETRY_INTERVAL
done

echo "ERROR: API did not become ready within $((MAX_RETRIES * RETRY_INTERVAL)) seconds"
echo "Refusing to start frontend over an unhealthy API (north star: no silent babysitting)."
exit 1
