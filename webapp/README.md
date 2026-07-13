# AAP Appliance Config Generator — Web Application

A PatternFly-styled web UI that generates a site-specific **agentconfig ISO**
(`agentconfig.noarch.iso`) for an AAP appliance node. Users fill in cluster and
network details; the server runs `openshift-install agent create config-image`
and the browser downloads the resulting ISO.

Boot your node with **two CD-ROMs** attached:

1. **`appliance.iso`** — the Phase 1 appliance (large, built once, distributable)
2. **`agentconfig.noarch.iso`** — the site config (small, generated here per-site)

---

## Running locally (development)

Install dependencies once:

```bash
cd webapp
pip install -r requirements.txt
```

### Local dev — plain HTTP, no auth (simplest)

```bash
uvicorn app:app --host 127.0.0.1 --port 8080 --reload
```

Open <http://127.0.0.1:8080/>.  Auth is disabled when neither `AUTH_USERNAME`
nor `AUTH_PASSWORD` is set.

### Local dev — plain HTTP, with auth

```bash
AUTH_USERNAME=demo AUTH_PASSWORD=changeme \
uvicorn app:app --host 127.0.0.1 --port 8080 --reload
```

### Local dev — with TLS (self-signed), no auth

Uses `entrypoint.sh` directly. The EC2 metadata query times out silently and
the cert is scoped to `127.0.0.1`/`localhost`.

```bash
cd webapp
PYTHON=python3.9 PORT=8443 bash entrypoint.sh
```

Open <https://127.0.0.1:8443/> and click through the self-signed cert warning.

### Local dev — with TLS, with auth

```bash
cd webapp
PYTHON=python3.9 PORT=8443 \
AUTH_USERNAME=demo AUTH_PASSWORD=changeme \
bash entrypoint.sh
```

### Enabling ISO generation locally

`openshift-install` v4.22.0 and `nmstatectl` must be reachable. Without them
the submit button is disabled and the UI shows a warning.

```bash
# If the binaries are in PATH (e.g. downloaded to /usr/local/sbin):
uvicorn app:app --host 127.0.0.1 --port 8080 --reload

# Or point to them explicitly:
OPENSHIFT_INSTALL_BIN=/usr/local/sbin/openshift-install \
NMSTATECTL_BIN=$(which nmstatectl) \
uvicorn app:app --host 127.0.0.1 --port 8080 --reload
```

### Appliance ISO link in local dev

The webapp auto-detects `../build/appliance.iso` relative to the `webapp/`
directory. If the Phase 1 build output is elsewhere:

```bash
APPLIANCE_ISO_PATH=/path/to/build/appliance.iso \
uvicorn app:app --host 127.0.0.1 --port 8080 --reload
```

### Running tests

```bash
cd webapp
pytest tests/ -v
```

Tests mock `create_iso` — no real `openshift-install` is needed.

---

## Building the container image

```bash
# From the repo root
sudo podman build -t aap-webapp:latest -f webapp/Containerfile webapp/

# Or with custom OCP version (must match appliance-config.yaml)
sudo podman build \
  --build-arg OCP_VERSION=4.22.0 \
  --build-arg CPU_ARCH=x86_64 \
  -t aap-webapp:latest \
  -f webapp/Containerfile webapp/
```

The image is self-contained: `openshift-install` and `nmstatectl` are baked in
at build time.

---

## Running the container

The container starts HTTPS by default (port 8443). A self-signed certificate is
generated at startup; on EC2 the instance's public and private IPs are included
in the certificate's Subject Alternative Name automatically.

### Configuration matrix

| Goal | Key variables |
|---|---|
| Minimal smoke test | _(none)_ |
| Add auth | `AUTH_USERNAME` + `AUTH_PASSWORD` |
| Serve appliance ISO locally | `APPLIANCE_ISO_PATH=/mounted/path/appliance.iso` |
| Link to appliance ISO on S3 | `APPLIANCE_ISO_URL=https://…/appliance.iso` |
| Use a real TLS certificate | `TLS_CERT_FILE` + `TLS_KEY_FILE` |
| Disable TLS (behind a proxy) | `TLS_DISABLED=true` |
| Custom port | `PORT=9443` |
| Use Phase 1 binary cache | `-v /build/assets:/assets:Z` |

### Examples

**Minimal** — verify the UI is reachable, no auth, self-signed TLS:

```bash
sudo podman run --rm -p 8443:8443 aap-webapp:latest
```

**With auth** — credentials required on every route except `/health`:

```bash
sudo podman run --rm -p 8443:8443 \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

**Appliance ISO served locally** — mounts `appliance.iso` so the webapp
streams it at `/appliance.iso`:

```bash
sudo podman run --rm -p 8443:8443 \
  -v /path/to/build:/build:ro,Z \
  -e APPLIANCE_ISO_PATH=/build/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

