import json
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture(scope='session')
def static_dir(tmp_path_factory):
    """Mirror the container's /static/ layout using symlinks to the repo's real files."""
    d = tmp_path_factory.mktemp('static')
    os.symlink(REPO_ROOT / 'assets' / 'openshift', d / 'openshift')
    os.symlink(REPO_ROOT / 'config', d / 'config')
    return d


@pytest.fixture
def assets_dir(tmp_path):
    """Fresh output directory per test; the script creates subdirs inside it."""
    return tmp_path / 'assets'


@pytest.fixture
def pull_secret_file(tmp_path):
    ps = tmp_path / 'pull-secret.json'
    ps.write_text(json.dumps({'auths': {'registry.example.com': {'auth': 'dXNlcjpwYXNz'}}}))
    return ps


@pytest.fixture
def base_env(static_dir, assets_dir, pull_secret_file):
    """Baseline environment with all required vars set to safe test defaults."""
    return {
        **os.environ,
        'STATIC_DIR': str(static_dir),
        'ASSETS_DIR': str(assets_dir),
        'PULL_SECRET_FILE': str(pull_secret_file),
        'BASE_DOMAIN': 'test.example.com',
        'RENDEZVOUS_IP': '192.168.100.1',
        'CLUSTER_NAME': 'appliance',
        'MACHINE_NETWORK': '192.168.122.0/24',
        'DISK_SIZE_GB': '200',
        'APPLIANCE_FORMAT': 'live-iso',
        'APPLIANCE_CONTENT': 'aap-full',
        'AAP_NAMESPACE': 'aap',
        'AAP_PRERELEASE': 'false',
        'AO_NAMESPACE': 'automation-orchestrator',
        'AO_PRERELEASE': 'true',
        'DISCONNECTED': 'false',
        'CPU_ARCHITECTURE': 'x86_64',
    }
