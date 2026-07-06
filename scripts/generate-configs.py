#!/usr/bin/env python3
"""Generate appliance-config.yaml, install-config.yaml, and agent-config.yaml
from the static templates in /static/config/ and environment variables.

Usage: python3 generate-configs.py
"""
import os, secrets as _secrets, shutil, pathlib, yaml

# Root directory for openshift-appliance assets (appliance-config.yaml, openshift/, cluster-config/, ...).
# Default /assets matches runtime entrypoint.sh; ISO CI builds set ASSETS_DIR=/ to match upstream iso_builder (--dir /).
_assets_raw = os.environ.get('ASSETS_DIR', '/assets').strip()
if not _assets_raw or _assets_raw == '/':
    ASSETS_DIR = pathlib.Path('/')
else:
    ASSETS_DIR = pathlib.Path(_assets_raw.rstrip('/'))

# Root directory for static template files baked into the container image.
# Default /static matches the container layout; override in tests via STATIC_DIR.
STATIC_DIR = pathlib.Path(os.environ.get('STATIC_DIR', '/static'))

aap_namespace        = os.environ.get('AAP_NAMESPACE', 'aap')
ao_namespace         = os.environ.get('AO_NAMESPACE', 'automation-orchestrator')
ao_db_password       = _secrets.token_urlsafe(24)
pull_secret_path = os.environ.get('PULL_SECRET_FILE', '/run/secrets/pull-secret')
pull_secret      = pathlib.Path(pull_secret_path).read_text().strip()
base_domain      = os.environ['BASE_DOMAIN']
rendezvous_ip    = os.environ['RENDEZVOUS_IP']
ssh_key_path     = os.environ.get('SSH_KEY_FILE', '/run/secrets/ssh-key')
ssh_key          = p.read_text().strip() if (p := pathlib.Path(ssh_key_path)).exists() else ''
cluster_name     = os.environ.get('CLUSTER_NAME', 'appliance')
machine_network  = os.environ.get('MACHINE_NETWORK', '192.168.122.0/24')
disk_size_gb     = int(os.environ.get('DISK_SIZE_GB', '200'))
appliance_format = os.environ.get('APPLIANCE_FORMAT', 'live-iso')
disconnected     = os.environ.get('DISCONNECTED', '').lower() in ('1', 'true', 'yes')
aap_prerelease   = os.environ.get('AAP_PRERELEASE', '').lower() in ('1', 'true', 'yes')
ao_prerelease    = os.environ.get('AO_PRERELEASE', 'true').lower() in ('1', 'true', 'yes')

cpu_architecture = os.environ.get('CPU_ARCHITECTURE', 'x86_64')
if cpu_architecture not in ('x86_64', 'aarch64'):
    raise SystemExit(
        f"error: CPU_ARCHITECTURE must be one of: x86_64, aarch64 (got {cpu_architecture!r})"
    )

appliance_content = os.environ.get('APPLIANCE_CONTENT', 'aap').lower()
if appliance_content not in ('aap', 'ao', 'aap-ao'):
    raise SystemExit(
        f"error: APPLIANCE_CONTENT must be one of: aap, ao, aap-ao (got {appliance_content!r})"
    )
include_aap = appliance_content in ('aap', 'aap-ao')
include_ao  = appliance_content in ('ao', 'aap-ao')

(ASSETS_DIR / 'openshift' / 'crs').mkdir(parents=True, exist_ok=True)
(ASSETS_DIR / 'cluster-config').mkdir(parents=True, exist_ok=True)

# AAP manifests
if include_aap:
    aap_src = STATIC_DIR / 'openshift' / ('aap-prerelease.yaml' if aap_prerelease else 'aap.yaml')
    dst = ASSETS_DIR / 'openshift' / 'aap.yaml'
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(aap_src.read_text().replace('${AAP_NAMESPACE}', aap_namespace))
    shutil.copy(STATIC_DIR / 'openshift' / 'crs' / 'aap-cr.yaml', ASSETS_DIR / 'openshift' / 'crs' / 'aap-cr.yaml')
    if aap_prerelease:
        shutil.copy(
            STATIC_DIR / 'openshift' / 'idms-aap-prerelease.yaml',
            ASSETS_DIR / 'openshift' / 'idms-aap-prerelease.yaml',
        )

