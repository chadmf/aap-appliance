#!/bin/bash
set -euo pipefail

PULL_SECRET_FILE="${PULL_SECRET_FILE:-/run/secrets/pull-secret}"
SSH_KEY_FILE="${SSH_KEY_FILE:-/run/secrets/ssh-key}"
STATIC_DIR="${STATIC_DIR:-/static}"
OPENSHIFT_APPLIANCE_BIN="${OPENSHIFT_APPLIANCE_BIN:-/openshift-appliance}"
ASSETS_DIR="${ASSETS_DIR:-/assets}"
ASSETSTEMP_LINK="${ASSETSTEMP_LINK:-/assetstemp}"

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

mkdir -p "$ASSETS_DIR/openshift/crs" "$ASSETS_DIR/cluster-config"

python3 "$STATIC_DIR/scripts/generate-configs.py"

if [ "${SKIP_APPLIANCE_BUILD:-false}" != "true" ]; then
    BUILD_ARGS="build"
    if [ "${APPLIANCE_FORMAT:-live-iso}" = "live-iso" ]; then
        BUILD_ARGS="build live-iso"
    fi

    # Workaround: openshift-appliance mounts the registry storage at /assetstemp/data
    # instead of /assets/temp/data. Symlink so the path resolves correctly.
    ln -sfn "$ASSETS_DIR/temp" "$ASSETSTEMP_LINK"

    "$OPENSHIFT_APPLIANCE_BIN" --dir "$ASSETS_DIR" $BUILD_ARGS
else
    echo "Skipping appliance build (SKIP_APPLIANCE_BUILD=true). Reusing cached openshift-install."
fi

# Generate the agent config ISO using the openshift-install binary cached by the build step.
# openshift-install deletes install-config.yaml and agent-config.yaml after reading them,
# so work in a temp dir to preserve the originals in cluster-config/.
OCP_VERSION=$(python3 -c "import yaml; cfg=yaml.safe_load(open('$STATIC_DIR/config/appliance-config.yaml')); print(cfg['ocpRelease']['version'])")
CACHE_INSTALL="$ASSETS_DIR/cache/${OCP_VERSION}-${CPU_ARCHITECTURE:-x86_64}/openshift-install"

if [ -n "${OPENSHIFT_INSTALL_BIN:-}" ]; then
    OCP_INSTALL="$OPENSHIFT_INSTALL_BIN"
else
    OCP_INSTALL="$CACHE_INSTALL"
fi

if [ ! -x "$OCP_INSTALL" ]; then
    echo "error: openshift-install not found at $OCP_INSTALL" >&2
    if [ "${SKIP_APPLIANCE_BUILD:-false}" = "true" ]; then
        echo "  When skipping the build, either mount the /assets/ dir from a prior run" >&2
        echo "  or set OPENSHIFT_INSTALL_BIN=/path/to/openshift-install" >&2
    fi
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

cp "$ASSETS_DIR/cluster-config/install-config.yaml" "$TMPDIR/"
cp "$ASSETS_DIR/cluster-config/agent-config.yaml" "$TMPDIR/"

"$OCP_INSTALL" agent create config-image --dir "$TMPDIR"

cp "$TMPDIR/agentconfig.noarch.iso" "$ASSETS_DIR/cluster-config/agentconfig.noarch.iso"

if [ -d "$TMPDIR/auth" ]; then
    cp -r "$TMPDIR/auth" "$ASSETS_DIR/cluster-config/"
fi

echo ""
echo "Appliance ready. Boot the node, then monitor installation:"
echo "  ssh core@${RENDEZVOUS_IP} sudo journalctl -fu assisted-service"
echo ""
echo "Once installed, access the cluster:"
echo "  export KUBECONFIG=$ASSETS_DIR/cluster-config/auth/kubeconfig"
