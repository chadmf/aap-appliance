#!/bin/bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: dhcp-reserve.sh [OPTIONS]

Add a static DHCP reservation to a libvirt network so the rendezvous node
always gets the same IP address. Idempotent — skips if the reservation already
exists.

Options:
  --mac <mac>        MAC address to reserve an IP for
                       default: 52:54:00:aa:bb:01
  --ip <ip>          IP address to assign to the MAC
                       default: 192.168.122.100
  --network <name>   libvirt network to update
                       default: default
  --help, -h         Show this help and exit

Environment variables (override option defaults):
  VM_MAC             Same as --mac
  RENDEZVOUS_IP      Same as --ip
  LIBVIRT_NETWORK    Same as --network
EOF
}

VM_MAC="${VM_MAC:-52:54:00:aa:bb:01}"
RENDEZVOUS_IP="${RENDEZVOUS_IP:-192.168.122.100}"
LIBVIRT_NETWORK="${LIBVIRT_NETWORK:-default}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mac)       VM_MAC="$2";          shift 2 ;;
        --ip)        RENDEZVOUS_IP="$2";   shift 2 ;;
        --network)   LIBVIRT_NETWORK="$2"; shift 2 ;;
        --help|-h)   usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

if sudo virsh net-dumpxml "$LIBVIRT_NETWORK" | grep -q "$VM_MAC"; then
    echo "DHCP reservation for $VM_MAC already exists in network '$LIBVIRT_NETWORK', skipping."
    exit 0
fi

sudo virsh net-update "$LIBVIRT_NETWORK" add ip-dhcp-host \
    "<host mac=\"$VM_MAC\" ip=\"$RENDEZVOUS_IP\"/>" \
    --live --config

echo "Reserved $RENDEZVOUS_IP for $VM_MAC on network '$LIBVIRT_NETWORK'."
