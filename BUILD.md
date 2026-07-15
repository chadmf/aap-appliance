# AAP Appliance — repeatable build cookbook (libvirt / qemu-kvm)

> Prefer the short path? **[QUICKSTART.md](QUICKSTART.md)** (Linux/libvirt) or **[QUICKSTART-macos.md](QUICKSTART-macos.md)** (VirtualBox).

End-to-end runbook for building an appliance ISO and booting it under **virt-manager /
libvirt / qemu-kvm**. Product background lives in [README.md](README.md); this file is
the build + launch sequence that actually works.

Tested path: **AO-only live ISO** on the libvirt `default` NAT network (`192.168.122.0/24`)
with rootless Podman. Change `APPLIANCE_CONTENT` for AAP or combined builds.

## What you need

| Requirement | Notes |
|---|---|
| Disk | ≥ **200 GB free** in the output directory (OCP mirror + ISO). A successful live ISO is ~40 GB. |
| Host RAM/CPU for the VM | ≥ **32 GB** RAM and **8 vCPUs** for the guest (do not over-allocate vs the host) |
| Build tools | `podman`, `skopeo`, `opm`, `jq`, `python3` |
| Hypervisor | libvirt + qemu-kvm + virt-manager (`./scripts/libvirt-prereqs.sh`) |
| Pull secret (OpenShift) | From [console.redhat.com/openshift/install/pull-secret](https://console.redhat.com/openshift/install/pull-secret) |
| Pull secret (`quay.io/aap`) | Robot/token with pull on `quay.io/aap` (AO / AAP pre-release). **Not** in a stock OpenShift pull secret. |
| SSH public key | Mounted into the builder; becomes the `core` user key on the node |

Rootless Podman is fine if you build and run as the same user (`localhost/aap-appliance:latest`).

### Defaults used below

| Setting | Value |
|---|---|
| Libvirt network | `default` (`192.168.122.0/24`) |
| Rendezvous IP | `192.168.122.100` |
| VM MAC | `52:54:00:aa:bb:01` |
| VM name | `aap-appliance` |
| Content | `ao` (Automation Orchestrator + CloudNativePG) |

Libvirt’s DHCP **does** send gateway/DNS, so you do **not** set `GATEWAY` / `VM_MAC` at
build time (unlike VirtualBox). You **do** pin the MAC via a DHCP reservation so the
node always gets `RENDEZVOUS_IP`.

## Critical: two different auth files

Mixing these wrong is the #1 failure mode.

### Creating the `quay.io/aap` robot token JSON

AO / AAP pre-release images live under the **`aap` organization on [quay.io](https://quay.io)**.
A stock OpenShift pull secret does **not** include this. You need a Quay robot (or user token)
with **Read** on the repos you pull (at minimum the operator indexes under
`quay.io/aap/ansible-automation-platform/...`).

#### 1. Create or reuse a robot in the `aap` org

1. Sign in at [https://quay.io](https://quay.io) with an account that is a member of the **`aap`** org
   (ask your AAP team admin if you cannot see the org).
2. Open **aap** → **Robot Accounts** (or *Organization Settings → Robot accounts*).
3. **Create Robot Account** (e.g. `myname-pull`) **or** open an existing pull robot.
4. Under the robot, set repository permissions to **Read** for the images you need
   (org-wide read if your admin allows it; otherwise the specific
   `ansible-automation-platform` repositories).
5. Open the robot → **Credentials** / kebab menu → view the username + token.
   - Username looks like: `aap+myname-pull`
   - Password/token: long secret string (shown once — copy it)

You can also use **podman login** / **docker login** and then harvest the encoded entry
(see step 3).

#### 2. Encode `username:token` as base64

```bash
# Replace with your robot username and token
printf '%s' 'aap+myname-pull:THE_TOKEN_HERE' | base64 -w0
echo
```

On macOS, `base64` has no `-w0`; use:

```bash
printf '%s' 'aap+myname-pull:THE_TOKEN_HERE' | base64
```

#### 3. Write a small auth JSON file

Save as e.g. `~/aap-appliance-output/aap-only-auth.json`:

```json
{
  "auths": {
    "quay.io/aap": {
      "auth": "PASTE_BASE64_OUTPUT_HERE"
    }
  }
}
```

`auth` must be the base64 of `username:token` (not the raw token alone).

One-liner that writes the file:

```bash
mkdir -p ~/aap-appliance-output
read -r -p "Robot username (e.g. aap+myname-pull): " QUSER
read -r -s -p "Robot token: " QTOKEN; echo
export QUSER QTOKEN
python3 - <<'PY'
import json, base64, os
from pathlib import Path
auth = base64.b64encode(f'{os.environ["QUSER"]}:{os.environ["QTOKEN"]}'.encode()).decode()
path = Path.home() / "aap-appliance-output" / "aap-only-auth.json"
path.write_text(json.dumps({"auths": {"quay.io/aap": {"auth": auth}}}))
print("wrote", path)
PY
unset QUSER QTOKEN
```

**Alternative — login then copy:**

```bash
podman login quay.io/aap -u 'aap+myname-pull'
# enter token when prompted
# Then copy the quay.io/aap (or quay.io) entry from:
#   $XDG_RUNTIME_DIR/containers/auth.json
# or ~/.config/containers/auth.json
```

#### 4. Verify

```bash
skopeo inspect --authfile ~/aap-appliance-output/aap-only-auth.json \
  docker://quay.io/aap/ansible-automation-platform/automation-orchestrator-operator-index:main \
  >/dev/null && echo "AO index: ok"
```

If this fails with `unauthorized`, the robot lacks Read on that repo or the username/token is wrong.

Then merge this file with your OpenShift pull secret as in the next section (keep OpenShift’s
`quay.io` entry; only add `quay.io/aap`).

### 1. Merged pull secret — for `podman run` (appliance mirror)

Must include **both**:

- OpenShift entries (`cloud.openshift.com`, `quay.io`, `registry.redhat.io`, …) — for `quay.io/openshift-release-dev/ocp-v4.0-art-dev`
- `quay.io/aap` — for AO / AAP pre-release images

**Do not** overwrite the OpenShift `quay.io` entry with the AAP robot.

```bash
# Adjust paths to your OpenShift pull secret and AAP Quay auth JSON
python3 - <<'PY'
import json
from pathlib import Path

openshift = json.loads(Path.home().joinpath("Downloads/pull-secret.txt").read_text())
aap = json.loads(Path.home().joinpath("aap-appliance-output/aap-only-auth.json").read_text())

merged = {"auths": dict(openshift["auths"])}
merged["auths"]["quay.io/aap"] = aap["auths"]["quay.io/aap"]  # overlay ONLY this key

out = Path.home() / "aap-appliance-output" / "pull-secret-merged.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(merged))
print("wrote", out)
print("keys:", sorted(merged["auths"]))
PY
```

Sanity checks:

```bash
MERGED=~/aap-appliance-output/pull-secret-merged.json

skopeo inspect --authfile "$MERGED" \
  docker://quay.io/openshift-release-dev/ocp-release:4.22.0-x86_64 >/dev/null \
  && echo "OCP: ok"

skopeo inspect --authfile "$MERGED" \
  docker://quay.io/aap/ansible-automation-platform/automation-orchestrator-operator-index:main >/dev/null \
  && echo "AO: ok"
```

### 2. AAP-only authfile — for `./scripts/update-ao-images.sh`

`opm render` often prefers generic `quay.io` creds over `quay.io/aap`. Use a minimal
authfile that maps both hosts to the AAP robot, and install it where containers look:

```bash
python3 - <<'PY'
import json
from pathlib import Path

src = json.loads(Path.home().joinpath("aap-appliance-output/aap-only-auth.json").read_text())
cred = src["auths"].get("quay.io/aap") or src["auths"]["quay.io"]
out = {"auths": {"quay.io/aap": cred, "quay.io": cred}}
path = Path.home() / "aap-appliance-output" / "aap-only-auth.json"
path.write_text(json.dumps(out))
print("wrote", path)
PY

mkdir -p "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers"
cp ~/aap-appliance-output/aap-only-auth.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
```

After pin updates, restore the **merged** secret into containers auth:

```bash
cp ~/aap-appliance-output/pull-secret-merged.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
```

## One-time host setup (libvirt)

```bash
cd ~/git/aap-appliance
./scripts/libvirt-prereqs.sh
```

Confirm the default network is up:

```bash
sudo virsh net-list --all
sudo virsh net-start default 2>/dev/null || true
sudo virsh net-autostart default
```

## Repeatable build sequence

Run from the repo root.

### Step 0 — clean leftovers from a failed builder run

```bash
# oc-mirror leaves a listener; next run fails with:
#   [Executor] 55000 is already bound and cannot be used
fuser -k 55000/tcp 2>/dev/null || true
fuser -k 5005/tcp 2>/dev/null || true
podman ps -q --filter ancestor=localhost/aap-appliance:latest | xargs -r podman stop
```

Only wipe the mirror cache if a previous run was **killed mid-copy** and you see
`manifest.json: no such file or directory`:

```bash
rm -rf ./build/cache
```

### Step 1 — refresh image pins

`quay.io/operatorhubio/catalog` digests go stale quickly (`manifest unknown` after
nearly completing the mirror). Refresh before meaningful rebuilds:

```bash
# AAP GA pins (skip if AO-only)
./scripts/update-aap-images.sh --authfile ~/aap-appliance-output/pull-secret-merged.json

# AO + CloudNativePG (needs AAP-only authfile)
mkdir -p "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers"
cp ~/aap-appliance-output/aap-only-auth.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
./scripts/update-ao-images.sh --authfile ~/aap-appliance-output/aap-only-auth.json
cp ~/aap-appliance-output/pull-secret-merged.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"

git diff --stat
```

### Step 2 — bake pins into the builder image

```bash
podman build -t localhost/aap-appliance:latest .
```

Editing `config/` / `assets/` on disk does nothing until this rebuild.

### Step 3 — build the appliance ISO (libvirt / DHCP)

No `GATEWAY` / `VM_MAC` env vars — libvirt DHCP handles gateway and DNS. The MAC is
enforced later via DHCP reservation + `launch-appliance.sh`.

```bash
mkdir -p ./build

podman run --rm --privileged --net=host \
  -e BASE_DOMAIN=example.com \
  -e RENDEZVOUS_IP=192.168.122.100 \
  -e MACHINE_NETWORK=192.168.122.0/24 \
  -e APPLIANCE_CONTENT=ao \
  -e DISCONNECTED=true \
  -v "$HOME/aap-appliance-output/pull-secret-merged.json:/run/secrets/pull-secret:Z" \
  -v "$HOME/.ssh/id_rsa.pub:/run/secrets/ssh-key:Z" \
  -v "$(pwd)/build:/assets:Z" \
  localhost/aap-appliance:latest
```

Cold cache: **30–90+ minutes**. Warm cache after a near-success: ~10 minutes.

### Step 4 — verify outputs

```text
build/appliance.iso                         # ~40 GB live ISO
build/cluster-config/agentconfig.noarch.iso # second CD-ROM
build/cluster-config/auth/kubeconfig
build/cluster-config/auth/kubeadmin-password
```

## Launch under virt-manager / libvirt

### Reserve the rendezvous IP

MAC and IP must match what you pass to `launch-appliance.sh`:

```bash
./scripts/dhcp-reserve.sh --mac 52:54:00:aa:bb:01 --ip 192.168.122.100
```

### Create and start the VM

`launch-appliance.sh` copies ISOs into `/var/lib/libvirt/images/` and runs `virt-install`
(32 GB RAM / 8 vCPUs by default). The domain then shows up in **virt-manager**.

```bash
VM_MAC=52:54:00:aa:bb:01 \
RENDEZVOUS_IP=192.168.122.100 \
./scripts/launch-appliance.sh --output-dir ./build --replace
```

Equivalent explicit flags:

```bash
./scripts/launch-appliance.sh \
  --output-dir ./build \
  --mac 52:54:00:aa:bb:01 \
  --rendezvous-ip 192.168.122.100 \
  --replace
```

Open the console in virt-manager (`aap-appliance`) or:

```bash
sudo virt-viewer aap-appliance
```

### Optional: reach the API/console from the host

```bash
RENDEZVOUS_IP=192.168.122.100 ./scripts/setup-port-forwarding.sh
# then https://localhost / API on localhost:6443
```

### Monitor install

```bash
SSH_OPTS='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
ssh $SSH_OPTS core@192.168.122.100 sudo journalctl -fu assisted-service
```

When installed:

```bash
export KUBECONFIG=$PWD/build/cluster-config/auth/kubeconfig
oc get nodes
oc get automationorchestrator -n automation-orchestrator -w   # APPLIANCE_CONTENT=ao
```

Add API/console names to `/etc/hosts` using the same `BASE_DOMAIN` as the build (script prompt or `-e BASE_DOMAIN=…`):

```bash
BASE_DOMAIN=nip.io            # must match the value used when building the ISO
RENDEZVOUS_IP=192.168.122.100
CLUSTER_NAME=appliance

sudo tee -a /etc/hosts >/dev/null <<EOF
${RENDEZVOUS_IP} api.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} oauth-openshift.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} downloads-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
EOF
```

Then open `https://console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}` (kubeadmin password in `build/cluster-config/auth/kubeadmin-password`).

### Cleanup

```bash
sudo virsh destroy aap-appliance
sudo virsh undefine aap-appliance --remove-all-storage
sudo rm -f /var/lib/libvirt/images/aap-appliance.iso \
           /var/lib/libvirt/images/agentconfig.noarch.iso \
           /var/lib/libvirt/images/aap-appliance.raw
```

## Copy-paste checklist

```bash
cd ~/git/aap-appliance

# One-time (if needed)
./scripts/libvirt-prereqs.sh

# 0. builder leftovers
fuser -k 55000/tcp 2>/dev/null || true

# 1. pins (AO)
mkdir -p "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers"
cp ~/aap-appliance-output/aap-only-auth.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"
./scripts/update-ao-images.sh --authfile ~/aap-appliance-output/aap-only-auth.json
cp ~/aap-appliance-output/pull-secret-merged.json \
  "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers/auth.json"

# 2. bake image
podman build -t localhost/aap-appliance:latest .

# 3. build ISO (libvirt defaults — no GATEWAY/VM_MAC)
mkdir -p ./build
podman run --rm --privileged --net=host \
  -e BASE_DOMAIN=example.com \
  -e RENDEZVOUS_IP=192.168.122.100 \
  -e MACHINE_NETWORK=192.168.122.0/24 \
  -e APPLIANCE_CONTENT=ao \
  -e DISCONNECTED=true \
  -v "$HOME/aap-appliance-output/pull-secret-merged.json:/run/secrets/pull-secret:Z" \
  -v "$HOME/.ssh/id_rsa.pub:/run/secrets/ssh-key:Z" \
  -v "$(pwd)/build:/assets:Z" \
  localhost/aap-appliance:latest

# 4. boot under libvirt / virt-manager
./scripts/dhcp-reserve.sh --mac 52:54:00:aa:bb:01 --ip 192.168.122.100
VM_MAC=52:54:00:aa:bb:01 RENDEZVOUS_IP=192.168.122.100 \
  ./scripts/launch-appliance.sh --output-dir ./build --replace
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unauthorized` on AO index during `update-ao-images.sh` while `skopeo` works | `opm` using OpenShift `quay.io` creds | AAP-only authfile + `$XDG_RUNTIME_DIR/containers/auth.json` |
| `unauthorized` on `ocp-v4.0-art-dev` during `podman run` | Merged secret overwrote OpenShift `quay.io` | Keep OpenShift `quay.io`; only add `quay.io/aap` |
| `manifest unknown` for `operatorhubio/catalog@sha256:…` | Stale catalog digest | Re-run `update-ao-images.sh`, rebuild container image, retry |
| `55000 is already bound` | Leftover oc-mirror | `fuser -k 55000/tcp` |
| `manifest.json: no such file` under `build/cache` | Interrupted registry copy | `rm -rf ./build/cache` and retry |
| Mirror `context deadline exceeded` / `unexpected EOF` | Transient Quay/CDN errors | Retry same `podman run` (warm cache) |
| VM gets wrong IP / install hangs on networking | MAC ≠ DHCP reservation | Same MAC in `dhcp-reserve.sh` and `launch-appliance.sh` |
| `VM 'aap-appliance' already exists` | Prior domain left behind | Add `--replace` to `launch-appliance.sh` |
| Guest OOM / install dies mid-way | Too little RAM or host overcommit | ≥ 32 GB guest RAM; leave headroom on the host |
| Can’t open console | Domain not started / no GUI | `sudo virsh start aap-appliance`; `virt-manager` or `sudo virt-viewer aap-appliance` |

## Why `APPLIANCE_CONTENT=ao` still mirrors some AAP images

The builder caches a broad image set so one appliance ISO can pair with different
`APPLIANCE_CONTENT` values later. Seeing `ansible-automation-platform-27/...` during an
`ao` one-shot build is expected. What gets installed is still selected by
`APPLIANCE_CONTENT` in the agentconfig / one-shot phase.
