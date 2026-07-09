#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: entrypoint.sh [SUBCOMMAND] [--help]

Build an AAP appliance ISO and/or a site-specific agentconfig ISO.

SUBCOMMANDS
  build-appliance     Phase 1 — build the distributable appliance ISO.
                      Caches images for both AAP and AO unconditionally so any
                      APPLIANCE_CONTENT choice works at phase 2 without a rebuild.
                      Does NOT require BASE_DOMAIN or RENDEZVOUS_IP.

  build-agentconfig   Phase 2 — build the site-specific agentconfig ISO.
                      Writes operator manifests chosen by APPLIANCE_CONTENT into
                      the ISO so openshift-install picks them up at install time.
                      Requires a pre-existing /assets/cache/ from build-appliance
                      (or OPENSHIFT_INSTALL_BIN pointing to the binary directly).

  (no subcommand)     One-shot — run both phases in sequence.
                      Convenient for single-machine setups where the appliance and
                      agentconfig ISOs are built together.

ENVIRONMENT VARIABLES — all phases
  PULL_SECRET_FILE    Path to pull secret JSON              (required)
                        default: /run/secrets/pull-secret
  SSH_KEY_FILE        Path to SSH public key
                        default: /run/secrets/ssh-key
  CPU_ARCHITECTURE    x86_64 or aarch64
                        default: x86_64
  APPLIANCE_FORMAT    live-iso or raw
                        default: live-iso
  DISK_SIZE_GB        Disk size in GB; only used when APPLIANCE_FORMAT=raw
                        default: 200
  AAP_PRERELEASE      Cache pre-release AAP images (true/false)
                        default: false
  AO_PRERELEASE       Cache pre-release AO images (true/false)
                        default: true  (AO only has pre-release builds)
ENVIRONMENT VARIABLES — build-agentconfig / one-shot
  BASE_DOMAIN         Cluster base domain                   (required)
  RENDEZVOUS_IP       Rendezvous node IP address            (required)
  APPLIANCE_CONTENT   Operators to install:
                        aap          AAP operator only
                        ao           Automation Orchestrator only
                        aap-with-ao  AAP + AO operators
                        aap-full     All AAP product operators (currently aap + ao;
                                     use this to get future operators automatically)
                        default: aap-full
  CLUSTER_NAME        OpenShift cluster name
                        default: appliance
  MACHINE_NETWORK     Machine network CIDR
                        default: 192.168.122.0/24
  GATEWAY             Static gateway IP; enables static network config
  VM_MAC / VM_MAC_0   MAC address of the rendezvous NIC; required with GATEWAY
  DNS_SERVER          DNS server IP; used only with static network config
                        default: 8.8.8.8
  DISCONNECTED        Use dummy pull secret in install-config (true/false)
                        default: false
  AAP_NAMESPACE       Namespace for the AAP operator
                        default: aap
  AO_NAMESPACE        Namespace for the AO operator
                        default: automation-orchestrator
  OPENSHIFT_INSTALL_BIN  Path to openshift-install binary
                        default: auto-detected from /assets/cache/

EXAMPLES
  # Phase 1 — build once, distribute to sites
  podman run --rm \
    -v ./assets:/assets -v ./pull-secret:/run/secrets/pull-secret \
    aap-appliance build-appliance

  # Phase 2 — per-site, run by end users
  podman run --rm \
    -v ./assets:/assets -v ./pull-secret:/run/secrets/pull-secret \
    -v ./ssh-key:/run/secrets/ssh-key \
    -e BASE_DOMAIN=example.com -e RENDEZVOUS_IP=192.168.1.10 \
    -e APPLIANCE_CONTENT=aap-full \
    aap-appliance build-agentconfig
EOF
}

PULL_SECRET_FILE="${PULL_SECRET_FILE:-/run/secrets/pull-secret}"
SSH_KEY_FILE="${SSH_KEY_FILE:-/run/secrets/ssh-key}"
STATIC_DIR="${STATIC_DIR:-/static}"
OPENSHIFT_APPLIANCE_BIN="${OPENSHIFT_APPLIANCE_BIN:-/openshift-appliance}"
ASSETS_DIR="${ASSETS_DIR:-/assets}"
ASSETSTEMP_LINK="${ASSETSTEMP_LINK:-/assetstemp}"

# Subcommand: build-appliance | build-agentconfig | (empty = one-shot, both phases)
SUBCOMMAND="${1:-}"