**Appliance ISO on S3** — shows a direct download link, no local file needed:

```bash
sudo podman run --rm --net=host -p 8443:8443 \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

**Real TLS certificate** (e.g. Let's Encrypt):

```bash
sudo podman run --rm --net=host \
  -v /etc/letsencrypt/live/yourdomain.com:/certs:ro,Z \
  -e TLS_CERT_FILE=/certs/fullchain.pem \
  -e TLS_KEY_FILE=/certs/privkey.pem \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

**Behind a TLS-terminating proxy** — plain HTTP, port 8000:

```bash
sudo podman run --rm -p 8000:8000 \
  -e TLS_DISABLED=true \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

**Phase 1 binary cache** — use the `openshift-install` cached during a prior
Phase 1 build instead of the one baked into the image:

```bash
sudo podman run --rm -p 8443:8443 \
  -v /path/to/build/assets:/assets:Z \
  -e APPLIANCE_ISO_URL=https://example.com/appliance.iso \
  aap-webapp:latest
```

The webapp auto-detects the binary at `/assets/cache/4.22.0-x86_64/openshift-install`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `APPLIANCE_ISO_PATH` | auto-detect | Path to a local `appliance.iso` to serve directly from this webapp at `/appliance.iso`. When unset, the app checks `../build/appliance.iso`, `../appliance.iso`, and `./appliance.iso` in that order. |
| `APPLIANCE_ISO_URL` | `""` | External URL for the appliance ISO (S3, CDN, etc.). Takes precedence over `APPLIANCE_ISO_PATH` / auto-detection. |
| `OPENSHIFT_INSTALL_BIN` | auto-detect | Path to the `openshift-install` binary. Falls back to `/assets/cache/4.22.0-x86_64/openshift-install` then `$PATH`. |
| `NMSTATECTL_BIN` | auto-detect | Path to the `nmstatectl` binary. Falls back to `$PATH`. |
| `AUTH_USERNAME` | `""` | HTTP Basic Auth username. Must be set together with `AUTH_PASSWORD` to enable authentication. If both are unset, the app runs without auth (suitable for local development). |
| `AUTH_PASSWORD` | `""` | HTTP Basic Auth password. Must be set together with `AUTH_USERNAME`. |
| `TLS_DISABLED` | `""` | Set to `true` to run plain HTTP. Use when TLS is terminated upstream. |
| `TLS_CERT_FILE` | `""` | Path to a PEM certificate file. Must be paired with `TLS_KEY_FILE`. When unset a self-signed certificate is generated at startup. |
| `TLS_KEY_FILE` | `""` | Path to the PEM private key matching `TLS_CERT_FILE`. |
| `PORT` | `8443` / `8000` | Listening port. Defaults to `8443` with TLS, `8000` when `TLS_DISABLED=true`. |

Authentication protects all routes except `GET /health` (kept open for load balancer health checks). Setting only one of the two auth variables is an error — the app returns 500 on all protected routes until both are set or both are removed.

---

## EC2 deployment

The container starts with HTTPS enabled by default. On first boot `entrypoint.sh`
generates a self-signed certificate, querying the EC2 instance metadata service to
include the instance's public and private IPs in the Subject Alternative Name so
browsers recognise the cert for direct-IP access.

```bash
sudo podman run -d \
  --name aap-webapp \
  --restart=always \
  --net=host \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

Point a browser at `https://<EC2-public-IP>:8443`. You will see a browser
security warning on the first visit because the certificate is self-signed — click
through once and the connection is encrypted from that point on.

### Using a real certificate

If you have a certificate (e.g. from Let's Encrypt via certbot):

```bash
sudo podman run -d \
  --name aap-webapp \
  --net=host \
  -v /etc/letsencrypt/live/yourdomain.com:/certs:ro,Z \
  -e TLS_CERT_FILE=/certs/fullchain.pem \
  -e TLS_KEY_FILE=/certs/privkey.pem \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

### Running behind a TLS-terminating proxy

If a load balancer or nginx already handles TLS, disable the built-in TLS to
avoid double-encryption:

```bash
sudo podman run -d \
  --name aap-webapp \
  -p 8000:8000 \
  -e TLS_DISABLED=true \
  -e APPLIANCE_ISO_URL=https://s3.amazonaws.com/mybucket/appliance.iso \
  -e AUTH_USERNAME=demo \
  -e AUTH_PASSWORD=changeme \
  aap-webapp:latest
```

---

## Keeping manifests in sync

The `webapp/manifests/openshift/` directory is a standalone copy of
`assets/openshift/`.  When image digests are updated via
`scripts/update-aap-images.sh` or `scripts/update-ao-images.sh`, sync the
copies with:

```bash
cp -r assets/openshift/* webapp/manifests/openshift/
```

A CI check that runs `diff -rq assets/openshift webapp/manifests/openshift`
can enforce this automatically.
