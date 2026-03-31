#!/bin/bash
set -euo pipefail

# Validate required env vars
for var in PULL_SECRET BASE_DOMAIN RENDEZVOUS_IP; do
    if [ -z "${!var:-}" ]; then
        echo "error: ${var} is required" >&2
        exit 1
    fi
done

mkdir -p /assets/openshift/crs /assets/cluster-config

python3 - <<'PYEOF'
import os, shutil, pathlib

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
    ('/static/openshift/aap.yaml',         '/assets/openshift/aap.yaml'),
    ('/static/openshift/crs/aap-cr.yaml',  '/assets/openshift/crs/aap-cr.yaml'),
]:
    content = pathlib.Path(src).read_text().replace('${NAMESPACE}', namespace)
    pathlib.Path(dst).write_text(content)

# Fully static files — copy unchanged
for f in ('local-path-provisioner.yaml', 'idms-additional-images.yaml'):
    shutil.copy(f'/static/openshift/{f}', f'/assets/openshift/{f}')


def write_block(f, key, value):
    """Write a YAML literal block scalar — handles JSON strings and multi-line SSH keys."""
    f.write(f'{key}: |\n')
    for line in value.rstrip('\n').split('\n'):
        f.write(f'  {line}\n')


# appliance-config.yaml
with open('/assets/appliance-config.yaml', 'w') as f:
    f.write('apiVersion: v1beta1\n')
    f.write('kind: ApplianceConfig\n')
    f.write('ocpRelease:\n')
    f.write('  version: "4.18.34"\n')
    f.write('  channel: stable\n')
    f.write('  cpuArchitecture: x86_64\n')
    if appliance_format != 'live-iso':
        f.write(f'diskSizeGB: {disk_size_gb}\n')
    write_block(f, 'pullSecret', pull_secret)
    if ssh_key:
        write_block(f, 'sshKey', ssh_key)
    f.write('enableDefaultSources: false\n')
    f.write('operators:\n')
    f.write('- catalog: registry.redhat.io/redhat/redhat-operator-index:v4.18\n')
    f.write('  packages:\n')
    f.write('  - name: ansible-automation-platform-operator\n')
    f.write('    channels:\n')
    f.write('    - name: stable-2.6\n')
    f.write('additionalImages:\n')
    f.write('- name: registry.redhat.io/openshift4/ose-kube-rbac-proxy-rhel9:v4.18\n')
    f.write('- name: registry.redhat.io/rhel9/redis-6:1\n')
    f.write('- name: docker.io/rancher/local-path-provisioner:v0.0.35\n')

# cluster-config/install-config.yaml
with open('/assets/cluster-config/install-config.yaml', 'w') as f:
    f.write('apiVersion: v1\n')
    f.write(f'baseDomain: {base_domain}\n')
    f.write('metadata:\n')
    f.write(f'  name: {cluster_name}\n')
    f.write('controlPlane:\n')
    f.write('  name: master\n')
    f.write('  replicas: 1\n')
    f.write('compute:\n')
    f.write('- name: worker\n')
    f.write('  replicas: 0\n')
    f.write('networking:\n')
    f.write('  networkType: OVNKubernetes\n')
    f.write('  machineNetwork:\n')
    f.write(f'  - cidr: {machine_network}\n')
    f.write('platform:\n')
    f.write('  none: {}\n')
    write_block(f, 'pullSecret', pull_secret)
    if ssh_key:
        write_block(f, 'sshKey', ssh_key)

# cluster-config/agent-config.yaml
with open('/assets/cluster-config/agent-config.yaml', 'w') as f:
    f.write('apiVersion: v1alpha1\n')
    f.write('kind: AgentConfig\n')
    f.write(f'rendezvousIP: {rendezvous_ip}\n')

PYEOF

BUILD_ARGS="build"
if [ "${APPLIANCE_FORMAT:-raw}" = "live-iso" ]; then
    BUILD_ARGS="build live-iso"
fi

# Workaround: openshift-appliance mounts the registry storage at /assetstemp/data
# instead of /assets/temp/data. Symlink so the path resolves correctly.
ln -sfn /assets/temp /assetstemp

/openshift-appliance --dir /assets $BUILD_ARGS

# Generate the agent config ISO using the openshift-install binary cached by the build step.
# openshift-install deletes install-config.yaml and agent-config.yaml after reading them,
# so work in a temp dir to preserve the originals in cluster-config/.
OCP_INSTALL="/assets/cache/4.18.34-x86_64/openshift-install"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

cp /assets/cluster-config/install-config.yaml "$TMPDIR/"
cp /assets/cluster-config/agent-config.yaml "$TMPDIR/"

"$OCP_INSTALL" agent create config-image --dir "$TMPDIR"

cp "$TMPDIR/agentconfig.noarch.iso" /assets/cluster-config/agentconfig.noarch.iso

if [ -d "$TMPDIR/auth" ]; then
    cp -r "$TMPDIR/auth" /assets/cluster-config/
fi

echo ""
echo "Appliance ready. Boot the node, then monitor installation:"
echo "  ssh core@${RENDEZVOUS_IP} sudo journalctl -fu assisted-service"
echo ""
echo "Once installed, access the cluster:"
echo "  export KUBECONFIG=/assets/cluster-config/auth/kubeconfig"
