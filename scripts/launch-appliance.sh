#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: launch-appliance.sh [OPTIONS]

Create and start a libvirt VM for the initial appliance installation.
Copies the appliance ISO (or raw disk) and the agentconfig ISO into
/var/lib/libvirt/images/ then launches virt-install.

Options:
  --output-dir, -o <dir>  Directory containing appliance.iso / appliance.raw
                          and cluster-config/agentconfig.noarch.iso
                            default: . (current directory)
  --appliance-iso <path>  Explicit path to appliance.iso (or appliance.raw for
                          --format raw). Overrides --output-dir for this file.
  --agentconfig-iso <path>
                          Explicit path to agentconfig.noarch.iso. Overrides
                          --output-dir for this file.
  --mac <mac>             VM NIC MAC address (must match RENDEZVOUS_IP reservation)
                            default: 52:54:00:aa:bb:01
  --rendezvous-ip <ip>    IP address printed in the post-install message
                            default: 192.168.122.100
  --memory <mb>           VM memory in MB
                            default: 32768
  --vcpus <n>             Number of virtual CPUs
                            default: 8
  --format <fmt>          Appliance image format: live-iso or raw
                            default: live-iso
  --vm-name <name>        libvirt domain name
                            default: aap-appliance
  --network <name>        libvirt network to attach to
                            default: default
  --replace               Destroy and recreate VM if it already exists
  --help, -h              Show this help and exit

Environment variables (override option defaults):
  OUTPUT_DIR          Same as --output-dir
  APPLIANCE_ISO       Same as --appliance-iso
  AGENTCONFIG_ISO     Same as --agentconfig-iso
  VM_MAC              Same as --mac
  RENDEZVOUS_IP       Same as --rendezvous-ip
  VM_MEMORY           Same as --memory
  VM_VCPUS            Same as --vcpus
  APPLIANCE_FORMAT    Same as --format
  VM_NAME             Same as --vm-name
  LIBVIRT_NETWORK     Same as --network
EOF
}

OUTPUT_DIR="${OUTPUT_DIR:-.}"
APPLIANCE_ISO="${APPLIANCE_ISO:-}"
AGENTCONFIG_ISO="${AGENTCONFIG_ISO:-}"
VM_MAC="${VM_MAC:-52:54:00:aa:bb:01}"
RENDEZVOUS_IP="${RENDEZVOUS_IP:-192.168.122.100}"
VM_MEMORY="${VM_MEMORY:-32768}"
VM_VCPUS="${VM_VCPUS:-8}"
APPLIANCE_FORMAT="${APPLIANCE_FORMAT:-live-iso}"
VM_NAME="${VM_NAME:-aap-appliance}"
LIBVIRT_NETWORK="${LIBVIRT_NETWORK:-default}"
REPLACE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir|-o)    OUTPUT_DIR="$2";       shift 2 ;;
        --appliance-iso)    APPLIANCE_ISO="$2";    shift 2 ;;
        --agentconfig-iso)  AGENTCONFIG_ISO="$2";  shift 2 ;;
        --mac)              VM_MAC="$2";            shift 2 ;;
        --rendezvous-ip)    RENDEZVOUS_IP="$2";    shift 2 ;;
        --memory)           VM_MEMORY="$2";         shift 2 ;;
        --vcpus)            VM_VCPUS="$2";          shift 2 ;;
        --format)           APPLIANCE_FORMAT="$2";  shift 2 ;;
        --vm-name)          VM_NAME="$2";           shift 2 ;;
        --network)          LIBVIRT_NETWORK="$2";   shift 2 ;;
        --replace)          REPLACE=true;           shift ;;
        --help|-h)          usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Resolve ISO paths: explicit flags take precedence over --output-dir defaults
if [[ -z "$APPLIANCE_ISO" ]]; then
    if [[ "$APPLIANCE_FORMAT" = "live-iso" ]]; then
        APPLIANCE_ISO="$OUTPUT_DIR/appliance.iso"
    else
        APPLIANCE_ISO="$OUTPUT_DIR/appliance.raw"
    fi
fi
if [[ -z "$AGENTCONFIG_ISO" ]]; then
    AGENTCONFIG_ISO="$OUTPUT_DIR/cluster-config/agentconfig.noarch.iso"
fi

IMAGES_DIR=/var/lib/libvirt/images

if sudo virsh dominfo "$VM_NAME" &>/dev/null; then
    if [[ "$REPLACE" != "true" ]]; then
        echo "error: VM '$VM_NAME' already exists. Use --replace to destroy and recreate it." >&2
        exit 1
    fi
    sudo virsh destroy "$VM_NAME" 2>/dev/null || true
    if [[ "$APPLIANCE_FORMAT" = "live-iso" ]]; then
        sudo virsh undefine "$VM_NAME" --remove-all-storage
    else
        sudo virsh undefine "$VM_NAME"
    fi
fi

if [[ "$APPLIANCE_FORMAT" = "live-iso" ]]; then
    sudo cp "$APPLIANCE_ISO" "$IMAGES_DIR/${VM_NAME}.iso"
else
    sudo cp "$APPLIANCE_ISO" "$IMAGES_DIR/${VM_NAME}.raw"
fi

sudo cp "$AGENTCONFIG_ISO" "$IMAGES_DIR/agentconfig.noarch.iso"

if [[ "$APPLIANCE_FORMAT" = "live-iso" ]]; then
    sudo virt-install \
        --name "$VM_NAME" \
        --memory "$VM_MEMORY" \
        --vcpus "$VM_VCPUS" \
        --disk "size=200,bus=virtio" \
        --disk "path=$IMAGES_DIR/${VM_NAME}.iso,device=cdrom,readonly=on" \
        --disk "path=$IMAGES_DIR/agentconfig.noarch.iso,device=cdrom,readonly=on" \
        --network "network=$LIBVIRT_NETWORK,model=virtio,mac=$VM_MAC" \
        --os-variant rhel9-unknown \
        --events on_reboot=restart,on_poweroff=restart,on_crash=restart \
        --boot hd,cdrom \
        --noautoconsole
else
    sudo virt-install \
        --name "$VM_NAME" \
        --memory "$VM_MEMORY" \
        --vcpus "$VM_VCPUS" \
        --disk "path=$IMAGES_DIR/${VM_NAME}.raw,format=raw,bus=virtio" \
        --disk "path=$IMAGES_DIR/agentconfig.noarch.iso,device=cdrom,readonly=on" \
        --network "network=$LIBVIRT_NETWORK,model=virtio,mac=$VM_MAC" \
        --os-variant rhel9-unknown \
        --events on_reboot=restart,on_poweroff=restart,on_crash=restart \
        --boot hd \
        --noautoconsole \
        --import
fi

echo ""
echo "VM '$VM_NAME' started. Monitor installation:"
echo "  ssh core@${RENDEZVOUS_IP} sudo journalctl -fu assisted-service"
echo ""
echo "Once installed:"
echo "  export KUBECONFIG=$(realpath "$OUTPUT_DIR")/cluster-config/auth/kubeconfig"
