#!/bin/bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-.}"
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
        --output-dir|-o)  OUTPUT_DIR="$2";       shift 2 ;;
        --mac)            VM_MAC="$2";            shift 2 ;;
        --rendezvous-ip)  RENDEZVOUS_IP="$2";    shift 2 ;;
        --memory)         VM_MEMORY="$2";         shift 2 ;;
        --vcpus)          VM_VCPUS="$2";          shift 2 ;;
        --format)         APPLIANCE_FORMAT="$2";  shift 2 ;;
        --vm-name)        VM_NAME="$2";           shift 2 ;;
        --network)        LIBVIRT_NETWORK="$2";   shift 2 ;;
        --replace)        REPLACE=true;           shift ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

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
    sudo cp "$OUTPUT_DIR/appliance.iso" "$IMAGES_DIR/${VM_NAME}.iso"
else
    sudo cp "$OUTPUT_DIR/appliance.raw" "$IMAGES_DIR/${VM_NAME}.raw"
fi
sudo cp "$OUTPUT_DIR/cluster-config/agentconfig.noarch.iso" "$IMAGES_DIR/agentconfig.noarch.iso"

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
