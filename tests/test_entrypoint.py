import os
import pathlib
import stat
import subprocess

import yaml
import pytest

ENTRYPOINT = pathlib.Path(__file__).parent.parent / 'entrypoint.sh'
REPO_ROOT = pathlib.Path(__file__).parent.parent

# OCP version from the baked-in appliance config — used to compute cache paths.
_appliance_cfg = yaml.safe_load((REPO_ROOT / 'config' / 'appliance-config.yaml').read_text())
OCP_VERSION = _appliance_cfg['ocpRelease']['version']
CPU_ARCH = 'x86_64'
CACHE_INSTALL_REL = f'cache/{OCP_VERSION}-{CPU_ARCH}/openshift-install'


def _write_script(path: pathlib.Path, body: str) -> pathlib.Path:
    path.write_text(f'#!/bin/bash\n{body}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture(scope='session')
def static_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp('static')
    os.symlink(REPO_ROOT / 'assets' / 'openshift', d / 'openshift')
    os.symlink(REPO_ROOT / 'config', d / 'config')
    # generate-configs.py must be reachable via STATIC_DIR/scripts/
    scripts_link = d / 'scripts'
    os.symlink(REPO_ROOT / 'scripts', scripts_link)
    return d


@pytest.fixture
def assets_dir(tmp_path):
    return tmp_path / 'assets'


@pytest.fixture
def pull_secret_file(tmp_path):
    import json
    ps = tmp_path / 'pull-secret.json'
    ps.write_text(json.dumps({'auths': {'registry.example.com': {'auth': 'dXNlcjpwYXNz'}}}))
    return ps


@pytest.fixture
def ssh_key_file(tmp_path):
    key = tmp_path / 'id_rsa.pub'
    key.write_text('ssh-rsa AAAAB3NzaC1yc2EAAAA test@test')
    return key


@pytest.fixture
def mock_bin_dir(tmp_path):
    """Temp directory with mock openshift-appliance and openshift-install binaries."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()

    # openshift-appliance: record that it was called, then exit 0
    called_marker = bin_dir / 'openshift-appliance.called'
    _write_script(
        bin_dir / 'openshift-appliance',
        f'touch {called_marker}',
    )

    # openshift-install: when called with 'agent create config-image --dir <dir>',
    # create the expected agentconfig ISO in that directory.
    _write_script(
        bin_dir / 'openshift-install',
        'if [[ "$*" == *"config-image"* ]]; then\n'
        '  dir="${@: -1}"\n'
        '  touch "$dir/agentconfig.noarch.iso"\n'
        'fi',
    )

    return bin_dir


@pytest.fixture
def base_entrypoint_env(static_dir, assets_dir, pull_secret_file, ssh_key_file, mock_bin_dir, tmp_path):
    return {
        **os.environ,
        'STATIC_DIR': str(static_dir),
        'ASSETS_DIR': str(assets_dir),
        'ASSETSTEMP_LINK': str(tmp_path / 'assetstemp'),
        'PULL_SECRET_FILE': str(pull_secret_file),
        'SSH_KEY_FILE': str(ssh_key_file),
        'OPENSHIFT_APPLIANCE_BIN': str(mock_bin_dir / 'openshift-appliance'),
        'OPENSHIFT_INSTALL_BIN': str(mock_bin_dir / 'openshift-install'),
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
        'CPU_ARCHITECTURE': CPU_ARCH,
    }


def run_entrypoint(env, subcommand=None):
    cmd = ['bash', str(ENTRYPOINT)]
    if subcommand:
        cmd.append(subcommand)
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


class TestBuildAppliance:
    def test_runs_appliance_build(self, base_entrypoint_env, mock_bin_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-appliance')
        assert result.returncode == 0, result.stderr
        assert (mock_bin_dir / 'openshift-appliance.called').exists()

    def test_does_not_run_openshift_install(self, base_entrypoint_env, mock_bin_dir, assets_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-appliance')
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'cluster-config' / 'agentconfig.noarch.iso').exists()

    def test_does_not_require_base_domain(self, base_entrypoint_env, mock_bin_dir):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'BASE_DOMAIN'}
        result = run_entrypoint(env, 'build-appliance')
        assert result.returncode == 0, result.stderr

    def test_does_not_require_rendezvous_ip(self, base_entrypoint_env, mock_bin_dir):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'RENDEZVOUS_IP'}
        result = run_entrypoint(env, 'build-appliance')
        assert result.returncode == 0, result.stderr

    def test_writes_appliance_config(self, base_entrypoint_env, assets_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-appliance')
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'appliance-config.yaml').exists()

    def test_no_operator_manifests_in_openshift_dir(self, base_entrypoint_env, assets_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-appliance')
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'openshift' / 'aap.yaml').exists()
        assert not (assets_dir / 'openshift' / 'ao.yaml').exists()

    def test_fails_without_pull_secret(self, base_entrypoint_env):
        env = {**base_entrypoint_env, 'PULL_SECRET_FILE': '/nonexistent/pull-secret'}
        result = run_entrypoint(env, 'build-appliance')
        assert result.returncode != 0
        assert 'pull secret' in result.stderr


class TestBuildAgentconfig:
    def test_runs_openshift_install(self, base_entrypoint_env, assets_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-agentconfig')
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'agentconfig.noarch.iso').exists()

    def test_does_not_run_appliance_build(self, base_entrypoint_env, mock_bin_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-agentconfig')
        assert result.returncode == 0, result.stderr
        assert not (mock_bin_dir / 'openshift-appliance.called').exists()

    def test_requires_base_domain(self, base_entrypoint_env):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'BASE_DOMAIN'}
        result = run_entrypoint(env, 'build-agentconfig')
        assert result.returncode != 0
        assert 'BASE_DOMAIN' in result.stderr

    def test_requires_rendezvous_ip(self, base_entrypoint_env):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'RENDEZVOUS_IP'}
        result = run_entrypoint(env, 'build-agentconfig')
        assert result.returncode != 0
        assert 'RENDEZVOUS_IP' in result.stderr

    def test_operator_manifests_in_cluster_config(self, base_entrypoint_env, assets_dir):
        result = run_entrypoint(base_entrypoint_env, 'build-agentconfig')
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').exists()

    def test_fails_if_no_binary(self, base_entrypoint_env):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'OPENSHIFT_INSTALL_BIN'}
        result = run_entrypoint(env, 'build-agentconfig')
        assert result.returncode != 0
        assert 'openshift-install not found' in result.stderr

    def test_explicit_bin_override_is_used(self, base_entrypoint_env, tmp_path, assets_dir):
        alt_bin = tmp_path / 'alt-openshift-install'
        marker = tmp_path / 'alt.called'
        _write_script(
            alt_bin,
            f'touch {marker}\n'
            'if [[ "$*" == *"config-image"* ]]; then\n'
            '  dir="${@: -1}"\n'
            '  touch "$dir/agentconfig.noarch.iso"\n'
            'fi',
        )
        env = {**base_entrypoint_env, 'OPENSHIFT_INSTALL_BIN': str(alt_bin)}
        result = run_entrypoint(env, 'build-agentconfig')
        assert result.returncode == 0, result.stderr
        assert marker.exists(), 'expected OPENSHIFT_INSTALL_BIN override to be invoked'

    def test_cache_path_used_when_no_override(self, base_entrypoint_env, assets_dir, mock_bin_dir):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'OPENSHIFT_INSTALL_BIN'}

        cache_binary = assets_dir / CACHE_INSTALL_REL
        cache_binary.parent.mkdir(parents=True, exist_ok=True)
        marker = assets_dir / 'cache-install.called'
        _write_script(
            cache_binary,
            f'touch {marker}\n'
            'if [[ "$*" == *"config-image"* ]]; then\n'
            '  dir="${@: -1}"\n'
            '  touch "$dir/agentconfig.noarch.iso"\n'
            'fi',
        )

        result = run_entrypoint(env, 'build-agentconfig')
        assert result.returncode == 0, result.stderr
        assert marker.exists(), 'expected cache-path binary to be invoked'


class TestOneShotMode:
    def test_runs_both_phases(self, base_entrypoint_env, mock_bin_dir, assets_dir):
        result = run_entrypoint(base_entrypoint_env)
        assert result.returncode == 0, result.stderr
        assert (mock_bin_dir / 'openshift-appliance.called').exists()
        assert (assets_dir / 'cluster-config' / 'agentconfig.noarch.iso').exists()

    def test_requires_base_domain(self, base_entrypoint_env):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'BASE_DOMAIN'}
        result = run_entrypoint(env)
        assert result.returncode != 0

    def test_requires_rendezvous_ip(self, base_entrypoint_env):
        env = {k: v for k, v in base_entrypoint_env.items() if k != 'RENDEZVOUS_IP'}
        result = run_entrypoint(env)
        assert result.returncode != 0



class TestUnknownSubcommand:
    def test_unknown_subcommand_fails(self, base_entrypoint_env):
        result = run_entrypoint(base_entrypoint_env, 'invalid-subcommand')
        assert result.returncode != 0
        assert 'unknown subcommand' in result.stderr
