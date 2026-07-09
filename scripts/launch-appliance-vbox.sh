#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: launch-appliance-vbox.sh [OPTIONS]

Create and start a VirtualBox VM for the initial appliance installation.
Configures a host-only adapter (vboxnet0) for static networking, creates a
virtual disk, attaches the appliance ISO and agentconfig ISO, then starts the
VM headless. Only live-iso format is supported (not raw).

Options:
  --output-dir, -o <dir>  Directory containing appliance.iso and
                          cluster-config/agentconfig.noarch.iso
                            default: . (current directory)
  --mac <mac>             VM NIC MAC address (must match RENDEZVOUS_IP used at build time)
                            default: 52:54:00:aa:bb:01
  --rendezvous-ip <ip>    IP address of the rendezvous node (printed in post-install message)
                            default: 192.168.56.100
  --memory <mb>           VM memory in MB
                            default: 32768
  --vcpus <n>             Number of virtual CPUs
                            default: 8
  --disk-size <gb>        Virtual disk size in GB
                            default: 200
  --vm-name <name>        VirtualBox VM name
                            default: aap-appliance
  --replace               Power off and delete VM if it already exists
  --help, -h              Show this help and exit

Environment variables (override option defaults):
  OUTPUT_DIR          Same as --output-dir
  VM_MAC              Same as --mac
  RENDEZVOUS_IP       Same as --rendezvous-ip
  VM_MEMORY           Same as --memory
  VM_VCPUS            Same as --vcpus
  DISK_SIZE_GB        Same as --disk-size
  APPLIANCE_FORMAT    live-iso only; raw is not supported on VirtualBox
  VM_NAME             Same as --vm-name
  HOSTONLY_IP         Host-only adapter IP on the host side
                        default: 192.168.56.1
  HOSTONLY_NETMASK    Host-only adapter netmask
                        default: 255.255.255.0
EOF
}

OUTPUT_DIR="${OUTPUT_DIR:-.}"
VM_MAC="${VM_MAC:-52:54:00:aa:bb:01}"
RENDEZVOUS_IP="${RENDEZVOUS_IP:-192.168.56.100}"
VM_MEMORY="${VM_MEMORY:-32768}"
VM_VCPUS="${VM_VCPUS:-8}"
DISK_SIZE_GB="${DISK_SIZE_GB:-200}"
APPLIANCE_FORMAT="${APPLIANCE_FORMAT:-live-iso}"
VM_NAME="${VM_NAME:-aap-appliance}"
HOSTONLY_IP="${HOSTONLY_IP:-192.168.56.1}"
HOSTONLY_NETMASK="${HOSTONLY_NETMASK:-255.255.255.0}"
REPLACE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir|-o)  OUTPUT_DIR="$2";      shift 2 ;;
        --mac)            VM_MAC="$2";           shift 2 ;;
        --rendezvous-ip)  RENDEZVOUS_IP="$2";   shift 2 ;;
        --memory)         VM_MEMORY="$2";        shift 2 ;;
        --vcpus)          VM_VCPUS="$2";         shift 2 ;;
        --disk-size)      DISK_SIZE_GB="$2";     shift 2 ;;
        --vm-name)        VM_NAME="$2";          shift 2 ;;
        --replace)        REPLACE=true;          shift ;;
        --help|-h)        usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Normalize MAC to VirtualBox internal format (12 lowercase hex digits, no separators)
VM_MAC_VBOX="$(echo "$VM_MAC" | tr -d ':-' | tr '[:upper:]' '[:lower:]')"

IMAGES_DIR="${HOME}/VirtualBox VMs"

echo "==> Configuring host-only adapter (vboxnet0)..."
if ! VBoxManage list hostonlyifs | grep -q "vboxnet0"; then
    VBoxManage hostonlyif create
fi
VBoxManage hostonlyif ipconfig vboxnet0 --ip "$HOSTONLY_IP" --netmask "$HOSTONLY_NETMASK"

if VBoxManage showvminfo "$VM_NAME" &>/dev/null; then
    if [[ "$REPLACE" != "true" ]]; then
        echo "error: VM '$VM_NAME' already exists. Use --replace to destroy and recreate it." >&2
        exit 1
    fi
    VBoxManage controlvm "$VM_NAME" poweroff 2>/dev/null || true
    VBoxManage unregistervm "$VM_NAME" --delete
fi

echo "==> Creating VM '$VM_NAME'..."
VBoxManage createvm --name "$VM_NAME" --ostype "RedHat_64" --register

VBoxManage modifyvm "$VM_NAME" \
    --firmware efi \
    --memory "$VM_MEMORY" \
    --cpus "$VM_VCPUS" \
    --rtcuseutc on \
    --nic1 hostonly \
    --hostonlyadapter1 vboxnet0 \
    --nictype1 virtio \
    --macaddress1 "$VM_MAC_VBOX" \
    --paravirtprovider kvm \
    --boot1 dvd --boot2 disk --boot3 none --boot4 none

echo "==> Creating virtual disk (${DISK_SIZE_GB} GB)..."
VBoxManage storagectl "$VM_NAME" --name "SATA" --add sata --controller IntelAhci --portcount 4
VBoxManage createmedium disk \
    --filename "$IMAGES_DIR/$VM_NAME/$VM_NAME.vdi" \
    --size $((DISK_SIZE_GB * 1024)) \
    --format VDI
VBoxManage storageattach "$VM_NAME" \
    --storagectl "SATA" --port 0 --device 0 \
    --type hdd \
    --medium "$IMAGES_DIR/$VM_NAME/$VM_NAME.vdi"

echo "==> Attaching ISOs..."
VBoxManage storagectl "$VM_NAME" --name "IDE" --add ide
if [[ "$APPLIANCE_FORMAT" = "live-iso" ]]; then
    VBoxManage storageattach "$VM_NAME" \
        --storagectl "IDE" --port 0 --device 0 \
        --type dvddrive \
        --medium "$(realpath "$OUTPUT_DIR/appliance.iso")"
else
    # raw: copy the disk image into a VDI and skip the appliance ISO slot
    echo "error: APPLIANCE_FORMAT=raw is not supported for VirtualBox; use live-iso" >&2
    exit 1
fi

VBoxManage storageattach "$VM_NAME" \
    --storagectl "IDE" --port 1 --device 0 \
    --type dvddrive \
    --medium "$(realpath "$OUTPUT_DIR/cluster-config/agentconfig.noarch.iso")"

echo "==> Starting VM (headless)..."
VBoxManage startvm "$VM_NAME" --type headless

echo ""
echo "VM '$VM_NAME' started. Monitor installation:"
echo "  ssh core@${RENDEZVOUS_IP} sudo journalctl -fu assisted-service"
echo ""
echo "Once installed, access the cluster:"
echo "  export KUBECONFIG=$(realpath "$OUTPUT_DIR")/cluster-config/auth/kubeconfig"
