#!/bin/bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: setup-port-forwarding.sh [--help]

Install a libvirt network hook that forwards host ports 80, 443, and 6443 to
the appliance VM using nft DNAT rules. The hook fires automatically whenever
the libvirt default network starts, so port forwarding survives host reboots.
Also applies the rules immediately without waiting for a libvirt restart.

Environment variables:
  RENDEZVOUS_IP   IP address of the appliance VM to forward traffic to
                    default: 192.168.122.100
EOF
    exit 0
fi

VM_IP="${RENDEZVOUS_IP:-192.168.122.100}"
HOOK_DIR=/etc/libvirt/hooks

sudo mkdir -p "$HOOK_DIR"

sudo tee "$HOOK_DIR/network" > /dev/null << EOF
#!/bin/bash
set -euo pipefail

NETWORK="\$1"
OPERATION="\$2"

[[ "\$NETWORK" == "default" && "\$OPERATION" == "started" ]] || exit 0

VM_IP=${VM_IP}

# DNAT: forward inbound ports to the VM
nft add table ip nat
nft add chain ip nat PREROUTING '{ type nat hook prerouting priority -100 ; }' 2>/dev/null || true
nft flush chain ip nat PREROUTING
nft add rule ip nat PREROUTING tcp dport 443  dnat to \${VM_IP}:443
nft add rule ip nat PREROUTING tcp dport 80   dnat to \${VM_IP}:80
nft add rule ip nat PREROUTING tcp dport 6443 dnat to \${VM_IP}:6443

# Allow forwarded traffic through libvirt's guest_input chain
if ! nft list chain ip libvirt_network guest_input 2>/dev/null | grep -q "6443"; then
    nft insert rule ip libvirt_network guest_input \
        oif "virbr0" ip daddr \${VM_IP} tcp dport '{80, 443, 6443}' accept
fi
EOF

sudo chmod +x "$HOOK_DIR/network"

# Apply immediately without waiting for next libvirt restart
sudo "$HOOK_DIR/network" default started
echo "Port forwarding hook installed and applied."
