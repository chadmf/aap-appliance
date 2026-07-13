#!/bin/bash
# Container entrypoint: optionally generate a self-signed TLS certificate,
# then start the uvicorn web server.
#
# Environment variables (all optional):
#   PYTHON                     Python interpreter to use. Defaults to python3.
#                              Set to python3.9 (or whichever has uvicorn) for
#                              local dev: PYTHON=python3.9 bash entrypoint.sh
#   TLS_DISABLED=true          Run plain HTTP — use when TLS is terminated
#                              upstream (load balancer, reverse proxy).
#   TLS_CERT_FILE / TLS_KEY_FILE
#                              Paths to an existing certificate and private key.
#                              Both must be set together. When provided, no
#                              certificate is generated.
#   PORT                       Listening port. Defaults to 8443 (HTTPS) or
#                              8000 when TLS_DISABLED=true.
set -euo pipefail

PYTHON="${PYTHON:-python3}"

# ── Resolve port ─────────────────────────────────────────────────────────────

if [[ "${TLS_DISABLED:-false}" == "true" ]]; then
    PORT="${PORT:-8000}"
    echo "==> TLS disabled — starting HTTP on port ${PORT}"
    exec "$PYTHON" -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
fi

PORT="${PORT:-8443}"

# ── Resolve certificate ───────────────────────────────────────────────────────

CERT_FILE="${TLS_CERT_FILE:-}"
KEY_FILE="${TLS_KEY_FILE:-}"

if [[ -n "$CERT_FILE" && -z "$KEY_FILE" ]] || [[ -z "$CERT_FILE" && -n "$KEY_FILE" ]]; then
    echo "error: set both TLS_CERT_FILE and TLS_KEY_FILE, or neither." >&2
    exit 1
fi

if [[ -z "$CERT_FILE" ]]; then
    CERT_FILE=/tmp/tls.crt
    KEY_FILE=/tmp/tls.key

    echo "==> Generating self-signed TLS certificate..."

    # Query EC2 instance metadata (IMDSv1).  Times out quickly when not on EC2.
    PUBLIC_IP=$(curl -sf --max-time 2 \
        http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)
    PRIVATE_IP=$(curl -sf --max-time 2 \
        http://169.254.169.254/latest/meta-data/local-ipv4 2>/dev/null || true)

    # Build Subject Alternative Name — include all IPs the server may be reached at
    SAN="IP:127.0.0.1,DNS:localhost"
    [[ -n "$PRIVATE_IP" ]] && SAN="IP:${PRIVATE_IP},${SAN}"
    [[ -n "$PUBLIC_IP"  ]] && SAN="IP:${PUBLIC_IP},${SAN}"

    openssl req -x509 -newkey rsa:2048 -days 365 -nodes \
        -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -subj "/CN=aap-appliance" \
        -addext "subjectAltName=${SAN}" 2>/dev/null

    echo "    SAN  : ${SAN}"
    echo "    Cert : ${CERT_FILE}"
fi

# ── Start server ──────────────────────────────────────────────────────────────

echo "==> Starting HTTPS on port ${PORT}"
exec "$PYTHON" -m uvicorn app:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --ssl-keyfile  "$KEY_FILE" \
    --ssl-certfile "$CERT_FILE"
