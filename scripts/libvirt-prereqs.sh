#!/bin/bash
set -euo pipefail

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
