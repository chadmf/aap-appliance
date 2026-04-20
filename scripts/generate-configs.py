#!/usr/bin/env python3
"""Generate appliance-config.yaml, install-config.yaml, and agent-config.yaml
from the static templates in /static/config/ and environment variables.

Usage: python3 generate-configs.py
"""
import os, shutil, pathlib, yaml

namespace        = os.environ.get('NAMESPACE', 'aap')
pull_secret      = os.environ['PULL_SECRET']
base_domain      = os.environ['BASE_DOMAIN']
rendezvous_ip    = os.environ['RENDEZVOUS_IP']
ssh_key          = os.environ.get('SSH_KEY', '')
cluster_name     = os.environ.get('CLUSTER_NAME', 'appliance')
machine_network  = os.environ.get('MACHINE_NETWORK', '192.168.122.0/24')
disk_size_gb     = int(os.environ.get('DISK_SIZE_GB', '200'))
appliance_format = os.environ.get('APPLIANCE_FORMAT', 'raw')

# Copy static manifests, substituting ${NAMESPACE}
for src, dst in [
    ('/static/openshift/aap.yaml',        '/assets/openshift/aap.yaml'),
    ('/static/openshift/crs/aap-cr.yaml', '/assets/openshift/crs/aap-cr.yaml'),
]:
    content = pathlib.Path(src).read_text().replace('${NAMESPACE}', namespace)
    pathlib.Path(dst).write_text(content)

# Fully static files — copy unchanged
for f in ('local-path-provisioner.yaml', 'idms-additional-images.yaml'):
    shutil.copy(f'/static/openshift/{f}', f'/assets/openshift/{f}')


def literal_block(value):
    """Return a YAML literal block scalar string (key written separately by caller)."""
    lines = ['|\n']
    for line in value.rstrip('\n').split('\n'):
        lines.append(f'  {line}\n')
    return ''.join(lines)


# appliance-config.yaml — start from static template, inject secrets and images
cfg = pathlib.Path('/static/config/appliance-config.yaml').read_text()
if appliance_format != 'live-iso':
    cfg += f'diskSizeGB: {disk_size_gb}\n'
cfg += f'pullSecret: {literal_block(pull_secret)}'
if ssh_key:
    cfg += f'sshKey: {literal_block(ssh_key)}'

aap_images = yaml.safe_load(pathlib.Path('/static/config/aap-images.yaml').read_text())
cfg += 'additionalImages:\n'
for entry in aap_images:
    cfg += f'- name: {entry["name"]}\n'
cfg += '- name: docker.io/rancher/local-path-provisioner:v0.0.35\n'

pathlib.Path('/assets/appliance-config.yaml').write_text(cfg)

# install-config.yaml — substitute env vars then append secrets
tmpl = pathlib.Path('/static/config/install-config.yaml').read_text()
for var, val in [
    ('${BASE_DOMAIN}',     base_domain),
    ('${CLUSTER_NAME}',    cluster_name),
    ('${MACHINE_NETWORK}', machine_network),
]:
    tmpl = tmpl.replace(var, val)
tmpl += f'pullSecret: {literal_block(pull_secret)}'
if ssh_key:
    tmpl += f'sshKey: {literal_block(ssh_key)}'
pathlib.Path('/assets/cluster-config/install-config.yaml').write_text(tmpl)

# agent-config.yaml — substitute env vars
tmpl = pathlib.Path('/static/config/agent-config.yaml').read_text()
tmpl = tmpl.replace('${RENDEZVOUS_IP}', rendezvous_ip)
pathlib.Path('/assets/cluster-config/agent-config.yaml').write_text(tmpl)
