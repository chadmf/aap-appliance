"""FastAPI endpoint tests using TestClient (synchronous, no real ISO generation)."""
import json
import pathlib
import re
import sys
import unittest.mock as mock

import pytest

# Ensure the webapp package root is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient
import app as app_module
from app import app

client = TestClient(app, raise_server_exceptions=False)

PULL_SECRET = json.dumps({'auths': {'registry.example.com': {'auth': 'dXNlcjpwYXNz'}}})

VALID_FORM = {
    'base_domain': 'example.com',
    'rendezvous_ip': '192.168.1.10',
    'pull_secret': PULL_SECRET,
    'gateway': '192.168.1.1',
    'vm_mac': '52:54:00:aa:bb:cc',
    'machine_network': '192.168.122.0/24',
    'dns_server': '8.8.8.8',
    'cluster_name': 'appliance',
    'appliance_content': 'aap-full',
    'aap_namespace': 'aap',
    'ao_namespace': 'automation-orchestrator',
    'disconnected': 'false',
    'ssh_key': '',
}


# ── /health ────────────────────────────────────────────────────────────────────

def test_health_returns_ok():
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok'}


# ── / (index) ─────────────────────────────────────────────────────────────────

def test_index_returns_html():
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']


def test_index_contains_form():
    resp = client.get('/')
    assert '<form' in resp.text
    assert 'base_domain' in resp.text
    assert 'pull_secret' in resp.text


def test_index_no_iso_link_when_url_not_set(monkeypatch):
    monkeypatch.delenv('APPLIANCE_ISO_URL', raising=False)
    monkeypatch.delenv('APPLIANCE_ISO_PATH', raising=False)
    monkeypatch.setattr(app_module, '_find_local_appliance_iso', lambda: None)
    resp = client.get('/')
    assert 'Download Appliance ISO' not in resp.text


def test_index_shows_iso_link_when_url_set(monkeypatch):
    monkeypatch.setenv('APPLIANCE_ISO_URL', 'https://example.com/appliance.iso')
    resp = client.get('/')
    assert 'Download Appliance ISO' in resp.text
    assert 'https://example.com/appliance.iso' in resp.text


def test_index_shows_local_iso_link_when_file_found(monkeypatch, tmp_path):
    monkeypatch.delenv('APPLIANCE_ISO_URL', raising=False)
    fake_iso = tmp_path / 'appliance.iso'
    fake_iso.write_bytes(b'fake')
    monkeypatch.setattr(app_module, '_find_local_appliance_iso', lambda: fake_iso)
    resp = client.get('/')
    assert 'Download Appliance ISO' in resp.text
    assert '/appliance.iso' in resp.text


def test_appliance_iso_route_serves_file(monkeypatch, tmp_path):
    fake_iso = tmp_path / 'appliance.iso'
    fake_iso.write_bytes(b'fake-appliance-content')
    monkeypatch.setattr(app_module, '_find_local_appliance_iso', lambda: fake_iso)
    resp = client.get('/appliance.iso')
    assert resp.status_code == 200
    assert resp.content == b'fake-appliance-content'


def test_appliance_iso_route_404_when_not_found(monkeypatch):
    monkeypatch.setattr(app_module, '_find_local_appliance_iso', lambda: None)
    resp = client.get('/appliance.iso')
    assert resp.status_code == 404


def test_index_shows_ocp_version():
    from agentconfig_gen import _OCP_VERSION
    resp = client.get('/')
    assert _OCP_VERSION in resp.text


# ── /generate ─────────────────────────────────────────────────────────────────

def _mock_create_iso(output_dir):
    """Async stub that creates a fake ISO file in output_dir."""
    import asyncio
    async def _impl(params, out_dir):
        iso = out_dir / 'agentconfig.noarch.iso'
        iso.write_bytes(b'fake-iso-content')
        return iso
    return _impl