if [[ "$SUBCOMMAND" == "--help" || "$SUBCOMMAND" == "-h" ]]; then
    usage
    exit 0
fi

if [[ -n "$SUBCOMMAND" && "$SUBCOMMAND" != "build-appliance" && "$SUBCOMMAND" != "build-agentconfig" ]]; then
    echo "error: unknown subcommand: $SUBCOMMAND" >&2
    echo "  usage: $0 [build-appliance | build-agentconfig]" >&2
    exit 1
fi

# Pull secret is required by all modes
if [ ! -f "${PULL_SECRET_FILE}" ] || [ ! -s "${PULL_SECRET_FILE}" ]; then
    echo "error: pull secret file not found or empty at ${PULL_SECRET_FILE}" >&2
    exit 1
fi
if [ ! -f "${SSH_KEY_FILE}" ]; then
    echo "warning: no SSH key found at ${SSH_KEY_FILE}; SSH access to the node will not be configured" >&2
fi

# BASE_DOMAIN and RENDEZVOUS_IP: required for agentconfig phase.
if [[ "$SUBCOMMAND" != "build-appliance" ]]; then
    for var in BASE_DOMAIN RENDEZVOUS_IP; do
        if [ -z "${!var:-}" ]; then
            echo "error: ${var} is required" >&2
            exit 1
        fi
    done
fi

# ── Phase 1: build the distributable appliance ────────────────────────────────

if [[ "$SUBCOMMAND" == "build-appliance" || -z "$SUBCOMMAND" ]]; then
    BUILD_MODE=appliance python3 "$STATIC_DIR/scripts/generate-configs.py"

    BUILD_ARGS="build"
    if [ "${APPLIANCE_FORMAT:-live-iso}" = "live-iso" ]; then
        BUILD_ARGS="build live-iso"
    fi

    # Workaround: openshift-appliance mounts the registry storage at /assetstemp/data
    # instead of /assets/temp/data. Symlink so the path resolves correctly.
    ln -sfn "$ASSETS_DIR/temp" "$ASSETSTEMP_LINK"

    "$OPENSHIFT_APPLIANCE_BIN" --dir "$ASSETS_DIR" $BUILD_ARGS

fi

# ── Phase 2: build the site-specific agentconfig ISO ─────────────────────────

if [[ "$SUBCOMMAND" == "build-agentconfig" || -z "$SUBCOMMAND" ]]; then
    BUILD_MODE=agentconfig python3 "$STATIC_DIR/scripts/generate-configs.py"

    # Use openshift-install from the cache populated by phase 1, or an explicit override.
    OCP_VERSION=$(python3 -c "import yaml; cfg=yaml.safe_load(open('$STATIC_DIR/config/appliance-config.yaml')); print(cfg['ocpRelease']['version'])")
    CACHE_INSTALL="$ASSETS_DIR/cache/${OCP_VERSION}-${CPU_ARCHITECTURE:-x86_64}/openshift-install"

    if [ -n "${OPENSHIFT_INSTALL_BIN:-}" ]; then
        OCP_INSTALL="$OPENSHIFT_INSTALL_BIN"
    else
        OCP_INSTALL="$CACHE_INSTALL"
    fi

    if [ ! -x "$OCP_INSTALL" ]; then
        echo "error: openshift-install not found at $OCP_INSTALL" >&2
        if [[ "$SUBCOMMAND" == "build-agentconfig" ]]; then
            echo "  Mount /assets/ from a prior build-appliance run, or set OPENSHIFT_INSTALL_BIN=/path/to/openshift-install" >&2
        fi
        exit 1
    fi

    # openshift-install deletes install-config.yaml and agent-config.yaml after reading them,
    # so work in a temp dir to preserve the originals in cluster-config/.
    TMPDIR=$(mktemp -d)
    trap 'rm -rf "$TMPDIR"' EXIT

    cp "$ASSETS_DIR/cluster-config/install-config.yaml" "$TMPDIR/"
    cp "$ASSETS_DIR/cluster-config/agent-config.yaml" "$TMPDIR/"

    # Copy operator manifests into tmpdir so openshift-install embeds them in the ISO.
    # openshift-install agent create config-image scans <dir>/openshift/ for extra manifests.
    if [ -d "$ASSETS_DIR/cluster-config/openshift" ] && [ -n "$(ls -A "$ASSETS_DIR/cluster-config/openshift" 2>/dev/null)" ]; then
        cp -r "$ASSETS_DIR/cluster-config/openshift" "$TMPDIR/openshift"
    fi

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
fi
