#!/bin/bash
set -euo pipefail

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