@pytest.fixture
def mock_iso(monkeypatch):
    """Patch create_iso to avoid calling openshift-install in tests."""
    async def fake_create_iso(params, output_dir):
        iso = output_dir / 'agentconfig.noarch.iso'
        iso.write_bytes(b'fake-iso-content')
        auth = output_dir / 'auth'
        auth.mkdir()
        (auth / 'kubeconfig').write_text('fake-kubeconfig')
        (auth / 'kubeadmin-password').write_text('fake-password')
        return iso

    monkeypatch.setattr(app_module.gen, 'create_iso', fake_create_iso)
    monkeypatch.setattr(app_module, '_iso_mode_available', lambda: True)


def test_generate_returns_download_url(mock_iso):
    resp = client.post('/generate', data=VALID_FORM)
    assert resp.status_code == 200
    data = resp.json()
    assert 'download_url' in data
    assert data['download_url'].startswith('/download/')
    assert 'curl_oneliner' in data
    assert 'agentconfig.noarch.iso' in data['curl_oneliner']


def test_generate_download_url_contains_uuid(mock_iso):
    resp = client.post('/generate', data=VALID_FORM)
    url = resp.json()['download_url']
    # Expect /download/<uuid4>
    match = re.search(r'/download/([0-9a-f-]{36})$', url)
    assert match, f"UUID not found in {url}"


def test_generate_missing_base_domain_returns_422():
    data = {**VALID_FORM}
    del data['base_domain']
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422


def test_generate_missing_gateway_returns_422():
    data = {**VALID_FORM}
    del data['gateway']
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422


def test_generate_missing_rendezvous_ip_returns_422():
    data = {**VALID_FORM}
    del data['rendezvous_ip']
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422


def test_generate_missing_vm_mac_returns_422():
    data = {**VALID_FORM}
    del data['vm_mac']
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422


def test_generate_invalid_pull_secret_returns_422(mock_iso):
    data = {**VALID_FORM, 'pull_secret': 'not-json'}
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422
    assert 'Pull secret' in resp.json()['detail']


def test_generate_invalid_appliance_content_returns_422(mock_iso):
    data = {**VALID_FORM, 'appliance_content': 'invalid'}
    resp = client.post('/generate', data=data)
    assert resp.status_code == 422


def test_generate_propagates_iso_error(monkeypatch):
    async def failing_iso(params, output_dir):
        raise RuntimeError('openshift-install not found')

    monkeypatch.setattr(app_module.gen, 'create_iso', failing_iso)
    monkeypatch.setattr(app_module, '_iso_mode_available', lambda: True)

    resp = client.post('/generate', data=VALID_FORM)
    assert resp.status_code == 500
    assert 'openshift-install not found' in resp.json()['detail']


# ── /download ─────────────────────────────────────────────────────────────────

