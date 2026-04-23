#!/bin/bash
set -euo pipefail

PULL_SECRET_FILE=/run/secrets/pull-secret
SSH_KEY_FILE=/run/secrets/ssh-key

# Validate required inputs
if [ ! -f "${PULL_SECRET_FILE}" ] || [ ! -s "${PULL_SECRET_FILE}" ]; then
    echo "error: pull secret file not found or empty at ${PULL_SECRET_FILE}" >&2
    exit 1
fi
if [ ! -f "${SSH_KEY_FILE}" ]; then
    echo "warning: no SSH key found at ${SSH_KEY_FILE}; SSH access to the node will not be configured" >&2
fi
for var in BASE_DOMAIN RENDEZVOUS_IP; do
    if [ -z "${!var:-}" ]; then
        echo "error: ${var} is required" >&2
        exit 1
    fi
done

mkdir -p /assets/openshift/crs /assets/cluster-config

python3 /static/scripts/generate-configs.py

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
