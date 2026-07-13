"""FastAPI web application for generating AAP appliance agentconfig ISOs."""
from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import shutil
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import agentconfig_gen as gen

_APP_DIR = pathlib.Path(__file__).parent
_TMP_BASE = pathlib.Path('/tmp/aap-webapp')
_TMP_TTL_SECONDS = 3600  # 1 hour

app = FastAPI(title='AAP Appliance Config Generator')
app.mount('/static', StaticFiles(directory=str(_APP_DIR / 'static')), name='static')
templates = Jinja2Templates(directory=str(_APP_DIR / 'templates'))

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

_http_basic = HTTPBasic(auto_error=False)


def _require_auth(credentials: HTTPBasicCredentials = Depends(_http_basic)) -> None:
    """Enforce HTTP Basic Auth when AUTH_USERNAME / AUTH_PASSWORD env vars are set.

    If neither env var is set the app runs without authentication (local dev).
    Both must be set to enable auth — a partial configuration raises on startup.
    """
    expected_user = os.environ.get('AUTH_USERNAME', '')
    expected_pass = os.environ.get('AUTH_PASSWORD', '')

    if not expected_user and not expected_pass:
        return  # auth disabled

    if bool(expected_user) != bool(expected_pass):
        raise RuntimeError('Set both AUTH_USERNAME and AUTH_PASSWORD, or neither.')

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail='Authentication required.',
            headers={'WWW-Authenticate': 'Basic realm="AAP Appliance Config Generator"'},
        )

    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_pass.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail='Invalid credentials.',
            headers={'WWW-Authenticate': 'Basic realm="AAP Appliance Config Generator"'},
        )


def _cleanup_old_files() -> None:
    """Remove result directories older than TTL."""
    if not _TMP_BASE.exists():
        return
    cutoff = time.time() - _TMP_TTL_SECONDS
    for entry in _TMP_BASE.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _iso_mode_available() -> bool:
    return bool(gen.find_openshift_install() and gen.find_nmstatectl())


# Locations checked in order when APPLIANCE_ISO_PATH is not set
_APPLIANCE_ISO_CANDIDATES = [
    _APP_DIR.parent / 'build' / 'appliance.iso',
    _APP_DIR.parent / 'appliance.iso',
    _APP_DIR / 'appliance.iso',
]


def _find_local_appliance_iso() -> Optional[pathlib.Path]:
    """Return path to a local appliance.iso if one can be found, else None."""
    if explicit := os.environ.get('APPLIANCE_ISO_PATH', ''):
        p = pathlib.Path(explicit)
        return p if p.is_file() else None
    for candidate in _APPLIANCE_ISO_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _appliance_iso_url() -> str:
    """Resolve the appliance ISO URL for the UI.

    Priority:
    1. APPLIANCE_ISO_URL env var (external URL — S3, CDN, etc.)
    2. Local file served by this webapp at /appliance.iso
    3. Empty string (link hidden in UI)
    """
    if url := os.environ.get('APPLIANCE_ISO_URL', ''):
        return url
    if _find_local_appliance_iso():
        return '/appliance.iso'
    return ''


@app.get('/', response_class=HTMLResponse, dependencies=[Depends(_require_auth)])
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'index.html', {
        'appliance_iso_url': _appliance_iso_url(),
        'iso_mode': _iso_mode_available(),
        'ocp_version': gen._OCP_VERSION,
    })


@app.get('/appliance.iso', dependencies=[Depends(_require_auth)])
async def download_appliance_iso() -> FileResponse:
    iso = _find_local_appliance_iso()
    if not iso:
        raise HTTPException(status_code=404, detail='Appliance ISO not found on this server.')
    return FileResponse(
        path=iso,
        media_type='application/octet-stream',
        filename='appliance.iso',
    )