def test_download_streams_iso(mock_iso, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp_gen = client.post('/generate', data=VALID_FORM)
    assert resp_gen.status_code == 200

    url = resp_gen.json()['download_url']
    resp_dl = client.get(url)
    assert resp_dl.status_code == 200
    assert resp_dl.headers['content-type'] == 'application/octet-stream'
    assert resp_dl.content == b'fake-iso-content'


def test_download_nonexistent_uuid_returns_404():
    resp = client.get('/download/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 404


def test_download_invalid_uuid_returns_404():
    resp = client.get('/download/../../etc/passwd')
    assert resp.status_code == 404


def test_download_non_uuid_returns_404():
    resp = client.get('/download/not-a-uuid')
    assert resp.status_code == 404


def test_download_attachment_filename(mock_iso, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp_gen = client.post('/generate', data=VALID_FORM)
    url = resp_gen.json()['download_url']
    resp_dl = client.get(url)
    assert 'agentconfig.noarch.iso' in resp_dl.headers.get('content-disposition', '')


# ── /generate — auth URLs in response ─────────────────────────────────────────

def test_generate_returns_auth_urls(mock_iso, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp = client.post('/generate', data=VALID_FORM)
    assert resp.status_code == 200
    data = resp.json()
    assert 'kubeconfig_url' in data
    assert 'kubeadmin_url' in data
    assert data['kubeconfig_url'].endswith('/kubeconfig')
    assert data['kubeadmin_url'].endswith('/kubeadmin-password')


def test_generate_auth_urls_empty_when_no_auth_dir(monkeypatch, tmp_path):
    async def fake_create_iso_no_auth(params, output_dir):
        iso = output_dir / 'agentconfig.noarch.iso'
        iso.write_bytes(b'fake')
        return iso

    monkeypatch.setattr(app_module.gen, 'create_iso', fake_create_iso_no_auth)
    monkeypatch.setattr(app_module, '_iso_mode_available', lambda: True)
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp = client.post('/generate', data=VALID_FORM)
    data = resp.json()
    assert data['kubeconfig_url'] == ''
    assert data['kubeadmin_url'] == ''


# ── /download/{job_id}/kubeconfig and /kubeadmin-password ─────────────────────

def test_download_kubeconfig(mock_iso, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp_gen = client.post('/generate', data=VALID_FORM)
    url = resp_gen.json()['kubeconfig_url']
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.text == 'fake-kubeconfig'
    assert 'kubeconfig' in resp.headers.get('content-disposition', '')


def test_download_kubeadmin_password(mock_iso, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, '_TMP_BASE', tmp_path)
    resp_gen = client.post('/generate', data=VALID_FORM)
    url = resp_gen.json()['kubeadmin_url']
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.text == 'fake-password'
    assert 'kubeadmin-password' in resp.headers.get('content-disposition', '')


def test_download_kubeconfig_nonexistent_uuid_returns_404():
    resp = client.get('/download/00000000-0000-0000-0000-000000000000/kubeconfig')
    assert resp.status_code == 404


def test_download_kubeadmin_nonexistent_uuid_returns_404():
    resp = client.get('/download/00000000-0000-0000-0000-000000000000/kubeadmin-password')
    assert resp.status_code == 404


def test_download_kubeconfig_invalid_uuid_returns_404():
    resp = client.get('/download/../../etc/passwd/kubeconfig')
    assert resp.status_code == 404


# ── Auth ───────────────────────────────────────────────────────────────────────

class TestAuth:
    """HTTP Basic Auth — enabled when AUTH_USERNAME and AUTH_PASSWORD are set."""

    def test_disabled_by_default_index(self, monkeypatch):
        monkeypatch.delenv('AUTH_USERNAME', raising=False)
        monkeypatch.delenv('AUTH_PASSWORD', raising=False)
        assert client.get('/').status_code == 200

    def test_health_always_open(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        assert client.get('/health').status_code == 200

    def test_index_requires_credentials_when_enabled(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        resp = client.get('/')
        assert resp.status_code == 401
        assert 'WWW-Authenticate' in resp.headers

    def test_index_correct_credentials_allowed(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        resp = client.get('/', auth=('user', 'secret'))
        assert resp.status_code == 200

    def test_index_wrong_password_rejected(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        assert client.get('/', auth=('user', 'wrong')).status_code == 401

    def test_index_wrong_username_rejected(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        assert client.get('/', auth=('wrong', 'secret')).status_code == 401

    def test_generate_requires_credentials_when_enabled(self, monkeypatch, mock_iso):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        assert client.post('/generate', data=VALID_FORM).status_code == 401

    def test_generate_correct_credentials_allowed(self, monkeypatch, mock_iso):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        resp = client.post('/generate', data=VALID_FORM, auth=('user', 'secret'))
        assert resp.status_code == 200

    def test_download_requires_credentials_when_enabled(self, monkeypatch):
        monkeypatch.setenv('AUTH_USERNAME', 'user')
        monkeypatch.setenv('AUTH_PASSWORD', 'secret')
        resp = client.get('/download/00000000-0000-0000-0000-000000000000')
        assert resp.status_code == 401
