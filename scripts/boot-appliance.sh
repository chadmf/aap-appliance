#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: boot-appliance.sh --qcow2 <path> [OPTIONS]

Import a raw QCOW2 appliance disk image into libvirt and start a VM from it.
Use this after a full appliance installation to re-boot the finished VM, not
for the initial install (use launch-appliance.sh for that).

Options:
  --qcow2, -q <path>   Path to the appliance QCOW2 image      (required)
  --mac <mac>          VM NIC MAC address
                         default: 52:54:00:aa:bb:01
  --memory <mb>        VM memory in MB
                         default: 32768
  --vcpus <n>          Number of virtual CPUs
                         default: 8
  --vm-name <name>     libvirt domain name
                         default: aap-appliance
  --network <name>     libvirt network to attach to
                         default: default
  --replace            Destroy and recreate VM if it already exists
  --help, -h           Show this help and exit

Environment variables (override option defaults):
  QCOW2_PATH           Same as --qcow2
  VM_MAC               Same as --mac
  VM_MEMORY            Same as --memory
  VM_VCPUS             Same as --vcpus
  VM_NAME              Same as --vm-name
  LIBVIRT_NETWORK      Same as --network
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QCOW2_PATH="${QCOW2_PATH:-}"
VM_MAC="${VM_MAC:-52:54:00:aa:bb:01}"
VM_MEMORY="${VM_MEMORY:-32768}"
VM_VCPUS="${VM_VCPUS:-8}"
VM_NAME="${VM_NAME:-aap-appliance}"
LIBVIRT_NETWORK="${LIBVIRT_NETWORK:-default}"
REPLACE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --qcow2|-q)    QCOW2_PATH="$2";       shift 2 ;;
        --mac)         VM_MAC="$2";            shift 2 ;;
        --memory)      VM_MEMORY="$2";         shift 2 ;;
        --vcpus)       VM_VCPUS="$2";          shift 2 ;;
        --vm-name)     VM_NAME="$2";           shift 2 ;;
        --network)     LIBVIRT_NETWORK="$2";   shift 2 ;;
        --replace)     REPLACE=true;           shift ;;
        --help|-h)     usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$QCOW2_PATH" ]]; then
    echo "error: --qcow2 <path> is required" >&2
    exit 1
fi

if [[ ! -f "$QCOW2_PATH" ]]; then
    echo "error: file not found: $QCOW2_PATH" >&2
    exit 1
fi

IMAGES_DIR=/var/lib/libvirt/images

if sudo virsh dominfo "$VM_NAME" &>/dev/null; then
    if [[ "$REPLACE" != "true" ]]; then
        echo "error: VM '$VM_NAME' already exists. Use --replace to destroy and recreate it." >&2
        exit 1
    fi
    sudo virsh destroy "$VM_NAME" 2>/dev/null || true
    sudo virsh undefine "$VM_NAME"
fi

sudo cp "$QCOW2_PATH" "$IMAGES_DIR/${VM_NAME}.qcow2"

sudo virt-install \
    --name "$VM_NAME" \
    --memory "$VM_MEMORY" \
    --vcpus "$VM_VCPUS" \
    --disk "path=$IMAGES_DIR/${VM_NAME}.qcow2,format=qcow2,bus=virtio" \
    --network "network=$LIBVIRT_NETWORK,model=virtio,mac=$VM_MAC" \
    --os-variant rhel9-unknown \
    --events on_reboot=restart,on_poweroff=restart,on_crash=restart \
    --boot hd \
    --noautoconsole \
    --import

sudo virsh autostart "$VM_NAME"

"$SCRIPT_DIR/setup-port-forwarding.sh"

echo ""
echo "VM '$VM_NAME' started."
echo "Run 'export KUBECONFIG=<path-to>/cluster-config/auth/kubeconfig' to access the cluster."
