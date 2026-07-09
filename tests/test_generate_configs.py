import pathlib
import subprocess

import yaml
import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / 'scripts' / 'generate-configs.py'


def run(env, build_mode=None):
    e = dict(env)
    if build_mode is not None:
        e['BUILD_MODE'] = build_mode
    return subprocess.run(
        ['python3', str(SCRIPT)],
        env=e,
        capture_output=True,
        text=True,
    )


# ── Phase 1 (appliance) ───────────────────────────────────────────────────────

class TestAppliancePhase:
    """Phase 1: appliance-config + infra manifests only — no operator manifests."""

    @pytest.fixture(autouse=True)
    def _env(self, base_env):
        self.env = {**base_env, 'BUILD_MODE': 'appliance'}

    def test_writes_appliance_config(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'appliance-config.yaml').exists()

    def test_no_operator_manifests_in_openshift_dir(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'openshift' / 'aap.yaml').exists()
        assert not (assets_dir / 'openshift' / 'ao.yaml').exists()
        assert not (assets_dir / 'openshift' / 'crs').exists()

    def test_infra_manifests_present(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'openshift' / 'local-path-provisioner.yaml').exists()
        assert (assets_dir / 'openshift' / 'idms-additional-images.yaml').exists()

    def test_ao_idms_always_present(self, assets_dir):
        # AO only has prerelease — IDMS is always included in the appliance
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'openshift' / 'idms-ao-prerelease.yaml').exists()

    def test_aap_idms_present_when_prerelease(self, assets_dir):
        env = {**self.env, 'AAP_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()

    def test_aap_idms_absent_when_released(self, assets_dir):
        # Released AAP images come from registry.redhat.io directly — no quay.io IDMS needed
        result = run(self.env)  # AAP_PRERELEASE=false by default in base_env
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()

    def test_image_list_includes_both_products(self, assets_dir, static_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        aap_images = yaml.safe_load((static_dir / 'config' / 'aap-images.yaml').read_text()) or []
        ao_images = yaml.safe_load((static_dir / 'config' / 'ao-images-prerelease.yaml').read_text()) or []
        expected = {img['name'] for img in aap_images + ao_images}

        cfg = yaml.safe_load((assets_dir / 'appliance-config.yaml').read_text())
        actual = {img['name'] for img in cfg.get('additionalImages', [])}
        assert expected.issubset(actual)

    def test_image_list_no_duplicates(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        cfg = yaml.safe_load((assets_dir / 'appliance-config.yaml').read_text())
        names = [img['name'] for img in cfg.get('additionalImages', [])]
        assert len(names) == len(set(names))

    def test_does_not_require_base_domain(self, base_env, assets_dir):
        env = {k: v for k, v in base_env.items() if k != 'BASE_DOMAIN'}
        env['BUILD_MODE'] = 'appliance'
        result = run(env)
        assert result.returncode == 0, result.stderr

    def test_does_not_require_rendezvous_ip(self, base_env, assets_dir):
        env = {k: v for k, v in base_env.items() if k != 'RENDEZVOUS_IP'}
        env['BUILD_MODE'] = 'appliance'
        result = run(env)
        assert result.returncode == 0, result.stderr

    def test_does_not_write_cluster_configs(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'cluster-config' / 'install-config.yaml').exists()
        assert not (assets_dir / 'cluster-config' / 'agent-config.yaml').exists()


# ── Phase 2 (agentconfig) ─────────────────────────────────────────────────────

class TestAgentconfigPhase:
    """Phase 2: cluster-identity configs + operator manifests only — no appliance-config."""

    @pytest.fixture(autouse=True)
    def _env(self, base_env):
        self.env = {**base_env, 'BUILD_MODE': 'agentconfig'}

    def test_writes_install_config(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'install-config.yaml').exists()

    def test_writes_agent_config(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'agent-config.yaml').exists()

    def test_does_not_write_appliance_config(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'appliance-config.yaml').exists()

    def _job_docs(self, assets_dir):
        """Parse the post-install-crs-job.yaml into a list of YAML documents."""
        job_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').read_text()
        return [d for d in yaml.safe_load_all(job_yaml) if d]

    def _job_configmap(self, assets_dir):
        docs = self._job_docs(assets_dir)
        return next(d for d in docs if d.get('kind') == 'ConfigMap')

    def test_aap_manifests_in_cluster_config_openshift(self, assets_dir):
        env = {**self.env, 'APPLIANCE_CONTENT': 'aap'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert not (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('aap-cr-') for k in cm['data'])
        assert not any(k.startswith('ao-cr-') for k in cm['data'])

    def test_ao_manifests_in_cluster_config_openshift(self, assets_dir):
        env = {**self.env, 'APPLIANCE_CONTENT': 'ao'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        assert not (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('ao-cr-') for k in cm['data'])
        assert not any(k.startswith('aap-cr-') for k in cm['data'])

    def test_both_manifests_for_aap_with_ao(self, assets_dir):
        env = {**self.env, 'APPLIANCE_CONTENT': 'aap-with-ao'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('aap-cr-') for k in cm['data'])
        assert any(k.startswith('ao-cr-') for k in cm['data'])

    def test_both_manifests_for_aap_full(self, assets_dir):
        env = {**self.env, 'APPLIANCE_CONTENT': 'aap-full'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('aap-cr-') for k in cm['data'])
        assert any(k.startswith('ao-cr-') for k in cm['data'])


# ── AAP flow ──────────────────────────────────────────────────────────────────

class TestAAPFlow:
    def _job_configmap(self, assets_dir):
        job_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').read_text()
        docs = [d for d in yaml.safe_load_all(job_yaml) if d]
        return next(d for d in docs if d.get('kind') == 'ConfigMap')

    def test_released_creates_correct_files(self, base_env, assets_dir):
        env = {**base_env, 'APPLIANCE_CONTENT': 'aap'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert not (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        assert not (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('aap-cr-') for k in cm['data'])
        assert not any(k.startswith('ao-cr-') for k in cm['data'])

    def test_released_substitutes_namespace(self, base_env, assets_dir):
        env = {**base_env, 'APPLIANCE_CONTENT': 'aap'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').read_text()
        assert 'name: aap' in aap_yaml
        assert '${AAP_NAMESPACE}' not in aap_yaml

    def test_released_uses_stable_catalog(self, base_env, assets_dir, static_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr

        # Released AAP uses the redhat-operator-index catalog source
        aap_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').read_text()
        released_src = (static_dir / 'openshift' / 'aap.yaml').read_text()
        assert 'redhat-operator-index' in aap_yaml
        # Content matches released template (with placeholder substituted)
        assert aap_yaml == released_src.replace('${AAP_NAMESPACE}', 'aap')

    def test_prerelease_uses_prerelease_catalog(self, base_env, assets_dir, static_dir):
        env = {**base_env, 'AAP_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').read_text()
        prerelease_src = (static_dir / 'openshift' / 'aap-prerelease.yaml').read_text()
        assert aap_yaml == prerelease_src.replace('${AAP_NAMESPACE}', 'aap')

    def test_prerelease_copies_aap_idms_to_openshift_dir(self, base_env, assets_dir):
        env = {**base_env, 'AAP_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        # IDMS goes into the appliance openshift/ dir, not cluster-config/
        assert (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()

    def test_released_does_not_copy_aap_idms(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr
        assert not (assets_dir / 'openshift' / 'idms-aap-prerelease.yaml').exists()


# ── AO flow ───────────────────────────────────────────────────────────────────

class TestAOFlow:
    @pytest.fixture(autouse=True)
    def _ao_env(self, base_env):
        self.env = {**base_env, 'APPLIANCE_CONTENT': 'ao', 'AO_PRERELEASE': 'true'}

    def _job_configmap(self, assets_dir):
        job_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').read_text()
        docs = [d for d in yaml.safe_load_all(job_yaml) if d]
        return next(d for d in docs if d.get('kind') == 'ConfigMap')

    def _ao_cr_docs(self, assets_dir):
        """Return parsed AO CR YAML documents from the ConfigMap (individual keys combined)."""
        cm = self._job_configmap(assets_dir)
        docs = []
        for k in sorted(cm['data']):
            if k.startswith('ao-cr-'):
                doc = yaml.safe_load(cm['data'][k])
                if doc:
                    docs.append(doc)
        return docs

    def _ao_cr_combined(self, assets_dir):
        """Return AO CR content as a single string for substring checks."""
        cm = self._job_configmap(assets_dir)
        return ''.join(cm['data'][k] for k in sorted(cm['data']) if k.startswith('ao-cr-'))

    def test_creates_correct_files(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        assert (assets_dir / 'openshift' / 'idms-ao-prerelease.yaml').exists()
        assert not (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('ao-cr-') for k in cm['data'])
        assert not any(k.startswith('aap-cr-') for k in cm['data'])

    def test_substitutes_namespace_in_cr(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        combined = self._ao_cr_combined(assets_dir)
        assert '${AO_NAMESPACE}' not in combined
        assert 'automation-orchestrator' in combined

    def test_substitutes_db_password_in_cr(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        combined = self._ao_cr_combined(assets_dir)
        assert '${AO_DB_PASSWORD}' not in combined
        passwords = [
            doc['stringData']['password']
            for doc in self._ao_cr_docs(assets_dir)
            if doc.get('kind') == 'Secret' and 'stringData' in doc
        ]
        assert all(p and len(p) > 0 for p in passwords)

    def test_substitutes_namespace_in_manifest(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        ao_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').read_text()
        assert '${AO_NAMESPACE}' not in ao_yaml
        assert 'automation-orchestrator' in ao_yaml


# ── AAP-WITH-AO combined flow ─────────────────────────────────────────────────

class TestAAPWithAOFlow:
    @pytest.fixture(autouse=True)
    def _combined_env(self, base_env):
        self.env = {**base_env, 'APPLIANCE_CONTENT': 'aap-with-ao', 'AO_PRERELEASE': 'true'}

    def _job_configmap(self, assets_dir):
        job_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').read_text()
        docs = [d for d in yaml.safe_load_all(job_yaml) if d]
        return next(d for d in docs if d.get('kind') == 'ConfigMap')

    def test_creates_both_manifests(self, assets_dir):
        result = run(self.env)
        assert result.returncode == 0, result.stderr

        assert (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').exists()
        assert (assets_dir / 'cluster-config' / 'openshift' / 'ao.yaml').exists()
        cm = self._job_configmap(assets_dir)
        assert any(k.startswith('aap-cr-') for k in cm['data'])
        assert any(k.startswith('ao-cr-') for k in cm['data'])

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
    def _job_configmap(self, assets_dir):
        job_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'post-install-crs-job.yaml').read_text()
        docs = [d for d in yaml.safe_load_all(job_yaml) if d]
        return next(d for d in docs if d.get('kind') == 'ConfigMap')

    def test_custom_aap_namespace(self, base_env, assets_dir):
        env = {**base_env, 'AAP_NAMESPACE': 'my-aap'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        aap_yaml = (assets_dir / 'cluster-config' / 'openshift' / 'aap.yaml').read_text()
        assert 'my-aap' in aap_yaml
        assert '${AAP_NAMESPACE}' not in aap_yaml

    def test_custom_ao_namespace(self, base_env, assets_dir):
        env = {**base_env, 'APPLIANCE_CONTENT': 'ao', 'AO_NAMESPACE': 'my-ao', 'AO_PRERELEASE': 'true'}
        result = run(env)
        assert result.returncode == 0, result.stderr

        cm = self._job_configmap(assets_dir)
        combined = ''.join(cm['data'][k] for k in sorted(cm['data']) if k.startswith('ao-cr-'))
        assert 'my-ao' in combined
        assert '${AO_NAMESPACE}' not in combined

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


# ── Static network config (GATEWAY / VM_MAC / DNS_SERVER) ────────────────────

class TestNetworkConfig:
    @pytest.fixture
    def gateway_env(self, base_env):
        return {**base_env, 'GATEWAY': '192.168.56.1', 'VM_MAC': '52:54:00:aa:bb:01',
                'MACHINE_NETWORK': '192.168.56.0/24'}

    def _agent_cfg(self, assets_dir):
        return yaml.safe_load((assets_dir / 'cluster-config' / 'agent-config.yaml').read_text())

    def test_no_hosts_section_by_default(self, base_env, assets_dir):
        result = run(base_env)
        assert result.returncode == 0, result.stderr
        assert 'hosts' not in self._agent_cfg(assets_dir)

    def test_gateway_appears_in_routes(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        routes = cfg['hosts'][0]['networkConfig']['routes']['config']
        assert routes[0]['next-hop-address'] == '192.168.56.1'

    def test_mac_in_interface(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        iface = cfg['hosts'][0]['networkConfig']['interfaces'][0]
        assert iface['mac-address'] == '52:54:00:aa:bb:01'

    def test_mac_normalizes_dashes(self, base_env, assets_dir):
        env = {**base_env, 'GATEWAY': '192.168.56.1', 'VM_MAC': '08-00-27-61-6A-4A'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        iface = cfg['hosts'][0]['networkConfig']['interfaces'][0]
        assert iface['mac-address'] == '08:00:27:61:6a:4a'

    def test_vm_mac_0_takes_precedence(self, base_env, assets_dir):
        env = {**base_env, 'GATEWAY': '192.168.56.1',
               'VM_MAC_0': 'aa:bb:cc:dd:ee:ff', 'VM_MAC': '11:22:33:44:55:66'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        iface = cfg['hosts'][0]['networkConfig']['interfaces'][0]
        assert iface['mac-address'] == 'aa:bb:cc:dd:ee:ff'

    def test_vm_mac_alias(self, gateway_env, assets_dir):
        env = {k: v for k, v in gateway_env.items() if k != 'VM_MAC_0'}
        env['VM_MAC'] = '52:54:00:aa:bb:01'
        result = run(env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        assert cfg['hosts'][0]['networkConfig']['interfaces'][0]['mac-address'] == '52:54:00:aa:bb:01'

    def test_default_dns_server(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        servers = cfg['hosts'][0]['networkConfig']['dns-resolver']['config']['server']
        assert servers == ['8.8.8.8']

    def test_custom_dns_server(self, gateway_env, assets_dir):
        env = {**gateway_env, 'DNS_SERVER': '1.1.1.1'}
        result = run(env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        servers = cfg['hosts'][0]['networkConfig']['dns-resolver']['config']['server']
        assert servers == ['1.1.1.1']

    def test_static_ip_uses_rendezvous_ip(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        addrs = cfg['hosts'][0]['networkConfig']['interfaces'][0]['ipv4']['address']
        assert addrs[0]['ip'] == '192.168.100.1'  # base_env RENDEZVOUS_IP

    def test_prefix_length_from_machine_network(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        addrs = cfg['hosts'][0]['networkConfig']['interfaces'][0]['ipv4']['address']
        assert addrs[0]['prefix-length'] == 24

    def test_identifier_is_mac_address(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        iface = cfg['hosts'][0]['networkConfig']['interfaces'][0]
        assert iface['identifier'] == 'mac-address'

    def test_no_dhcp_on_interface(self, gateway_env, assets_dir):
        result = run(gateway_env)
        assert result.returncode == 0, result.stderr
        cfg = self._agent_cfg(assets_dir)
        ipv4 = cfg['hosts'][0]['networkConfig']['interfaces'][0]['ipv4']
        assert ipv4['dhcp'] is False

    def test_gateway_without_mac_errors(self, base_env):
        env = {**base_env, 'GATEWAY': '192.168.56.1'}
        result = run(env)
        assert result.returncode != 0
        assert 'VM_MAC' in result.stderr


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

    def test_invalid_build_mode(self, base_env):
        env = {**base_env, 'BUILD_MODE': 'invalid'}
        result = run(env)
        assert result.returncode != 0
        assert 'BUILD_MODE' in result.stderr
