# AAP Appliance Builder

Builds an OpenShift Single Node (SNO) appliance disk image with Ansible Automation Platform (AAP) pre-baked. When the image is cloned to hardware or a VM and booted, it installs OCP 4.18 and deploys AAP fully offline — no internet access required at install time.

The image is built on top of the [openshift-appliance](https://github.com/openshift/appliance) tool. This repo bakes in the static OCP and AAP manifests so the only inputs you need to provide at build time are your site-specific parameters.

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

The built image includes:
- OCP 4.18 SNO
- AAP operator (2.7-next-ns) installed from the baked-in catalog
- Rancher local-path-provisioner as the default StorageClass (hostPath-backed, supports RWX)
- All required images pre-cached in the appliance local registry — no external pulls during install

## Updating AAP image pins

The AAP operator images in `entrypoint.sh` and the CatalogSource in `assets/openshift/aap.yaml` are pinned to specific digests. The `operator-index:2.7-next` tag moves as new builds are published — run this script before rebuilding to pick up the latest:

```bash
./scripts/update-aap-images.sh --authfile /path/to/pull-secret.json
```

The script:
1. Resolves current digests for `operator-index:2.7-next` and `platform-operator-bundle:2.7-next-ns`
2. Verifies the bundle digest is referenced by the index (cross-check)
3. Extracts `spec.relatedImages` from the bundle's CSV manifest
4. Rewrites `config/aap-images.yaml` with the index + all bundle relatedImages
5. Updates the CatalogSource `spec.image` digest in `assets/openshift/aap.yaml`

Review the diff with `git diff` before building.

> **Note:** The pull secret must have access to `quay.io/aap`. If your standard Red Hat pull secret doesn't cover it, merge in a `quay.io/aap`-scoped credential — see [Prerequisites](#prerequisites).

## Build

```bash
sudo podman build -t aap-appliance:latest .
```

This step bakes the static manifests into the image. It only needs to be done once per version. `sudo` is required so the image lands in root's store, where the privileged `podman run` can find it.

## Run

```bash
sudo podman run --rm --privileged --net=host \
  -e PULL_SECRET="$(cat /path/to/pull-secret.json)" \
  -e BASE_DOMAIN=example.com \
  -e RENDEZVOUS_IP=192.168.122.100 \
  -e SSH_KEY="$(cat ~/.ssh/id_rsa.pub)" \
  -v /absolute/path/to/output:/assets:Z \
  aap-appliance:latest
```

The container generates config files and runs the appliance builder. The output directory receives the built disk image and all cluster config files. This takes several minutes — the builder downloads the OCP release and assembles the image.

### Parameters

| ENV variable | Default | Description |
|---|---|---|
| `PULL_SECRET` | — | Pull secret JSON **(required)** |
| `BASE_DOMAIN` | — | Cluster base domain, e.g. `example.com` **(required)** |
| `RENDEZVOUS_IP` | — | IP of the SNO node **(required)** |
| `SSH_KEY` | — | SSH public key — enables `ssh core@<rendezvous-ip>` during install |
| `CLUSTER_NAME` | `appliance` | Cluster name (appears in the API endpoint: `api.<name>.<base-domain>`) |
| `MACHINE_NETWORK` | `192.168.122.0/24` | CIDR of the network the node is on |
| `DISK_SIZE_GB` | `200` | Disk size in GB for the raw image (minimum 150; ignored for `live-iso`) |
| `NAMESPACE` | `aap` | Kubernetes namespace where AAP is deployed |
| `APPLIANCE_FORMAT` | `raw` | `raw` for a disk image, `live-iso` for a bootable ISO |

## Testing with a local VM

### Raw disk image

Copy the built assets to the libvirt image pool (QEMU requires files outside your home directory):

```bash
sudo cp /path/to/output/appliance.raw /var/lib/libvirt/images/aap-appliance.raw
sudo cp /path/to/output/cluster-config/agentconfig.noarch.iso /var/lib/libvirt/images/agentconfig.noarch.iso
```

Add a DHCP reservation so the VM always gets the rendezvous IP (the MAC here is an example; use any stable MAC):

```bash
sudo virsh net-update default add ip-dhcp-host \
  '<host mac="52:54:00:aa:bb:01" ip="192.168.122.100"/>' \
  --live --config
```

Boot a VM from the appliance image with the config ISO attached as a CD-ROM:

```bash
sudo virt-install \
  --name aap-appliance \
  --memory 32768 \
  --vcpus 8 \
  --disk path=/var/lib/libvirt/images/aap-appliance.raw,format=raw,bus=virtio \
  --disk path=/var/lib/libvirt/images/agentconfig.noarch.iso,device=cdrom,readonly=on \
  --network network=default,model=virtio,mac=52:54:00:aa:bb:01 \
  --os-variant rhel9-unknown \
  --events on_reboot=restart,on_poweroff=restart,on_crash=restart \
  --boot hd \
  --noautoconsole \
  --import
```

> **Note:** The VM needs at least 32 GB RAM and 8 vCPUs. `--import` skips OS installation — the appliance handles everything from the raw image. The MAC in `--network` must match the DHCP reservation above. Do not over-allocate RAM relative to the host; the OOM killer will crash the VM mid-install.

### Live ISO

With `APPLIANCE_FORMAT=live-iso` you get `appliance.iso` instead of `appliance.raw`. The ISO boots directly — no disk pre-imaging required. You provide an empty disk for OCP to install onto.

```bash
sudo cp /path/to/output/appliance.iso /var/lib/libvirt/images/aap-appliance.iso
sudo cp /path/to/output/cluster-config/agentconfig.noarch.iso /var/lib/libvirt/images/agentconfig.noarch.iso
```

```bash
sudo virsh net-update default add ip-dhcp-host \
  '<host mac="52:54:00:aa:bb:01" ip="192.168.122.100"/>' \
  --live --config
```

```bash
sudo virt-install \
  --name aap-appliance \
  --memory 32768 \
  --vcpus 8 \
  --disk size=200,bus=virtio \
  --disk path=/var/lib/libvirt/images/aap-appliance.iso,device=cdrom,readonly=on \
  --disk path=/var/lib/libvirt/images/agentconfig.noarch.iso,device=cdrom,readonly=on \
  --network network=default,model=virtio,mac=52:54:00:aa:bb:01 \
  --os-variant rhel9-unknown \
  --events on_reboot=restart,on_poweroff=restart,on_crash=restart \
  --boot hd,cdrom \
  --noautoconsole
```

> **Note:** `--boot hd,cdrom` lets the first boot fall through to the ISO (the disk is empty), and subsequent reboots after bootstrap boot from the installed OCP on the disk.

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

The local-path-provisioner is already running as the default StorageClass. The appliance automatically applies the AAP CR once the operator CRD is registered.

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
