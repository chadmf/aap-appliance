# Quickstart — macOS (VirtualBox)

Build (or obtain) an AAP appliance live ISO and boot it on a Mac with **VirtualBox**.

Linux/libvirt users: see [QUICKSTART.md](QUICKSTART.md) instead.

## Reality check

| Topic | Guidance |
|---|---|
| Where to **build** the ISO | Prefer a **Linux x86_64** host (or remote VM) with ≥200 GB free. The builder needs privileged Podman and pulls a full OCP release. |
| Apple Silicon (M1/M2/M3/…) | The default appliance is **x86_64**. Building/running on ARM Macs is painful; build on Linux x86_64, then copy ISOs to the Mac for VirtualBox (x86_64 VirtualBox on Apple Silicon uses emulation and is very slow — a remote Linux VM is usually better for *running* too). |
| Where to **run** the VM | This quickstart uses VirtualBox on macOS with a **host-only** network and **static IP** (VirtualBox DHCP does not hand out gateway/DNS). |
| Guest size | ≥ **32 GB** RAM, **8** vCPUs, ~**200 GB** disk for the guest |

## What you need on the Mac

| Need | Notes |
|---|---|
| [VirtualBox](https://www.virtualbox.org/) | With extension pack if you use USB/etc.; host-only networking is required |
| `VBoxManage` on `PATH` | Ships with VirtualBox |
| OpenShift pull secret | Only needed if you build on this machine |
| `quay.io/aap` auth | Prompted by the Linux build script (robot username/token), or see [QUICKSTART.md](QUICKSTART.md) |
| SSH public key | Baked into the appliance at build time |

Clone the repo (or copy built ISOs onto the Mac):

```bash
git clone https://github.com/automation-nexus/aap-appliance.git
cd aap-appliance
```

## Path A — recommended: build on Linux, launch on Mac

### 1. Build on a Linux x86_64 host

On the Linux builder (see [QUICKSTART.md](QUICKSTART.md)):

```bash
./scripts/build-libvirt.sh --skip-launch
```

**Important for VirtualBox:** that script targets libvirt (`192.168.122.0/24`) and does **not** bake static VirtualBox networking. For a Mac/VirtualBox boot, build with host-only static params instead:

```bash
# On Linux builder — after auth merge / image bake (or use the manual commands in BUILD.md)
mkdir -p ./build

podman run --rm --privileged --net=host \
  -e BASE_DOMAIN=example.com \
  -e RENDEZVOUS_IP=192.168.56.100 \
  -e MACHINE_NETWORK=192.168.56.0/24 \
  -e GATEWAY=192.168.56.1 \
  -e VM_MAC=52:54:00:aa:bb:01 \
  -e APPLIANCE_CONTENT=ao \
  -e DISCONNECTED=true \
  -v "$HOME/aap-appliance-output/pull-secret-merged.json:/run/secrets/pull-secret:Z" \
  -v "$HOME/.ssh/id_rsa.pub:/run/secrets/ssh-key:Z" \
  -v "$(pwd)/build:/assets:Z" \
  localhost/aap-appliance:latest
```

`GATEWAY` + `VM_MAC` are **required** for VirtualBox. Use the same `VM_MAC` / `RENDEZVOUS_IP` when launching.

### 2. Copy artifacts to the Mac

```bash
# From the Mac (adjust host/path)
mkdir -p ~/aap-appliance-build
scp 'user@linux-builder:~/git/aap-appliance/build/appliance.iso' ~/aap-appliance-build/
scp 'user@linux-builder:~/git/aap-appliance/build/cluster-config/agentconfig.noarch.iso' \
  ~/aap-appliance-build/
# Optional but useful after install:
scp -r 'user@linux-builder:~/git/aap-appliance/build/cluster-config/auth' ~/aap-appliance-build/
```

Layout expected by the launch script:

```text
~/aap-appliance-build/
  appliance.iso
  cluster-config/
    agentconfig.noarch.iso
    auth/   (optional until install finishes)
```

If you only have flat files, pass explicit paths (see below).

### 3. Launch with VirtualBox on the Mac

```bash
cd ~/git/aap-appliance   # repo with scripts/

VM_MAC=52:54:00:aa:bb:01 \
RENDEZVOUS_IP=192.168.56.100 \
./scripts/launch-appliance-vbox.sh \
  --appliance-iso ~/aap-appliance-build/appliance.iso \
  --agentconfig-iso ~/aap-appliance-build/agentconfig.noarch.iso \
  --replace
```

Or if you recreated the `build/` + `cluster-config/` layout:

```bash
VM_MAC=52:54:00:aa:bb:01 \
RENDEZVOUS_IP=192.168.56.100 \
./scripts/launch-appliance-vbox.sh --output-dir ~/aap-appliance-build --replace
```

This creates `vboxnet0` (host `192.168.56.1`), attaches both ISOs, and starts the VM headless.

Open the VirtualBox UI for the console, or:

```bash
VBoxManage startvm aap-appliance --type separate
```

## Path B — import a finished OVA

If someone already installed the appliance and exported an OVA:

```bash
RENDEZVOUS_IP=192.168.56.100 \
./scripts/import-appliance-vbox.sh --ova ~/Downloads/appliance.ova --replace
```

No MAC pinning needed on import — networking was resolved during the original install.

## After it starts

```bash
# Install progress (once SSH is up)
ssh core@192.168.56.100 sudo journalctl -fu assisted-service

# When installed (kubeconfig from the build host / copied auth dir)
export KUBECONFIG=~/aap-appliance-build/auth/kubeconfig
# or: .../cluster-config/auth/kubeconfig
oc get nodes
```

## Defaults (VirtualBox)

| Setting | Value |
|---|---|
| Host-only net | `192.168.56.0/24` |
| Host gateway | `192.168.56.1` (`vboxnet0`) |
| Rendezvous IP | `192.168.56.100` |
| VM MAC | `52:54:00:aa:bb:01` (must match build-time `VM_MAC`) |
| Memory / CPUs | 32768 MB / 8 |

## Common failures

| Error | Fix |
|---|---|
| No default route / OVN MTU probe fails | Rebuild with `GATEWAY` + `VM_MAC`; VirtualBox DHCP does not send gateway/DNS |
| VM MAC ≠ build `VM_MAC` | Same MAC in `podman run` and `launch-appliance-vbox.sh` |
| `unauthorized` during build | Merged pull secret must keep OpenShift `quay.io` **and** add `quay.io/aap` — see [BUILD.md](BUILD.md) |
| Slow / unusable on Apple Silicon | Expect emulation cost; prefer Linux x86_64 for build **and** run when possible |
| Not enough memory | Guest needs ≥32 GB; leave headroom for macOS |

## Next reading

- [QUICKSTART.md](QUICKSTART.md) — Linux / libvirt one-shot  
- [BUILD.md](BUILD.md) — auth merge, pins, troubleshooting  
- [README.md](README.md) — VirtualBox notes, monitoring, cleanup  
