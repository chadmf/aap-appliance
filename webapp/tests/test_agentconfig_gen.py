import dataclasses
import json

import pytest
import yaml

from agentconfig_gen import (
    AgentconfigParams,
    _OCP_VERSION,
    _content_flags,
    _split_yaml_docs,
    generate_agent_config,
    generate_install_config,
    generate_operator_manifests,
    generate_post_install_job,
    literal_block,
    normalize_mac,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

PULL_SECRET = json.dumps({'auths': {'registry.example.com': {'auth': 'dXNlcjpwYXNz'}}})


# ── normalize_mac ──────────────────────────────────────────────────────────────

class TestNormalizeMac:
    def test_dash_to_colon(self):
        assert normalize_mac('08-00-27-AA-BB-CC') == '08:00:27:aa:bb:cc'

    def test_already_colons(self):
        assert normalize_mac('52:54:00:AA:BB:CC') == '52:54:00:aa:bb:cc'

    def test_lowercases(self):
        assert normalize_mac('52:54:00:AA:BB:CC') == '52:54:00:aa:bb:cc'


# ── literal_block ──────────────────────────────────────────────────────────────

class TestLiteralBlock:
    def test_single_line(self):
        result = literal_block('hello')
        assert result == '|\n  hello\n'

    def test_multiline(self):
        result = literal_block('line1\nline2')
        assert result == '|\n  line1\n  line2\n'

    def test_trailing_newline_stripped(self):
        result = literal_block('hello\n')
        assert result == '|\n  hello\n'


# ── generate_install_config ────────────────────────────────────────────────────

class TestInstallConfig:
    def test_base_domain_substituted(self, base_params):
        result = generate_install_config(base_params)
        assert 'baseDomain: test.example.com' in result

    def test_cluster_name_substituted(self, base_params):
        result = generate_install_config(base_params)
        assert 'name: appliance' in result

    def test_machine_network_substituted(self, base_params):
        result = generate_install_config(base_params)
        assert '192.168.100.0/24' in result

    def test_pull_secret_embedded(self, base_params):
        result = generate_install_config(base_params)
        assert 'pullSecret: |' in result
        assert 'registry.example.com' in result

    def test_ssh_key_appended_when_set(self, base_params):
        p = dataclasses.replace(base_params, ssh_key='ssh-rsa AAAAB3NzaC1...')
        result = generate_install_config(p)
        assert 'sshKey: |' in result
        assert 'ssh-rsa AAAAB3NzaC1' in result

    def test_ssh_key_omitted_when_empty(self, base_params):
        result = generate_install_config(base_params)
        assert 'sshKey' not in result

    def test_disconnected_uses_dummy_pull_secret(self, base_params):
        p = dataclasses.replace(base_params, disconnected=True)
        result = generate_install_config(p)
        assert 'registry.example.com' not in result
        assert 'dXNlcjpwYXNz' in result   # dummy auth token

    def test_valid_yaml(self, base_params):
        result = generate_install_config(base_params)
        docs = list(yaml.safe_load_all(result))
        assert len(docs) == 1
        assert docs[0]['baseDomain'] == 'test.example.com'


# ── generate_agent_config ──────────────────────────────────────────────────────

class TestAgentConfig:
    def test_rendezvous_ip_substituted(self, base_params):
        result = generate_agent_config(base_params)
        assert 'rendezvousIP: 192.168.100.10' in result

    def test_nmstate_block_always_present(self, base_params):
        result = generate_agent_config(base_params)
        assert 'networkConfig:' in result
        assert 'hosts:' in result

    def test_gateway_in_routes(self, base_params):
        result = generate_agent_config(base_params)
        assert '192.168.100.1' in result

    def test_mac_in_nmstate(self, base_params):
        result = generate_agent_config(base_params)
        assert '52:54:00:aa:bb:cc' in result

    def test_dns_server_in_nmstate(self, base_params):
        result = generate_agent_config(base_params)
        assert '8.8.8.8' in result

    def test_prefix_length_from_cidr(self, base_params):
        # /24 network → prefix-length: 24
        result = generate_agent_config(base_params)
        assert 'prefix-length: 24' in result

    def test_prefix_length_from_cidr_16(self, base_params):
        p = dataclasses.replace(base_params, machine_network='10.0.0.0/16')
        result = generate_agent_config(p)
        assert 'prefix-length: 16' in result

    def test_dhcp_disabled(self, base_params):
        result = generate_agent_config(base_params)
        assert 'dhcp: false' in result

    def test_identifier_is_mac_address(self, base_params):
        result = generate_agent_config(base_params)
        assert 'identifier: mac-address' in result


# ── generate_operator_manifests ────────────────────────────────────────────────

class TestOperatorManifests:
    def test_local_path_always_present(self, base_params):
        result = generate_operator_manifests(base_params)
        assert 'local-path-provisioner.yaml' in result

    def test_idms_additional_always_present(self, base_params):
        result = generate_operator_manifests(base_params)
        assert 'idms-additional-images.yaml' in result

    def test_idms_ao_always_present(self, base_params):
        result = generate_operator_manifests(base_params)
        assert 'idms-ao-prerelease.yaml' in result

    def test_aap_full_includes_aap_and_ao(self, base_params):
        result = generate_operator_manifests(base_params)
        assert 'aap.yaml' in result
        assert 'ao.yaml' in result

    def test_aap_only_excludes_ao(self, base_params):
        p = dataclasses.replace(base_params, appliance_content='aap')
        result = generate_operator_manifests(p)
        assert 'aap.yaml' in result
        assert 'ao.yaml' not in result

    def test_ao_only_excludes_aap(self, base_params):
        p = dataclasses.replace(base_params, appliance_content='ao')
        result = generate_operator_manifests(p)
        assert 'aap.yaml' not in result
        assert 'ao.yaml' in result

    def test_aap_with_ao_includes_both(self, base_params):
        p = dataclasses.replace(base_params, appliance_content='aap-with-ao')
        result = generate_operator_manifests(p)
        assert 'aap.yaml' in result
        assert 'ao.yaml' in result

    def test_aap_prerelease_uses_prerelease_yaml(self, base_params):
        p = dataclasses.replace(base_params, aap_prerelease=True)
        result = generate_operator_manifests(p)
        assert 'aap.yaml' in result
        assert 'idms-aap-prerelease.yaml' in result
        # aap-prerelease.yaml uses the cs-aap-* CatalogSource; aap.yaml uses redhat-operator-index
        assert 'cs-aap-2-7-next-ns' in result['aap.yaml']
        assert 'redhat-operator-index' not in result['aap.yaml']

    def test_aap_released_no_prerelease_idms(self, base_params):
        p = dataclasses.replace(base_params, aap_prerelease=False)
        result = generate_operator_manifests(p)
        assert 'idms-aap-prerelease.yaml' not in result

    def test_aap_namespace_substituted(self, base_params):
        p = dataclasses.replace(base_params, aap_namespace='custom-aap')
        result = generate_operator_manifests(p)
        assert 'custom-aap' in result['aap.yaml']
        assert '${AAP_NAMESPACE}' not in result['aap.yaml']

    def test_ao_namespace_substituted(self, base_params):
        p = dataclasses.replace(base_params, ao_namespace='custom-ao')
        result = generate_operator_manifests(p)
        assert 'custom-ao' in result['ao.yaml']
        assert '${AO_NAMESPACE}' not in result['ao.yaml']


# ── generate_post_install_job ──────────────────────────────────────────────────

class TestPostInstallJob:
    def test_empty_when_no_content(self, base_params):
        # For AO-only content: the AAP CRD wait block should not appear.
        # Note: the bare plural 'ansibleautomationplatforms' appears in the script's
        # case statement regardless; check for the full CRD name in wait blocks.
        p = dataclasses.replace(base_params, appliance_content='ao')
        result = generate_post_install_job(p)
        assert 'ansibleautomationplatforms.aap.ansible.com' not in result
        assert 'automationorchestrators.aap.ansible.com' in result

    def test_returns_non_empty_for_aap_full(self, base_params):
        result = generate_post_install_job(base_params)
        assert result != ''

    def test_valid_yaml_documents(self, base_params):
        result = generate_post_install_job(base_params)
        docs = list(yaml.safe_load_all(result))
        kinds = [d['kind'] for d in docs if d]
        assert 'Namespace' in kinds
        assert 'ServiceAccount' in kinds
        assert 'ClusterRoleBinding' in kinds
        assert 'ConfigMap' in kinds
        assert 'Job' in kinds

    def test_aap_cr_in_configmap(self, base_params):
        result = generate_post_install_job(base_params)
        assert 'aap-cr-0.yaml' in result

    def test_ao_cr_in_configmap(self, base_params):
        result = generate_post_install_job(base_params)
        assert 'ao-cr-0.yaml' in result

    def test_ao_db_password_substituted(self, base_params):
        result = generate_post_install_job(base_params)
        assert '${AO_DB_PASSWORD}' not in result

    def test_aap_crd_wait_present(self, base_params):
        result = generate_post_install_job(base_params)
        assert 'ansibleautomationplatforms.aap.ansible.com' in result

    def test_ao_crd_wait_present(self, base_params):
        result = generate_post_install_job(base_params)
        assert 'automationorchestrators.aap.ansible.com' in result
        assert 'clusters.postgresql.cnpg.io' in result

    def test_aap_namespace_in_cr(self, base_params):
        p = dataclasses.replace(base_params, aap_namespace='my-aap')
        result = generate_post_install_job(p)
        assert 'my-aap' in result
        assert '${AAP_NAMESPACE}' not in result

    def test_ao_namespace_in_cr(self, base_params):
        p = dataclasses.replace(base_params, ao_namespace='my-ao')
        result = generate_post_install_job(p)
        assert 'my-ao' in result
        assert '${AO_NAMESPACE}' not in result

    def test_job_image_present(self, base_params):
        from agentconfig_gen import _JOB_IMAGE
        result = generate_post_install_job(base_params)
        assert _JOB_IMAGE in result

    def test_aap_only_no_ao_crd_wait(self, base_params):
        p = dataclasses.replace(base_params, appliance_content='aap')
        result = generate_post_install_job(p)
        assert 'clusters.postgresql.cnpg.io' not in result


# ── _split_yaml_docs ───────────────────────────────────────────────────────────

class TestSplitYamlDocs:
    def test_single_doc(self):
        result = _split_yaml_docs('---\nkind: Foo\n')
        assert len(result) == 1

    def test_multi_doc(self):
        result = _split_yaml_docs('---\nkind: Foo\n---\nkind: Bar\n')
        assert len(result) == 2

    def test_empty_sections_skipped(self):
        result = _split_yaml_docs('---\n---\nkind: Foo\n')
        assert len(result) == 1

    def test_each_doc_starts_with_separator(self):
        result = _split_yaml_docs('---\nkind: Foo\n---\nkind: Bar\n')
        for doc in result:
            assert doc.startswith('---\n')


# ── _content_flags ─────────────────────────────────────────────────────────────

class TestContentFlags:
    @pytest.mark.parametrize('content,aap,ao', [
        ('aap',         True,  False),
        ('ao',          False, True),
        ('aap-with-ao', True,  True),
        ('aap-full',    True,  True),
    ])
    def test_flags(self, content, aap, ao):
        assert _content_flags(content) == (aap, ao)
