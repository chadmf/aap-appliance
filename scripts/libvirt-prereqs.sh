#!/bin/bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: libvirt-prereqs.sh [--help]

Install libvirt prerequisites and start the virtualisation daemon if they are
not already present. Safe to run multiple times (idempotent).

Installs: qemu-kvm, libvirt, libvirt-client, virt-install (via dnf if missing)
Enables:  libvirtd (or virtqemud on newer Fedora/RHEL) and the default network

No arguments or environment variables.
EOF
    exit 0
fi

if ! command -v virsh &>/dev/null || ! command -v virt-install &>/dev/null; then
    sudo dnf install -y qemu-kvm libvirt libvirt-client virt-install
fi

if systemctl list-unit-files libvirtd.service &>/dev/null; then
    sudo systemctl enable --now libvirtd
else
    sudo systemctl enable --now virtqemud
fi

sudo virsh net-start default 2>/dev/null || true
sudo virsh net-autostart default

echo "libvirt ready."
