import json
import pytest
from agentconfig_gen import AgentconfigParams, normalize_mac


PULL_SECRET = json.dumps({'auths': {'registry.example.com': {'auth': 'dXNlcjpwYXNz'}}})


@pytest.fixture
def base_params():
    return AgentconfigParams(
        base_domain='test.example.com',
        rendezvous_ip='192.168.100.10',
        pull_secret=PULL_SECRET,
        gateway='192.168.100.1',
        vm_mac=normalize_mac('52:54:00:aa:bb:cc'),
        machine_network='192.168.100.0/24',
        dns_server='8.8.8.8',
        cluster_name='appliance',
        appliance_content='aap-full',
        aap_namespace='aap',
        ao_namespace='automation-orchestrator',
        aap_prerelease=False,
        ao_prerelease=True,
        disconnected=False,
        ssh_key='',
    )
