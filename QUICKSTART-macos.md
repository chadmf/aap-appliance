# Quickstart — macOS (VirtualBox)

Build (or obtain) an AAP appliance live ISO and boot it on a Mac with **VirtualBox**.

**Linux / libvirt:** see [QUICKSTART.md](QUICKSTART.md).

For deep detail and troubleshooting, see [BUILD.md](BUILD.md).

## Reality check

| Topic | Guidance |
|---|---|
| Where to **build** the ISO | Prefer a **Linux x86_64** host (or remote VM) with ≥200 GB free. The builder needs privileged Podman and pulls a full OCP release. |
| Apple Silicon (M1/M2/M3/…) | The default appliance is **x86_64**. Building/running on ARM Macs is painful; build on Linux x86_64, then copy ISOs to the Mac for VirtualBox (x86_64 VirtualBox on Apple Silicon uses emulation and is very slow — a remote Linux VM is usually better for *running* too). |
| Where to **run** the VM | This quickstart uses VirtualBox on macOS with a **host-only** network and **static IP** (VirtualBox DHCP does not hand out gateway/DNS). |
| Guest size | ≥ **32 GB** RAM, **8** vCPUs, ~**200 GB** disk for the guest |

## Before you start

### On the Mac (launch)

| Need | Notes |
|---|---|
| [VirtualBox](https://www.virtualbox.org/) | Host-only networking required |
| `VBoxManage` on `PATH` | Ships with VirtualBox |
| This repo (for launch scripts) | `git clone` or copy `scripts/launch-appliance-vbox.sh` |

### On the Linux builder (ISO build)

| Need | Notes |
|---|---|
| Disk | ≥ **200 GB** free (ISO ends up ~40 GB) |
| Tools | `podman`, `skopeo`, `opm`, `jq`, `python3` |
| OpenShift pull secret | [console.redhat.com/openshift/install/pull-secret](https://console.redhat.com/openshift/install/pull-secret) |
| Quay `aap` robot | Username + token — see below. The build script will ask for these if missing. |
| SSH public key | e.g. `~/.ssh/id_rsa.pub` (baked into the appliance at build time) |

## Quay robot: what `aap+myname-pull` is and how to get it

AO / pre-release images live under the private **`aap` organization on [quay.io](https://quay.io)**. A normal OpenShift pull secret does **not** cover that. You need a **Quay robot account**.

The username looks like `aap+myname-pull`:

| Part | Meaning |
|---|---|
| `aap` | The Quay **organization** |
| `+` | Quay’s separator between org and robot name |
| `yourusername-pull` | The **robot name** you (or an admin) chose — any name is fine (`ci-pull`, `yourusername-laptop`, …) |

So the full username is always `{organization}+{robot-name}`. The **token** is the robot’s password.

This is **not** your personal Quay login and **not** the OpenShift pull secret from console.redhat.com.

### How to get one

1. Sign in at [https://quay.io](https://quay.io).
2. You must be a member of the **`aap`** org. If you don’t see it, ask an AAP/Quay admin to add you or share a pull robot.
3. Open **aap → Robot Accounts**.
4. **Create Robot Account** (e.g. name `yourusername-pull`) **or** use an existing pull robot you’re allowed to use.
5. Grant the robot **Read** on the repos you need (at least under `ansible-automation-platform`, including the AO operator index).
6. Open the robot → credentials and copy:
   - **Username:** e.g. `aap+yourusername-pull`
   - **Token:** the secret password string

When `build-libvirt.sh` asks for robot username and token (on the Linux builder), paste those values. The script writes the auth JSON for you.

## Path A — recommended: build on Linux, launch on Mac

### 1. Prepare auth, pins, and builder image (Linux)

On the Linux x86_64 host:

```bash
git clone https://github.com/automation-nexus/aap-appliance.git   # or your fork
cd aap-appliance

# Auth merge + pin refresh + image bake only (no libvirt-networked ISO)
./scripts/build-libvirt.sh --skip-appliance --skip-launch
```

The script prompts for:

1. **OpenShift pull-secret path** — skipped if it already finds one (e.g. `~/Downloads/pull-secret.txt`, `~/.aap-demo/pull-secret.txt`, or `~/aap-appliance-output/pull-secret-merged.json`)  
2. **Quay robot username/token** — skipped if `~/aap-appliance-output/aap-only-auth.json` (or a merged secret with `quay.io/aap`) already exists; otherwise asked, then written to that path  
3. **SSH public key** — skipped if `~/.ssh/id_ed25519.pub` or `id_rsa.pub` exists  
4. **Base domain** and other build defaults (used later for the VirtualBox `podman run`)

It merges auth (keeps OpenShift `quay.io`, adds `quay.io/aap`), refreshes pins, and builds `localhost/aap-appliance:latest`.

### 2. Build the appliance ISO with VirtualBox networking (Linux)

`build-libvirt.sh` defaults to the libvirt network (`192.168.122.0/24`) and does **not** bake VirtualBox static networking. For Mac/VirtualBox you must pass `GATEWAY` + `VM_MAC`:

```bash
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

`GATEWAY` + `VM_MAC` are **required** for VirtualBox. Use the same `VM_MAC` / `RENDEZVOUS_IP` when launching on the Mac.

Cold builds take a long time (often 30–90+ minutes). Warm retries are much faster.

### 3. Copy artifacts to the Mac

```bash
# From the Mac (adjust host/path)
mkdir -p ~/aap-appliance-build/cluster-config
scp 'user@linux-builder:~/git/aap-appliance/build/appliance.iso' ~/aap-appliance-build/
scp 'user@linux-builder:~/git/aap-appliance/build/cluster-config/agentconfig.noarch.iso' \
  ~/aap-appliance-build/cluster-config/
# Optional but useful after install:
scp -r 'user@linux-builder:~/git/aap-appliance/build/cluster-config/auth' \
  ~/aap-appliance-build/cluster-config/
```

Layout:

```text
~/aap-appliance-build/
  appliance.iso
  cluster-config/
    agentconfig.noarch.iso
    auth/   (optional until install finishes)
```

### 4. Launch with VirtualBox on the Mac

```bash
cd ~/git/aap-appliance   # repo with scripts/

VM_MAC=52:54:00:aa:bb:01 \
RENDEZVOUS_IP=192.168.56.100 \
./scripts/launch-appliance-vbox.sh --output-dir ~/aap-appliance-build --replace
```

Or with explicit ISO paths:

```bash
VM_MAC=52:54:00:aa:bb:01 \
RENDEZVOUS_IP=192.168.56.100 \
./scripts/launch-appliance-vbox.sh \
  --appliance-iso ~/aap-appliance-build/appliance.iso \
  --agentconfig-iso ~/aap-appliance-build/cluster-config/agentconfig.noarch.iso \
  --replace
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

**Node SSH (RHCOS `core` user)** — use the private key that matches the public key baked in at build time (no password). Disable host-key checks so recreated VMs do not trip `KNOWN_HOSTS` errors:

```bash
SSH_OPTS='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'

ssh $SSH_OPTS core@192.168.56.100
# or, if needed:
ssh $SSH_OPTS -i ~/.ssh/id_rsa core@192.168.56.100
```

Open the VM window in the VirtualBox UI for the graphical console.

Install progress:

```bash
ssh $SSH_OPTS core@192.168.56.100 sudo journalctl -fu assisted-service
```

**After OCP is installed — access the OpenShift cluster with kubeconfig:**

Copy `cluster-config/auth/` from the Linux builder if you have not already, then:

```bash
export KUBECONFIG="$HOME/aap-appliance-build/cluster-config/auth/kubeconfig"

# Confirm API access
oc whoami
oc get nodes
oc get clusteroperators
```

Optional — persist for later:

```bash
mkdir -p ~/.kube
cp "$HOME/aap-appliance-build/cluster-config/auth/kubeconfig" ~/.kube/config
```

Web console (user `kubeadmin`):

```bash
cat ~/aap-appliance-build/cluster-config/auth/kubeadmin-password
```

**`/etc/hosts` — required for name resolution from your Mac**

Use the same **base domain** you set with `-e BASE_DOMAIN=…` at build time. Unquoted `<<EOF` expands the variables:

```bash
# Same BASE_DOMAIN as the podman build (also in cluster-config/install-config.yaml)
BASE_DOMAIN=nip.io
RENDEZVOUS_IP=192.168.56.100
CLUSTER_NAME=appliance

sudo tee -a /etc/hosts >/dev/null <<EOF
${RENDEZVOUS_IP} api.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} oauth-openshift.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} downloads-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
EOF
```

Then open `https://console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}`.

Your Mac must also be able to reach the host-only network (`192.168.56.0/24`). (`*.apps` wildcards are not supported in `/etc/hosts`; add more app routes the same way if needed.)

Outputs (on the Linux builder, then copied to the Mac):

```text
build/appliance.iso
build/cluster-config/agentconfig.noarch.iso
build/cluster-config/auth/kubeconfig
build/cluster-config/auth/kubeadmin-password
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
| `unauthorized` on AO index during pin update | Re-run the Linux script and enter a valid `aap` org robot username/token when prompted |
| `unauthorized` on OCP release images | Do not overwrite OpenShift `quay.io` with the AAP robot — the script keeps both |
| `manifest unknown` for `operatorhubio/catalog` | On Linux: `./scripts/build-libvirt.sh --update-pins-only`, rebuild image, then re-run the VirtualBox `podman run` |
| `55000 is already bound` | Leftover oc-mirror on the builder; `fuser -k 55000/tcp` |
| No default route / OVN MTU probe fails | Rebuild with `GATEWAY` + `VM_MAC`; VirtualBox DHCP does not send gateway/DNS |
| VM MAC ≠ build `VM_MAC` | Same MAC in `podman run` and `launch-appliance-vbox.sh` |
| Slow / unusable on Apple Silicon | Prefer Linux x86_64 for build **and** run when possible |
| Not enough memory | Guest needs ≥32 GB; leave headroom for macOS |

## Next reading

- [QUICKSTART.md](QUICKSTART.md) — Linux / libvirt one-shot  
- [BUILD.md](BUILD.md) — auth merge, pins, troubleshooting  
- [README.md](README.md) — VirtualBox notes, monitoring, cleanup  
