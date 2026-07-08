#!/bin/bash
set -euo pipefail

# Imports a pre-built appliance OVA (exported from a fully installed VirtualBox VM).
#
# After installation OVN-K migrates the static IP to the br-ex bridge via a MAC-
# independent NM connection, so the physical NIC MAC does not affect cluster operation.
#
# The host-only NIC binding is not reliably preserved in the OVF export (VirtualBox
# omits the adapter name from the manifest); --nic1/--hostonlyadapter1 is applied
# after import to ensure the VM is always on vboxnet0.
#
# Defaults match the standard build parameters. Override if the appliance was built
# with different values (GATEWAY, MACHINE_NETWORK, RENDEZVOUS_IP).

# Must match GATEWAY / MACHINE_NETWORK used at build time
HOSTONLY_IP="${HOSTONLY_IP:-192.168.56.1}"
HOSTONLY_NETMASK="${HOSTONLY_NETMASK:-255.255.255.0}"
# Must match RENDEZVOUS_IP used at build time
RENDEZVOUS_IP="${RENDEZVOUS_IP:-192.168.56.100}"

VM_NAME="${VM_NAME:-aap-appliance}"
OVA="${OVA:-appliance.ova}"
REPLACE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ova)            OVA="$2";            shift 2 ;;
        --vm-name)        VM_NAME="$2";        shift 2 ;;
        --rendezvous-ip)  RENDEZVOUS_IP="$2";  shift 2 ;;
        --hostonly-ip)    HOSTONLY_IP="$2";    shift 2 ;;
        --replace)        REPLACE=true;        shift ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

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

echo "==> Importing $OVA as '$VM_NAME'..."
VBoxManage import "$OVA" --vsys 0 --vmname "$VM_NAME"

echo "==> Applying VM settings..."
VBoxManage modifyvm "$VM_NAME" \
    --nic1 hostonly \
    --hostonlyadapter1 vboxnet0

echo "==> Starting VM (headless)..."
VBoxManage startvm "$VM_NAME" --type headless

echo ""
echo "VM '$VM_NAME' is up."
echo "  ssh core@${RENDEZVOUS_IP}"
echo "  oc get nodes  # requires KUBECONFIG from the original build output"
