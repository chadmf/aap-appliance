import pathlib
import subprocess

import yaml
import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / 'scripts' / 'generate-configs.py'


def run(env):
    return subprocess.run(
        ['python3', str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


# ── AAP flow ──────────────────────────────────────────────────────────────────

class TestAAPFlow:
    def test_released_creates_correct_files(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'openshift' / 'crs' / 'aap-cr.yaml').exists()
        assert not (assets_dir / 'openshift' / 'ao.yaml').exists()
        assert not (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()

    def test_released_substitutes_namespace(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'openshift' / 'aap.yaml').read_text()
        assert 'name: aap' in aap_yaml
        assert '${AAP_NAMESPACE}' not in aap_yaml

    def test_released_uses_stable_catalog(self, base_env, assets_dir, static_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        # Released AAP uses the redhat-operator-index catalog source
        aap_yaml = (assets_dir / 'openshift' / 'aap.yaml').read_text()
        released_src = (static_dir / 'openshift' / 'aap.yaml').read_text()
        assert 'redhat-operator-index' in aap_yaml
        # Content matches released template (with placeholder substituted)
        assert aap_yaml == released_src.replace('${AAP_NAMESPACE}', 'aap')

    def test_prerelease_uses_prerelease_catalog(self, base_env, assets_dir, static_dir):
        env = {**base_env, 'AAP_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'openshift' / 'aap.yaml').read_text()
        prerelease_src = (static_dir / 'openshift' / 'aap-prerelease.yaml').read_text()
        assert aap_yaml == prerelease_src.replace('${AAP_NAMESPACE}', 'aap')

    def test_prerelease_copies_idms(self, base_env, assets_dir):
        env = {**base_env, 'AAP_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()
        assert not (assets_dir / 'openshift' / 'idms-ao-prerelease.yaml').exists()

    def test_released_does_not_copy_idms(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        assert not (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()


# ── AO flow ───────────────────────────────────────────────────────────────────

class TestAOFlow:
    @pytest.fixture(autouse=True)
    def _ao_env(self, base_env):
        self.env = {**base_env, 'APPLIANCE_CONTENT': 'ao', 'AO_PRERELEASE': 'true'}

    def test_creates_correct_files(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'openshift' / 'ao.yaml').exists()
        assert (assets_dir / 'openshift' / 'crs' / 'ao-cr.yaml').exists()
        assert (assets_dir / 'openshift' / 'idms-ao-prerelease.yaml').exists()
        assert not (assets_dir / 'openshift' / 'aap.yaml').exists()

    def test_substitutes_namespace_in_cr(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        ao_cr = (assets_dir / 'openshift' / 'crs' / 'ao-cr.yaml').read_text()
        assert '${AO_NAMESPACE}' not in ao_cr
        assert 'automation-orchestrator' in ao_cr

    def test_substitutes_db_password_in_cr(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        ao_cr = (assets_dir / 'openshift' / 'crs' / 'ao-cr.yaml').read_text()
        assert '${AO_DB_PASSWORD}' not in ao_cr
        # Password must be a non-empty string (secrets.token_urlsafe(24))
        parsed = list(yaml.safe_load_all(ao_cr))
        passwords = [
            doc['stringData']['password']
            for doc in parsed
            if doc and doc.get('kind') == 'Secret' and 'stringData' in doc
        ]
        assert all(p and len(p) > 0 for p in passwords)

    def test_substitutes_namespace_in_manifest(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        ao_yaml = (assets_dir / 'openshift' / 'ao.yaml').read_text()
        assert '${AO_NAMESPACE}' not in ao_yaml
        assert 'automation-orchestrator' in ao_yaml


# ── AAP-WITH-AO combined flow ─────────────────────────────────────────────────

class TestAAPWithAOFlow:
    @pytest.fixture(autouse=True)
    def _combined_env(self, base_env):
        self.env = {**base_env, 'APPLIANCE_CONTENT': 'aap-ao', 'AO_PRERELEASE': 'true'}

    def test_creates_both_manifests(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'openshift' / 'ao.yaml').exists()
        assert (assets_dir / 'openshift' / 'crs' / 'aap-cr.yaml').exists()
        assert (assets_dir / 'openshift' / 'crs' / 'ao-cr.yaml').exists()

    def test_image_list_has_no_duplicates(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        appliance_cfg = yaml.safe_load((assets_dir / 'appliance-config.yaml').read_text())
        names = [img['name'] for img in appliance_cfg.get('additionalImages', [])]
        assert len(names) == len(set(names)), f"Duplicate image names: {[n for n in names if names.count(n) > 1]}"

    def test_image_list_includes_both_products(self, assets_dir, static_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        aap_images = yaml.safe_load((static_dir / 'config' / 'aap-images.yaml').read_text()) or []
        ao_images = yaml.safe_load((static_dir / 'config' / 'ao-images-prerelease.yaml').read_text()) or []
        expected_names = {img['name'] for img in aap_images + ao_images}

        appliance_cfg = yaml.safe_load((assets_dir / 'appliance-config.yaml').read_text())
        actual_names = {img['name'] for img in appliance_cfg.get('additionalImages', [])}

        assert expected_names.issubset(actual_names)


# ── Appliance format ──────────────────────────────────────────────────────────

class TestApplianceFormat:
    def test_live_iso_omits_disk_size(self, base_env, assets_dir):
        result = run(base_env)  # APPLIANCE_FORMAT=live-iso by default
        assert result.returncode == 0, result.stderr

        assert 'diskSizeGB' not in (assets_dir / 'appliance-config.yaml').read_text()

    def test_raw_format_includes_disk_size(self, base_env, assets_dir):
        env = {**base_env, 'APPLIANCE_FORMAT': 'raw', 'DISK_SIZE_GB': '300'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        assert 'diskSizeGB: 300' in (assets_dir / 'appliance-config.yaml').read_text()


# ── CPU architecture ──────────────────────────────────────────────────────────

class TestCPUArchitecture:
    def test_x86_64(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        assert 'cpuArchitecture: x86_64' in (assets_dir / 'appliance-config.yaml').read_text()

    def test_aarch64(self, base_env, assets_dir):
        env = {**base_env, 'CPU_ARCHITECTURE': 'aarch64'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        cfg_text = (assets_dir / 'appliance-config.yaml').read_text()
        assert 'cpuArchitecture: aarch64' in cfg_text
        assert 'x86_64' not in cfg_text


# ── Disconnected mode ─────────────────────────────────────────────────────────

class TestDisconnectedMode:
    def test_disconnected_uses_dummy_pull_secret_in_install_config(self, base_env, assets_dir):
        env = {**base_env, 'DISCONNECTED': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        install_cfg = (assets_dir / 'cluster-config' / 'install-config.yaml').read_text()
        assert '"auths":{"":' in install_cfg

    def test_connected_uses_real_pull_secret_in_install_config(self, base_env, assets_dir):
        result = run(base_env)  # DISCONNECTED=false by default
        assert result.returncode == 0, result.stderr

        install_cfg = (assets_dir / 'cluster-config' / 'install-config.yaml').read_text()
        assert '"auths":{"":' not in install_cfg
        assert 'registry.example.com' in install_cfg

    def test_appliance_config_always_uses_real_pull_secret(self, base_env, assets_dir):
        env = {**base_env, 'DISCONNECTED': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        appliance_cfg = (assets_dir / 'appliance-config.yaml').read_text()
        assert 'registry.example.com' in appliance_cfg


# ── Custom namespaces ─────────────────────────────────────────────────────────

class TestCustomNamespaces:
    def test_custom_aap_namespace(self, base_env, assets_dir):
        env = {**base_env, 'AAP_NAMESPACE': 'my-aap'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'openshift' / 'aap.yaml').read_text()
        assert 'my-aap' in aap_yaml
        assert '${AAP_NAMESPACE}' not in aap_yaml

    def test_custom_ao_namespace(self, base_env, assets_dir):
        env = {**base_env, 'APPLIANCE_CONTENT': 'ao', 'AO_NAMESPACE': 'my-ao', 'AO_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        ao_cr = (assets_dir / 'openshift' / 'crs' / 'ao-cr.yaml').read_text()
        assert 'my-ao' in ao_cr
        assert '${AO_NAMESPACE}' not in ao_cr

    def test_install_config_substitutes_base_domain(self, base_env, assets_dir):
        env = {**base_env, 'BASE_DOMAIN': 'my.cluster.io'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        install_cfg = (assets_dir / 'cluster-config' / 'install-config.yaml').read_text()
        assert 'baseDomain: my.cluster.io' in install_cfg
        assert '${BASE_DOMAIN}' not in install_cfg

    def test_agent_config_substitutes_rendezvous_ip(self, base_env, assets_dir):
        env = {**base_env, 'RENDEZVOUS_IP': '10.0.0.50'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        agent_cfg = (assets_dir / 'cluster-config' / 'agent-config.yaml').read_text()
        assert 'rendezvousIP: 10.0.0.50' in agent_cfg
        assert '${RENDEZVOUS_IP}' not in agent_cfg


# ── Error cases ───────────────────────────────────────────────────────────────

class TestErrorCases:
    def test_invalid_appliance_content(self, base_env):
        env = {**base_env, 'APPLIANCE_CONTENT': 'invalid'}
        result = run(env)
        assert result.returncode != 0
        assert 'APPLIANCE_CONTENT' in result.stderr

    def test_invalid_cpu_architecture(self, base_env):
        env = {**base_env, 'CPU_ARCHITECTURE': 'armv7'}
        result = run(env)
        assert result.returncode != 0
        assert 'CPU_ARCHITECTURE' in result.stderr
