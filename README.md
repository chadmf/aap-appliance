# AAP Appliance Builder

Builds an OpenShift Single Node (SNO) appliance disk image with Red Hat automation products pre-baked. When the image is cloned to hardware or a VM and booted, it installs OCP and deploys the selected products fully offline — no internet access required at install time.

Supported products (controlled by `APPLIANCE_CONTENT`):
- **`aap`** (default) — Ansible Automation Platform 2.7
- **`ao`** — Automation Orchestrator + CloudNativePG
- **`aap-ao`** — both products together

The image is built on top of the [openshift-appliance](https://github.com/openshift/appliance) tool. This repo bakes in the static OCP and product manifests so the only inputs you need to provide at build time are your site-specific parameters.

## Prerequisites

- `podman`
- `sudo` (required for the privileged build container)
- At least 200 GB of free disk space in the output directory (the builder downloads OCP release artifacts)
- A Red Hat pull secret — download from [console.redhat.com/openshift/install/pull-secret](https://console.redhat.com/openshift/install/pull-secret)

## What gets built

| Output | Description |
|---|---|
| `appliance.raw` | Disk image — clone to hardware or a VM disk (default) |
| `appliance.iso` | Live ISO — boot directly, installs OCP onto a separate disk (`APPLIANCE_FORMAT=live-iso`) |
| `cluster-config/agentconfig.noarch.iso` | Config ISO — attach as a CD-ROM; carries the cluster identity and rendezvous IP |
| `cluster-config/auth/kubeconfig` | Cluster kubeconfig for use after installation |
| `cluster-config/auth/kubeadmin-password` | Initial admin password |

The built image includes (depending on `APPLIANCE_CONTENT`):
- OCP SNO
- **AAP** (`aap`, `aap-ao`): AAP operator 2.7 installed via the certified-operators catalog
- **AO** (`ao`, `aap-ao`): Automation Orchestrator operator (pre-release) + CloudNativePG operator
- Rancher local-path-provisioner as the default StorageClass (hostPath-backed, supports RWX)
- All required images pre-cached in the appliance local registry — no external pulls during install

## Updating image pins

Image digests are pinned in `config/` and managed by update scripts. Run the appropriate script(s) before each rebuild to pick up the latest digests.

### AAP images

```bash
# Released (default) — pulls from registry.redhat.io/redhat/redhat-operator-index:v4.22
./scripts/update-aap-images.sh --authfile /path/to/pull-secret.json

# Pre-release — pulls from quay.io/aap/ansible-automation-platform/operator-index:2.7-next
./scripts/update-aap-images.sh --authfile /path/to/pull-secret.json --prerelease
```

The script resolves the current operator index digest, finds the `stable-2.7` channel head, extracts all `relatedImages`, and rewrites `config/aap-images*.yaml` and the CatalogSource digest + `startingCSV` in `assets/openshift/aap*.yaml`.

### AO images

```bash
./scripts/update-ao-images.sh --authfile /path/to/pull-secret.json
```

The script:
1. Resolves the AO operator index (`quay.io/aap/.../automation-orchestrator-operator-index:main`) digest
2. Finds the AO channel head and extracts `relatedImages`
3. Resolves the CloudNativePG bundle from `quay.io/operatorhubio/catalog` (OperatorHub community catalog — the Red Hat OCI indexes do not carry `cloudnative-pg` for OCP 4.22)
4. Writes the merged image list to `config/ao-images-prerelease.yaml`
5. Updates the AO and `redhat-operator-index` CatalogSource digests, AO `startingCSV`, and channel names in `assets/openshift/ao-prerelease.yaml`

For `APPLIANCE_CONTENT=aap-ao` builds, run **both** update scripts to keep all digests current. The `redhat-operator-index` digest appears in both `aap.yaml` and `ao-prerelease.yaml`; the scripts keep them in sync independently.

Review all changes with `git diff` before building.

> [!IMPORTANT]
> The pull secret must have access to `quay.io/aap` for pre-release indexes. If your standard Red Hat pull secret doesn't cover it, merge in a `quay.io/aap`-scoped credential:
>
> ```json
> {
>   "quay.io/aap": {
>     "auth": "foobar"
>   },
>   "quay.io": {
>     "auth": "barbaz",
>     "email": "someone@redhat.com"
>   }
> }
> ```

## Build

```bash
sudo podman build -t aap-appliance:latest .
```

This step bakes the static manifests into the image. It only needs to be done once per version. `sudo` is required so the image lands in root's store, where the privileged `podman run` can find it.

## Run

```bash
sudo podman run --rm --privileged --net=host \
  -e BASE_DOMAIN=example.com \
  -e RENDEZVOUS_IP=192.168.122.100 \
  -v /path/to/pull-secret.json:/run/secrets/pull-secret:Z \
  -v ~/.ssh/id_rsa.pub:/run/secrets/ssh-key:Z \
  -v /absolute/path/to/output:/assets:Z \
  aap-appliance:latest
```

The container generates config files and runs the appliance builder. The output directory receives the built disk image and all cluster config files. This takes several minutes — the builder downloads the OCP release and assembles the image.

### Parameters

| ENV variable | Default | Description |
|---|---|---|
| `BASE_DOMAIN` | — | Cluster base domain, e.g. `example.com` **(required)** |
| `RENDEZVOUS_IP` | — | IP of the SNO node **(required)** |
| `CPU_ARCHITECTURE` | `x86_64` | CPU architecture of the OCP release: `x86_64` or `aarch64` |
| `CLUSTER_NAME` | `appliance` | Cluster name (appears in the API endpoint: `api.<name>.<base-domain>`) |
| `MACHINE_NETWORK` | `192.168.122.0/24` | CIDR of the network the node is on |
| `GATEWAY` | _(unset)_ | Default route gateway. When set, bakes a static NMState network config into `agent-config.yaml` — bypasses DHCP entirely. Required when the hypervisor DHCP server does not send options 3 or 6 (e.g. VirtualBox). Must be paired with `VM_MAC` or `VM_MAC_0`. |
| `VM_MAC` | _(unset)_ | MAC address of the rendezvous NIC. Required when `GATEWAY` is set. Accepts colon-separated (`52:54:00:aa:bb:01`) or dash-separated (`08-00-27-61-6A-4A`) format. Use the same value as the `--mac` flag in `launch-appliance.sh`. `VM_MAC_0` takes precedence if both are set. |
| `DNS_SERVER` | `8.8.8.8` | DNS nameserver for the rendezvous node. Only used when `GATEWAY` is set. |
| `DISK_SIZE_GB` | `200` | Disk size in GB for the raw image (minimum 150; ignored for `live-iso`) |
| `APPLIANCE_CONTENT` | `aap` | Products to include: `aap`, `ao`, or `aap-ao` |
| `AAP_NAMESPACE` | `aap` | Kubernetes namespace where AAP is deployed |
| `AAP_PRERELEASE` | `false` | Set to `true` to use pre-release AAP from `quay.io/aap` instead of the released `registry.redhat.io` index |
| `AO_NAMESPACE` | `automation-orchestrator` | Kubernetes namespace where Automation Orchestrator is deployed |
| `AO_PRERELEASE` | `true` | Set to `false` to use a GA AO release (not yet available; reserved for future use) |

| `APPLIANCE_FORMAT` | `live-iso` | `live-iso` for a bootable ISO, `raw` for a disk image |
| `DISCONNECTED` | `true` | Use a dummy pull secret in `install-config.yaml`; the real pull secret is still used by the appliance builder for registry caching |

The pull secret and SSH key are read from fixed paths inside the container (`/run/secrets/pull-secret` and `/run/secrets/ssh-key`). Mount your files there with `-v` as shown above.

### VirtualBox networking

> [!NOTE]
> VirtualBox's built-in DHCP server has a known bug: it never transmits DHCP options 3 (gateway) and 6 (DNS) to clients, regardless of the server configuration. Without a gateway the installed RHCOS has no default route, causing the OVN-K MTU prober to fail. Without DNS, bootstrap services cannot reach each other by hostname.
>
> Work around this by passing the gateway and MAC address at build time so the appliance bakes static network config into the agent-config ISO:
>
> ```bash
> sudo podman run --rm --privileged --net=host \
>   -e BASE_DOMAIN=example.com \
>   -e RENDEZVOUS_IP=192.168.56.10 \
>   -e MACHINE_NETWORK=192.168.56.0/24 \
>   -e GATEWAY=192.168.56.1 \
>   -e VM_MAC=08:00:27:61:6a:4a \
>   -v /path/to/pull-secret.json:/run/secrets/pull-secret:Z \
>   -v ~/.ssh/id_rsa.pub:/run/secrets/ssh-key:Z \
>   -v /absolute/path/to/output:/assets:Z \
>   aap-appliance:latest
> ```
>
> Set `RENDEZVOUS_IP` to the static IP you want the VM to use and `MACHINE_NETWORK` to the VirtualBox host-only network CIDR. `GATEWAY` is the host-only adapter IP on your laptop (typically `192.168.56.1`). `VM_MAC` must match the MAC configured for the VM's host-only NIC. No DHCP reservation is needed when using static config.

## Testing with a local VM

The `scripts/` directory contains convenience scripts for every step. All scripts accept `--help` / unknown flags to print usage.

| Script | Purpose |
|---|---|
| [`scripts/libvirt-prereqs.sh`](scripts/libvirt-prereqs.sh) | Install and enable libvirt/KVM on the host (run once) |
| [`scripts/dhcp-reserve.sh`](scripts/dhcp-reserve.sh) | Add a DHCP reservation so the VM always gets `RENDEZVOUS_IP` |
| [`scripts/launch-appliance.sh`](scripts/launch-appliance.sh) | Create a libvirt VM from build output (live-iso or raw) |
| [`scripts/boot-appliance.sh`](scripts/boot-appliance.sh) | Boot a pre-built qcow2 image with port forwarding configured |
| [`scripts/setup-port-forwarding.sh`](scripts/setup-port-forwarding.sh) | Add nftables DNAT rules forwarding ports 80, 443, 6443 to the VM |
| [`scripts/launch-appliance-vbox.sh`](scripts/launch-appliance-vbox.sh) | Create a VirtualBox VM from build output |
| [`scripts/import-appliance-vbox.sh`](scripts/import-appliance-vbox.sh) | Import a pre-built appliance OVA into VirtualBox |

### VirtualBox

`launch-appliance-vbox.sh` creates the VM from scratch with these settings:

| Setting | Value | Why |
|---|---|---|
| Firmware | EFI | RHCOS requires UEFI boot |
| NIC type | virtio | Better performance and compatibility than the default e1000 |
| NIC MAC | matches `VM_MAC` build param | Static IP config is keyed to this MAC during installation |
| Hardware clock | UTC | RHCOS always expects hardware clock in UTC; without this the VM clock is offset by the host timezone, causing TLS cert errors after install |
| Paravirt provider | KVM | Improves guest performance on Linux hosts |
| Networking | host-only (vboxnet0) | Isolates the VM on a private network reachable from your laptop |

`import-appliance-vbox.sh` only applies host-only networking after import — all other settings (firmware, NIC type, memory, CPUs, hardware clock) come from the OVF that was embedded in the OVA at export time. The MAC is **not** set on import; VirtualBox assigns a random one, which does not affect the installed cluster because OVN-K migrated the static IP to a MAC-independent `br-ex` bridge during installation.

No DHCP server is needed — the appliance uses the static IP baked into the agent-config ISO at build time.

#### Builder workflow — create a VM from build output

After running the `podman run` build, use `scripts/launch-appliance-vbox.sh` to create a VirtualBox VM from the resulting ISOs (analogous to `launch-appliance.sh` for libvirt):

```bash
VM_MAC=08:00:27:61:6a:4a \
RENDEZVOUS_IP=192.168.56.100 \
./scripts/launch-appliance-vbox.sh --output-dir ./build
```

This creates a fresh VM, attaches `appliance.iso` and `agentconfig.noarch.iso`, and starts the installer. Once OCP is fully installed you can export the running VM as an OVA to distribute to others.

#### Distribution workflow — import a pre-built OVA

If you have a fully-installed appliance OVA (exported from a VM created by `launch-appliance-vbox.sh`), use `scripts/import-appliance-vbox.sh` to import it on any VirtualBox host:

```bash
RENDEZVOUS_IP=192.168.56.100 \
./scripts/import-appliance-vbox.sh --ova appliance.ova
```

This sets up `vboxnet0`, imports the OVA, applies all required VM settings, and starts the cluster. No MAC pinning is needed — the static IP config was resolved during installation and is stored on the installed disk as MAC-independent NetworkManager profiles on the `br-ex` OVN-K bridge. VirtualBox's default random MAC on import does not affect the cluster.

Use `--replace` to destroy and recreate an existing VM of the same name.

### libvirt (KVM)

#### Prerequisites

Install and enable libvirt/KVM (run once on a fresh host):

```bash
./scripts/libvirt-prereqs.sh
```

#### Live ISO (default)

Add a DHCP reservation so the VM always gets the rendezvous IP, then create and boot the VM:

```bash
./scripts/dhcp-reserve.sh --mac 52:54:00:aa:bb:01 --ip 192.168.122.100

VM_MAC=52:54:00:aa:bb:01 \
./scripts/launch-appliance.sh --output-dir /path/to/build
```

`launch-appliance.sh` copies the ISOs to the libvirt image pool and calls `virt-install`. Use `--replace` to destroy and recreate an existing VM of the same name.

#### Raw disk image

```bash
./scripts/dhcp-reserve.sh --mac 52:54:00:aa:bb:01 --ip 192.168.122.100

VM_MAC=52:54:00:aa:bb:01 \
APPLIANCE_FORMAT=raw \
./scripts/launch-appliance.sh --output-dir /path/to/build
```

#### Booting a pre-built qcow2

If you have a qcow2 image of a fully installed appliance, `boot-appliance.sh` imports it and sets up port forwarding (ports 80, 443, 6443) so the cluster API and console are reachable from the host:

```bash
./scripts/boot-appliance.sh --qcow2 /path/to/appliance.qcow2
```

Port forwarding can also be configured separately (e.g. after a network restart):

```bash
RENDEZVOUS_IP=192.168.122.100 ./scripts/setup-port-forwarding.sh
```

> **Note:** The VM needs at least 32 GB RAM and 8 vCPUs. Do not over-allocate RAM relative to the host; the OOM killer will crash the VM mid-install.

## Monitoring installation

Watch early boot progress via the graphical console:

```bash
sudo virt-viewer aap-appliance
```

Once the node is up, tail the assisted installer:

```bash
ssh core@192.168.122.100
sudo journalctl -fu assisted-service   # orchestrates installation
sudo journalctl -fu agent              # runs steps on this host
```

Monitor installation progress via the Assisted Installer API:

```bash
export TOKEN=$(ssh core@192.168.122.100 \
  "sudo grep USER_AUTH_TOKEN /etc/assisted/rendezvous-host.env | cut -d= -f2")

export CLUSTER_ID=$(curl -s -H "Authorization: $TOKEN" \
  http://192.168.122.100:8090/api/assisted-install/v2/clusters | \
  python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

watch -n 30 'curl -s -H "Authorization: $TOKEN" \
  "http://192.168.122.100:8090/api/assisted-install/v2/clusters/$CLUSTER_ID/hosts" | \
  python3 -c "
import sys, json
for h in json.load(sys.stdin):
    p = h.get(\"progress\", {})
    print(h[\"id\"], \"|\", h[\"status\"], \"|\", p.get(\"current_stage\",\"n/a\"), \"|\", str(p.get(\"installation_percentage\",\"?\"))+\"%\")
" && curl -s -H "Authorization: $TOKEN" \
  "http://192.168.122.100:8090/api/assisted-install/v2/clusters/$CLUSTER_ID" | \
  python3 -c "
import sys, json
c = json.load(sys.stdin)
p = c.get(\"progress\", {})
print(\"Cluster:\", c[\"status\"], \"-\", c[\"status_info\"])
print(\"Total:\", p.get(\"total_percentage\",\"n/a\"), \"% | Finalizing:\", p.get(\"finalizing_stage_percentage\",\"n/a\"), \"%\")
"'
```

When `Cluster: installed` appears, the cluster is ready.

## Accessing the cluster

```bash
export KUBECONFIG=/path/to/output/cluster-config/auth/kubeconfig
oc get nodes
oc get clusteroperators
oc get aap -n aap -w
```

The local-path-provisioner is already running as the default StorageClass. The appliance automatically applies operator CRs once the operator CRDs are registered.

```bash
# AAP (APPLIANCE_CONTENT=aap or aap-ao)
oc get aap -n aap -w

# AO (APPLIANCE_CONTENT=ao or aap-ao)
oc get automationorchestrator -n automation-orchestrator -w
oc get cluster -n automation-orchestrator -w   # CloudNativePG cluster
```

## Cleanup

```bash
sudo virsh destroy aap-appliance
sudo virsh undefine aap-appliance
sudo rm /var/lib/libvirt/images/aap-appliance.raw /var/lib/libvirt/images/agentconfig.noarch.iso
```

For live ISO:

```bash
sudo virsh destroy aap-appliance
sudo virsh undefine aap-appliance --remove-all-storage
sudo rm /var/lib/libvirt/images/aap-appliance.iso /var/lib/libvirt/images/agentconfig.noarch.iso
```