# AO manifests
if include_ao:
    ao_src = STATIC_DIR / 'openshift' / ('ao-prerelease.yaml' if ao_prerelease else 'ao.yaml')
    dst = ASSETS_DIR / 'openshift' / 'ao.yaml'
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(ao_src.read_text().replace('${AO_NAMESPACE}', ao_namespace))
    ao_cr = (
        (STATIC_DIR / 'openshift' / 'crs' / 'ao-cr.yaml').read_text()
        .replace('${AO_NAMESPACE}', ao_namespace)
        .replace('${AO_DB_PASSWORD}', ao_db_password)
    )
    (ASSETS_DIR / 'openshift' / 'crs' / 'ao-cr.yaml').write_text(ao_cr)
    if ao_prerelease:
        shutil.copy(
            STATIC_DIR / 'openshift' / 'idms-ao-prerelease.yaml',
            ASSETS_DIR / 'openshift' / 'idms-ao-prerelease.yaml',
        )

# Fully static files — copy unchanged
for f in ('local-path-provisioner.yaml', 'idms-additional-images.yaml'):
    shutil.copy(STATIC_DIR / 'openshift' / f, ASSETS_DIR / 'openshift' / f)


def literal_block(value):
    """Return a YAML literal block scalar string (key written separately by caller)."""
    lines = ['|\n']
    for line in value.rstrip('\n').split('\n'):
        lines.append(f'  {line}\n')
    return ''.join(lines)


# appliance-config.yaml — start from static template, inject secrets and images
cfg = (STATIC_DIR / 'config' / 'appliance-config.yaml').read_text()
cfg = cfg.replace('${CPU_ARCHITECTURE}', cpu_architecture)
if appliance_format != 'live-iso':
    cfg += f'diskSizeGB: {disk_size_gb}\n'
cfg += f'pullSecret: {literal_block(pull_secret)}'
if ssh_key:
    cfg += f'sshKey: {literal_block(ssh_key)}'

# Collect and deduplicate images across all included products
all_images: list[dict] = []
if include_aap:
    aap_images_file = STATIC_DIR / 'config' / ('aap-images-prerelease.yaml' if aap_prerelease else 'aap-images.yaml')
    all_images.extend(yaml.safe_load(aap_images_file.read_text()) or [])
if include_ao:
    ao_images_file = STATIC_DIR / 'config' / ('ao-images-prerelease.yaml' if ao_prerelease else 'ao-images.yaml')
    all_images.extend(yaml.safe_load(ao_images_file.read_text()) or [])

seen: set[str] = set()
cfg += 'additionalImages:\n'
for entry in all_images:
    if entry['name'] not in seen:
        seen.add(entry['name'])
        cfg += f'- name: {entry["name"]}\n'
cfg += '- name: docker.io/rancher/local-path-provisioner:v0.0.35\n'

(ASSETS_DIR / 'appliance-config.yaml').write_text(cfg)

# install-config.yaml — substitute env vars then append secrets
tmpl = (STATIC_DIR / 'config' / 'install-config.yaml').read_text()
for var, val in [
    ('${BASE_DOMAIN}',     base_domain),
    ('${CLUSTER_NAME}',    cluster_name),
    ('${MACHINE_NETWORK}', machine_network),
]:
    tmpl = tmpl.replace(var, val)
install_pull_secret = '{"auths":{"":{"auth":"dXNlcjpwYXNz"}}}' if disconnected else pull_secret
tmpl += f'pullSecret: {literal_block(install_pull_secret)}'
if ssh_key:
    tmpl += f'sshKey: {literal_block(ssh_key)}'
(ASSETS_DIR / 'cluster-config' / 'install-config.yaml').write_text(tmpl)

# agent-config.yaml — substitute env vars
tmpl = (STATIC_DIR / 'config' / 'agent-config.yaml').read_text()
tmpl = tmpl.replace('${RENDEZVOUS_IP}', rendezvous_ip)
(ASSETS_DIR / 'cluster-config' / 'agent-config.yaml').write_text(tmpl)