@app.post('/generate', dependencies=[Depends(_require_auth)])
async def generate(
    request: Request,
    base_domain: str = Form(...),
    rendezvous_ip: str = Form(...),
    pull_secret: str = Form(''),
    gateway: str = Form(...),
    vm_mac: str = Form(...),
    machine_network: str = Form('192.168.122.0/24'),
    dns_server: str = Form('8.8.8.8'),
    cluster_name: str = Form('appliance'),
    appliance_content: str = Form('aap-full'),
    aap_namespace: str = Form('aap'),
    ao_namespace: str = Form('automation-orchestrator'),
    disconnected: str = Form('false'),
    ssh_key: str = Form(''),
):
    is_disconnected = disconnected.lower() in ('1', 'true', 'yes', 'on')

    # Pull secret is required unless disconnected mode substitutes a dummy value
    if not is_disconnected:
        if not pull_secret:
            raise HTTPException(status_code=422, detail='Pull secret is required.')
        try:
            json.loads(pull_secret)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail='Pull secret must be valid JSON.')

    # Validate appliance_content
    if appliance_content not in gen._VALID_APPLIANCE_CONTENT:
        raise HTTPException(
            status_code=422,
            detail=f'appliance_content must be one of: {", ".join(gen._VALID_APPLIANCE_CONTENT)}',
        )

    params = gen.AgentconfigParams(
        base_domain=base_domain.strip(),
        rendezvous_ip=rendezvous_ip.strip(),
        pull_secret=pull_secret.strip(),
        gateway=gateway.strip(),
        vm_mac=gen.normalize_mac(vm_mac.strip()),
        machine_network=machine_network.strip() or '192.168.122.0/24',
        dns_server=dns_server.strip() or '8.8.8.8',
        cluster_name=cluster_name.strip() or 'appliance',
        appliance_content=appliance_content,
        aap_namespace=aap_namespace.strip() or 'aap',
        ao_namespace=ao_namespace.strip() or 'automation-orchestrator',
        aap_prerelease=False,
        ao_prerelease=True,
        disconnected=is_disconnected,
        ssh_key=ssh_key.strip(),
    )

    _cleanup_old_files()

    job_id = str(uuid.uuid4())
    output_dir = _TMP_BASE / job_id
    output_dir.mkdir(parents=True)

    try:
        await gen.create_iso(params, output_dir)
    except RuntimeError as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(exc))

    base_url = str(request.base_url).rstrip('/')
    download_url = f'/download/{job_id}'
    curl_oneliner = f'curl -sLo agentconfig.noarch.iso "{base_url}{download_url}"'

    auth_dir = output_dir / 'auth'
    kubeconfig_url = f'/download/{job_id}/kubeconfig' if (auth_dir / 'kubeconfig').exists() else ''
    kubeadmin_url = f'/download/{job_id}/kubeadmin-password' if (auth_dir / 'kubeadmin-password').exists() else ''

    return {
        'download_url': download_url,
        'curl_oneliner': curl_oneliner,
        'kubeconfig_url': kubeconfig_url,
        'kubeadmin_url': kubeadmin_url,
    }


@app.get('/download/{job_id}', dependencies=[Depends(_require_auth)])
async def download(job_id: str) -> FileResponse:
    if not _UUID_RE.match(job_id):
        raise HTTPException(status_code=404, detail='Not found.')
    iso_path = _TMP_BASE / job_id / 'agentconfig.noarch.iso'
    if not iso_path.exists():
        raise HTTPException(status_code=404, detail='ISO not found or expired.')
    return FileResponse(
        path=iso_path,
        media_type='application/octet-stream',
        filename='agentconfig.noarch.iso',
    )


@app.get('/download/{job_id}/kubeconfig', dependencies=[Depends(_require_auth)])
async def download_kubeconfig(job_id: str) -> FileResponse:
    if not _UUID_RE.match(job_id):
        raise HTTPException(status_code=404, detail='Not found.')
    path = _TMP_BASE / job_id / 'auth' / 'kubeconfig'
    if not path.exists():
        raise HTTPException(status_code=404, detail='kubeconfig not found or expired.')
    return FileResponse(path=path, media_type='text/plain', filename='kubeconfig')


@app.get('/download/{job_id}/kubeadmin-password', dependencies=[Depends(_require_auth)])
async def download_kubeadmin_password(job_id: str) -> FileResponse:
    if not _UUID_RE.match(job_id):
        raise HTTPException(status_code=404, detail='Not found.')
    path = _TMP_BASE / job_id / 'auth' / 'kubeadmin-password'
    if not path.exists():
        raise HTTPException(status_code=404, detail='kubeadmin-password not found or expired.')
    return FileResponse(path=path, media_type='text/plain', filename='kubeadmin-password')


@app.get('/health')
async def health() -> dict:
    return {'status': 'ok'}
