# Quickstart — build & boot on libvirt

Build an AAP appliance live ISO and launch it under **virt-manager / qemu-kvm** with one script.

**macOS / VirtualBox:** see [QUICKSTART-macos.md](QUICKSTART-macos.md).

For deep detail and troubleshooting, see [BUILD.md](BUILD.md).

## Before you start


| Need                  | Notes                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------ |
| Disk                  | ≥ **200 GB** free (ISO ends up ~40 GB)                                                                       |
| Guest size            | ≥ **32 GB** RAM, **8** vCPUs for the VM                                                                      |
| Tools                 | `podman`, `skopeo`, `opm`, `jq`, `python3`                                                                   |
| OpenShift pull secret | [console.redhat.com/openshift/install/pull-secret](https://console.redhat.com/openshift/install/pull-secret) |
| Quay `aap` robot      | Username + token — see below. The script will ask for these.                                                 |
| SSH public key        | e.g. `~/.ssh/id_rsa.pub`                                                                                     |


One-time hypervisor setup (if libvirt is not already working):

```bash
./scripts/libvirt-prereqs.sh
```



## Quay robot: what `aap+myname-pull` is and how to get it

AO / pre-release images live under the private `aap` **organization on [quay.io](https://quay.io)**. A normal OpenShift pull secret does **not** cover that. You need a **Quay robot account**.

The username looks like `aap+myname-pull`:


| Part                | Meaning                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------- |
| `aap`               | The Quay **organization**                                                                           |
| `+`                 | Quay’s separator between org and robot name                                                         |
| `yourusername-pull` | The **robot name** you (or an admin) chose — any name is fine (`ci-pull`, `yourusername-laptop`, …) |


So the full username is always `{organization}+{robot-name}`. The **token** is the robot’s password.

This is **not** your personal Quay login and **not** the OpenShift pull secret from console.redhat.com.

### How to get one

1. Sign in at [https://quay.io](https://quay.io).
2. You must be a member of the `aap` org. If you don’t see it, ask an AAP/Quay admin to add you or share a pull robot.
3. Open **aap → Robot Accounts**.
4. **Create Robot Account** (e.g. name `yourusername-pull`) **or** use an existing pull robot you’re allowed to use.
5. Grant the robot **Read** on the repos you need (at least under `ansible-automation-platform`, including the AO operator index).
6. Open the robot → credentials and copy:
  - **Username:** e.g. `aap+yourusername-pull`
  - **Token:** the secret password string

When `build-libvirt.sh` asks for robot username and token, paste those values. The script writes the auth JSON for you.

## Build and launch

```bash
./scripts/build-libvirt.sh
```

The script prompts for:

1. **OpenShift pull-secret path** — skipped if it already finds one (e.g. `~/Downloads/pull-secret.txt`, `~/.aap-demo/pull-secret.txt`, or `~/aap-appliance-output/pull-secret-merged.json`)
2. **Quay robot username/token** — skipped if `~/aap-appliance-output/aap-only-auth.json` (or a merged secret with `quay.io/aap`) already exists; otherwise asked, then written to that path
3. **SSH public key** — skipped if `~/.ssh/id_ed25519.pub` or `id_rsa.pub` exists
4. **Base domain** (e.g. `example.com`)
5. Optional defaults: content type, rendezvous IP, MAC, whether to refresh pins / launch the VM

Then it merges auth (keeps OpenShift `quay.io`, adds `quay.io/aap`), refreshes image pins, builds the builder image, builds the appliance ISO, and (by default) reserves DHCP + starts the libvirt VM.

Defaults (press Enter to accept):


| Setting         | Default             |
| --------------- | ------------------- |
| Content         | `ao`                |
| Rendezvous IP   | `192.168.122.100`   |
| Machine network | `192.168.122.0/24`  |
| VM MAC          | `52:54:00:aa:bb:01` |
| Output          | `./build`           |


Cold builds take a long time (often 30–90+ minutes). Warm retries are much faster.

Useful flags: `--skip-launch`, `--launch-only`, `--update-pins-only`, `--skip-pins`.

## After it starts

**Node SSH (RHCOS `core` user)** — use the private key that matches the public key you gave the builder (no password). Disable host-key checks so recreated VMs do not trip `KNOWN_HOSTS` errors:

```bash
SSH_OPTS='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'

ssh $SSH_OPTS core@192.168.122.100
# or, if needed:
ssh $SSH_OPTS -i ~/.ssh/id_rsa core@192.168.122.100
```

Graphical console (libvirt):

```bash
virt-manager
# or
sudo virt-viewer aap-appliance
```

Install progress:

```bash
ssh $SSH_OPTS core@192.168.122.100 sudo journalctl -fu assisted-service
```

**After OCP is installed — access the OpenShift cluster with kubeconfig:**

The builder writes a kubeconfig next to the ISOs. Point `oc` / `kubectl` at it:

```bash
# From the repo on the build host
export KUBECONFIG="$(pwd)/build/cluster-config/auth/kubeconfig"

# Confirm API access
oc whoami
oc get nodes
oc get clusteroperators
```

Optional — use it as your default kubeconfig for this shell session only (preferred), or copy it:

```bash
# Session-only (recommended)
export KUBECONFIG="$(pwd)/build/cluster-config/auth/kubeconfig"

# Or merge/copy for later use (overwrites ~/.kube/config if you use cp alone)
mkdir -p ~/.kube
cp "$(pwd)/build/cluster-config/auth/kubeconfig" ~/.kube/config
```

Web console (user `kubeadmin`):

```bash
cat build/cluster-config/auth/kubeadmin-password
```

**`/etc/hosts` — required for name resolution from your laptop**

Use the same **base domain** you entered when `build-libvirt.sh` prompted (also printed in the script’s post-launch `/etc/hosts` block). Unquoted `<<EOF` expands the variables:

```bash
# Same value as the "Cluster base domain" prompt (also in build/cluster-config/install-config.yaml)
BASE_DOMAIN=nip.io
RENDEZVOUS_IP=192.168.122.100
CLUSTER_NAME=appliance

sudo tee -a /etc/hosts >/dev/null <<EOF
${RENDEZVOUS_IP} api.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} oauth-openshift.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
${RENDEZVOUS_IP} downloads-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}
EOF
```

Then open `https://console-openshift-console.apps.${CLUSTER_NAME}.${BASE_DOMAIN}` (e.g. `…apps.appliance.nip.io`).

(`*.apps` wildcards are not supported in `/etc/hosts`; add more app routes the same way if needed.)

If the API is not reachable from your laptop, set up port forwards (ports 80/443/6443):

```bash
RENDEZVOUS_IP=192.168.122.100 ./scripts/setup-port-forwarding.sh
```

Outputs:

```text
build/appliance.iso
build/cluster-config/agentconfig.noarch.iso
build/cluster-config/auth/kubeconfig
build/cluster-config/auth/kubeadmin-password
```

## Common failures


| Error                                          | Fix                                                                                                   |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `unauthorized` on AO index during pin update   | Re-run and enter a valid `aap` org robot username/token when prompted                                 |
| `unauthorized` on OCP release images           | Do not overwrite OpenShift `quay.io` with the AAP robot — the script keeps both                       |
| `manifest unknown` for `operatorhubio/catalog` | Re-run with pin refresh (default), or `./scripts/build-libvirt.sh --update-pins-only` then full build |
| `55000 is already bound`                       | Leftover oc-mirror; script clears this, or `fuser -k 55000/tcp`                                       |
| Wrong VM IP                                    | Same MAC in DHCP reserve and launch (`52:54:00:aa:bb:01` by default)                                  |




## Next reading

- [BUILD.md](BUILD.md) — full cookbook  
- [README.md](README.md) — products, parameters, monitoring

