#!/usr/bin/env python3
"""Generate configs and manifests from static templates and environment variables.

BUILD_MODE controls which outputs are generated:

  appliance   Phase 1 — writes appliance-config.yaml and infra manifests.
              Always caches both AAP and AO images so the resulting appliance.iso
              is distributable: any APPLIANCE_CONTENT choice works at phase 2
              without a rebuild.
              Required env vars:  PULL_SECRET_FILE
              Optional env vars:  SSH_KEY_FILE, CPU_ARCHITECTURE, APPLIANCE_FORMAT,
                                  DISK_SIZE_GB, AAP_PRERELEASE, AO_PRERELEASE

  agentconfig Phase 2 — writes install-config.yaml, agent-config.yaml, and operator
              manifests under cluster-config/openshift/. The manifests are picked up
              by `openshift-install agent create config-image` and embedded in the
              agentconfig ISO, giving end users control over which operators are
              installed without touching the appliance.
              Required env vars:  PULL_SECRET_FILE, BASE_DOMAIN, RENDEZVOUS_IP
              Optional env vars:  SSH_KEY_FILE, APPLIANCE_CONTENT, CLUSTER_NAME,
                                  MACHINE_NETWORK, GATEWAY, VM_MAC / VM_MAC_0,
                                  DNS_SERVER, DISCONNECTED, AAP_NAMESPACE,
                                  AO_NAMESPACE, AAP_PRERELEASE, AO_PRERELEASE

  all         Both phases in sequence (default). Used by entrypoint.sh one-shot mode.

Environment variables — phase 1 (appliance):
  PULL_SECRET_FILE      Path to pull secret JSON                    (required)
  SSH_KEY_FILE          Path to SSH public key
  CPU_ARCHITECTURE      x86_64 or aarch64                            default: x86_64
  APPLIANCE_FORMAT      live-iso or raw                              default: live-iso
  DISK_SIZE_GB          Disk size in GB; only for raw format         default: 200
  AAP_PRERELEASE        Cache pre-release AAP images (true/false)    default: false
  AO_PRERELEASE         Cache pre-release AO images (true/false)     default: true

Environment variables — phase 2 (agentconfig):
  PULL_SECRET_FILE      Path to pull secret JSON                    (required)
  BASE_DOMAIN           Cluster base domain                         (required)
  RENDEZVOUS_IP         Rendezvous node IP address                  (required)
  SSH_KEY_FILE          Path to SSH public key
  APPLIANCE_CONTENT     Operators to install:                        default: aap-full
                        aap        AAP operator only
                        ao         Automation Orchestrator only
                        aap-with-ao  AAP + AO operators
                        aap-full   All AAP product operators (currently equivalent to aap-with-ao;
                                   use this when you want everything and future operators too)
  CLUSTER_NAME          OpenShift cluster name                       default: appliance
  MACHINE_NETWORK       Machine network CIDR                         default: 192.168.122.0/24
  GATEWAY               Static gateway IP; enables static network config
  VM_MAC / VM_MAC_0     MAC address of the rendezvous NIC; required with GATEWAY
  DNS_SERVER            DNS server IP; used only with static config  default: 8.8.8.8
  DISCONNECTED          Dummy pull secret in install-config          default: false
  AAP_NAMESPACE         Namespace for the AAP operator               default: aap
  AO_NAMESPACE          Namespace for the AO operator                default: automation-orchestrator
  AAP_PRERELEASE        Use pre-release AAP manifest                 default: false
  AO_PRERELEASE         Use pre-release AO manifest                  default: true

Usage: BUILD_MODE=appliance python3 generate-configs.py
       python3 generate-configs.py --help
"""
import ipaddress, os, secrets as _secrets, shutil, pathlib, sys, yaml

if '--help' in sys.argv or '-h' in sys.argv:
    print(__doc__)
    sys.exit(0)

# Root directory for openshift-appliance assets (appliance-config.yaml, openshift/, cluster-config/, ...).
# Default /assets matches runtime entrypoint.sh; ISO CI builds set ASSETS_DIR=/ to match upstream iso_builder (--dir /).
_assets_raw = os.environ.get('ASSETS_DIR', '/assets').strip()
if not _assets_raw or _assets_raw == '/':
    ASSETS_DIR = pathlib.Path('/')
else:
    ASSETS_DIR = pathlib.Path(_assets_raw.rstrip('/'))

# Root directory for static template files baked into the container image.
# Default /static matches the container layout; override in tests via STATIC_DIR.
STATIC_DIR = pathlib.Path(os.environ.get('STATIC_DIR', '/static'))

build_mode = os.environ.get('BUILD_MODE', 'all').lower()
if build_mode not in ('appliance', 'agentconfig', 'all'):
    raise SystemExit(
        f"error: BUILD_MODE must be one of: appliance, agentconfig, all (got {build_mode!r})"
    )

# Common env vars needed by one or both phases
pull_secret_path = os.environ.get('PULL_SECRET_FILE', '/run/secrets/pull-secret')
pull_secret      = pathlib.Path(pull_secret_path).read_text().strip()
ssh_key_path     = os.environ.get('SSH_KEY_FILE', '/run/secrets/ssh-key')
ssh_key          = p.read_text().strip() if (p := pathlib.Path(ssh_key_path)).exists() else ''
aap_prerelease   = os.environ.get('AAP_PRERELEASE', '').lower() in ('1', 'true', 'yes')
ao_prerelease    = os.environ.get('AO_PRERELEASE', 'true').lower() in ('1', 'true', 'yes')
appliance_format = os.environ.get('APPLIANCE_FORMAT', 'live-iso')
disk_size_gb     = int(os.environ.get('DISK_SIZE_GB', '200'))
cpu_architecture = os.environ.get('CPU_ARCHITECTURE', 'x86_64')
if cpu_architecture not in ('x86_64', 'aarch64'):
    raise SystemExit(
        f"error: CPU_ARCHITECTURE must be one of: x86_64, aarch64 (got {cpu_architecture!r})"
    )


def literal_block(value):
    """Return a YAML literal block scalar string (key written separately by caller)."""
    lines = ['|\n']
    for line in value.rstrip('\n').split('\n'):
        lines.append(f'  {line}\n')
    return ''.join(lines)


def generate_appliance():
    """Phase 1: write appliance-config.yaml and copy infra manifests.

    Always caches both AAP and AO image sets so any phase-2 APPLIANCE_CONTENT choice works.
    Operator manifests (CatalogSource, Subscription, CRs) are NOT written here — they belong
    in the agentconfig ISO (phase 2) so the appliance.iso is distributable.
    """
    (ASSETS_DIR / 'openshift').mkdir(parents=True, exist_ok=True)

    # Infrastructure manifests — always present, independent of operator choice
    for f in ('local-path-provisioner.yaml', 'idms-additional-images.yaml'):
        shutil.copy(STATIC_DIR / 'openshift' / f, ASSETS_DIR / 'openshift' / f)

    # AAP IDMS: only needed when caching prerelease images (quay.io/aap redirect)
    if aap_prerelease:
        shutil.copy(
            STATIC_DIR / 'openshift' / 'idms-aap-prerelease.yaml',
            ASSETS_DIR / 'openshift' / 'idms-aap-prerelease.yaml',
        )

    # AO IDMS: always included — AO only has prerelease images (quay.io/aap redirect)
    shutil.copy(
        STATIC_DIR / 'openshift' / 'idms-ao-prerelease.yaml',
        ASSETS_DIR / 'openshift' / 'idms-ao-prerelease.yaml',
    )

    # appliance-config.yaml — start from static template, inject secrets and images
    cfg = (STATIC_DIR / 'config' / 'appliance-config.yaml').read_text()
    cfg = cfg.replace('${CPU_ARCHITECTURE}', cpu_architecture)
    if appliance_format != 'live-iso':
        cfg += f'diskSizeGB: {disk_size_gb}\n'
    cfg += f'pullSecret: {literal_block(pull_secret)}'
    if ssh_key:
        cfg += f'sshKey: {literal_block(ssh_key)}'

    # Collect and deduplicate images for both AAP and AO unconditionally so either
    # product can be deployed from this appliance without a rebuild.
    all_images: list[dict] = []
    aap_images_file = STATIC_DIR / 'config' / ('aap-images-prerelease.yaml' if aap_prerelease else 'aap-images.yaml')
    all_images.extend(yaml.safe_load(aap_images_file.read_text()) or [])
    ao_images_file = STATIC_DIR / 'config' / ('ao-images-prerelease.yaml' if ao_prerelease else 'ao-images.yaml')
    all_images.extend(yaml.safe_load(ao_images_file.read_text()) or [])

    seen: set[str] = set()
    cfg += 'additionalImages:\n'
    for entry in all_images:
        if entry['name'] not in seen:
            seen.add(entry['name'])
            cfg += f'- name: {entry["name"]}\n'
    cfg += '- name: docker.io/rancher/local-path-provisioner:v0.0.35\n'

    (ASSETS_DIR / 'appliance-config.yaml').write_text(cfg)


def _split_yaml_docs(content: str) -> list[str]:
    """Split a multi-document YAML string into a list of non-empty single-document strings."""
    docs = []
    for part in content.split('---'):
        stripped = part.strip()
        if stripped:
            docs.append('---\n' + stripped + '\n')
    return docs


_JOB_IMAGE = 'registry.appliance.openshift.com:22625/ubi9-minimal:latest'


def _write_post_install_crs_job(manifest_dir, include_aap, include_ao, aap_cr_content, ao_cr_content):
    """Write a Job that applies operator CRs after their CRDs are registered.

    openshift-install agent create config-image only picks up flat files from openshift/ —
    it does not recurse into subdirectories.  Embedding the CRs in a ConfigMap and applying
    them from a Job side-steps the timing problem: the Job polls for each CRD and then
    applies the CRs via curl against the Kubernetes API using the pod service account token.
    No dependency on the internal image registry or oc binary.

    The image is referenced directly against the appliance embedded registry
    (registry.appliance.openshift.com:22625), whose hostname is in /etc/hosts on every
    appliance node — no external DNS, no IDMS, no phase-1 digest pin required.
    """
    wait_crds: list[str] = []
    cr_files: dict[str, str] = {}

    if include_aap:
        wait_crds.append('ansibleautomationplatforms.aap.ansible.com')
        for i, doc in enumerate(_split_yaml_docs(aap_cr_content)):
            cr_files[f'aap-cr-{i}.yaml'] = doc

    if include_ao:
        wait_crds.append('automationorchestrators.aap.ansible.com')
        wait_crds.append('clusters.postgresql.cnpg.io')
        for i, doc in enumerate(_split_yaml_docs(ao_cr_content)):
            cr_files[f'ao-cr-{i}.yaml'] = doc

    if not cr_files:
        return

    wait_blocks = []
    for crd in wait_crds:
        wait_blocks.append(
            f'echo "Waiting for CRD {crd}..."\n'
            f'until curl -sf --cacert "$CA" -H "Authorization: Bearer $TOKEN" \\\n'
            f'    "$API/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{crd}" \\\n'
            f'    >/dev/null 2>&1; do\n'
            f'  echo "  {crd} not yet available, retrying in 30s..."\n'
            f'  sleep 30\n'
            f'done\n'
            f'echo "CRD {crd} is available"'
        )
    wait_lines = '\n'.join(wait_blocks)

    # Bash script using curl against the Kubernetes API — no oc binary needed.
    # Each /crs/*.yaml file is a single-document YAML resource (split at generation time).
    # get_plural() uses the API discovery endpoint to look up the plural resource name for any
    # kind, so the script works for any new operator CR without hardcoded mappings.
    script = r"""set -euo pipefail
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
CA=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
API=https://kubernetes.default.svc
declare -A PLURAL_CACHE

get_plural() {
  local api_root="$1" kind="$2" cache_key result
  cache_key="${api_root}:${kind}"
  [[ -n "${PLURAL_CACHE[$cache_key]+_}" ]] && { echo "${PLURAL_CACHE[$cache_key]}"; return; }
  case "${api_root}:${kind}" in
    /api/v1:ConfigMap)                                         result=configmaps ;;
    /api/v1:Namespace)                                         result=namespaces ;;
    /api/v1:Secret)                                            result=secrets ;;
    /api/v1:Service)                                           result=services ;;
    /api/v1:ServiceAccount)                                    result=serviceaccounts ;;
    /apis/aap.ansible.com/v1alpha1:AnsibleAutomationPlatform)   result=ansibleautomationplatforms ;;
    /apis/aap.ansible.com/v1alpha1:AutomationOrchestrator)     result=automationorchestrators ;;
    /apis/postgresql.cnpg.io/v1:Cluster)                       result=clusters ;;
    /apis/postgresql.cnpg.io/v1:Database)                      result=databases ;;
    *)
      result=$(curl -sf --cacert "$CA" -H "Authorization: Bearer $TOKEN" "$API$api_root" |
        tr -d '\n\r' |
        sed 's/"name":"/\n"name":"/g' |
        grep "\"kind\":\"${kind}\"" |
        grep -v '"name":"[^"]*/' |
        head -1 |
        sed 's/.*"name":"//; s/".*//')
      ;;
  esac
  PLURAL_CACHE[$cache_key]="$result"
  echo "$result"
}

apply_doc() {
  local file="$1"
  local api_version kind namespace name group version api_root plural path http_code tmpout
  api_version=$(grep '^apiVersion:' "$file" | head -1 | sed 's/^apiVersion:[[:space:]]*//')
  kind=$(grep '^kind:' "$file" | head -1 | sed 's/^kind:[[:space:]]*//')
  namespace=$(grep '^  namespace:' "$file" | head -1 | sed 's/^[[:space:]]*namespace:[[:space:]]*//')
  name=$(grep '^  name:' "$file" | head -1 | sed 's/^[[:space:]]*name:[[:space:]]*//')
  if [[ "$api_version" == *"/"* ]]; then
    group="${api_version%/*}"
    version="${api_version#*/}"
    api_root="/apis/$group/$version"
  else
    api_root="/api/$api_version"
  fi
  plural=$(get_plural "$api_root" "$kind")
  if [[ -z "$plural" ]]; then
    echo "ERROR: could not discover plural resource name for $kind at $api_root" >&2
    return 1
  fi
  if [[ -n "$namespace" ]]; then
    path="$api_root/namespaces/$namespace/$plural/$name"
  else
    path="$api_root/$plural/$name"
  fi
  echo "Applying $kind $namespace/$name..."
  tmpout=$(mktemp)
  http_code=$(curl -sw '%{http_code}' --cacert "$CA" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/apply-patch+yaml" \
    -X PATCH \
    "$API${path}?fieldManager=post-install-crs&force=true" \
    --data-binary "@$file" -o "$tmpout")
  if [[ ! "$http_code" =~ ^2 ]]; then
    echo "ERROR: HTTP $http_code when applying $kind $namespace/$name" >&2
    cat "$tmpout" >&2
    rm -f "$tmpout"
    return 1
  fi
  rm -f "$tmpout"
  echo "  Applied $kind $namespace/$name: HTTP $http_code"
}

__WAIT_LINES__
for file in /crs/*.yaml; do
  apply_doc "$file"
done
echo "post-install CRs applied successfully"
""".replace('__WAIT_LINES__', wait_lines)

    # ConfigMap data: each individual CR document as a literal-block-scalar value
    cm_data = ''
    for fname, content in cr_files.items():
        indented = '\n'.join('    ' + line for line in content.splitlines())
        cm_data += f'  {fname}: |\n{indented}\n'

    script_indented = '\n'.join('          ' + line for line in script.splitlines())

    manifest = f"""\
---
apiVersion: v1
kind: Namespace
metadata:
  name: post-install-crs
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: post-install-crs
  namespace: post-install-crs
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: post-install-crs
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: post-install-crs
  namespace: post-install-crs
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: post-install-crs
  namespace: post-install-crs
data:
{cm_data}---
apiVersion: batch/v1
kind: Job
metadata:
  name: post-install-crs
  namespace: post-install-crs
spec:
  backoffLimit: 20
  template:
    spec:
      serviceAccountName: post-install-crs
      restartPolicy: OnFailure
      containers:
      - name: apply
        image: {_JOB_IMAGE}
        command:
        - /bin/bash
        - -c
        - |
{script_indented}
        volumeMounts:
        - mountPath: /crs
          name: crs
      volumes:
      - name: crs
        configMap:
          name: post-install-crs
"""
    (manifest_dir / 'post-install-crs-job.yaml').write_text(manifest)


def generate_agentconfig():
    """Phase 2: write install-config.yaml, agent-config.yaml, and operator manifests.

    Operator manifests are written to cluster-config/openshift/ so that
    `openshift-install agent create config-image` picks them up and embeds them in the
    agentconfig ISO. The user controls which operators to install via APPLIANCE_CONTENT.
    """
    base_domain      = os.environ['BASE_DOMAIN']
    rendezvous_ip    = os.environ['RENDEZVOUS_IP']
    cluster_name     = os.environ.get('CLUSTER_NAME', 'appliance')
    machine_network  = os.environ.get('MACHINE_NETWORK', '192.168.122.0/24')
    gateway          = os.environ.get('GATEWAY', '')
    vm_mac           = os.environ.get('VM_MAC_0', os.environ.get('VM_MAC', '')).lower().replace('-', ':')
    dns_server       = os.environ.get('DNS_SERVER', '8.8.8.8')
    disconnected     = os.environ.get('DISCONNECTED', '').lower() in ('1', 'true', 'yes')
    aap_namespace    = os.environ.get('AAP_NAMESPACE', 'aap')
    ao_namespace     = os.environ.get('AO_NAMESPACE', 'automation-orchestrator')
    ao_db_password   = _secrets.token_urlsafe(24)

    appliance_content = os.environ.get('APPLIANCE_CONTENT', 'aap-full').lower()
    if appliance_content not in ('aap', 'ao', 'aap-with-ao', 'aap-full'):
        raise SystemExit(
            f"error: APPLIANCE_CONTENT must be one of: aap, ao, aap-with-ao, aap-full (got {appliance_content!r})"
        )
    # aap-full: all AAP product operators; currently aap + ao but extensible in the future
    include_aap = appliance_content in ('aap', 'aap-with-ao', 'aap-full')
    include_ao  = appliance_content in ('ao', 'aap-with-ao', 'aap-full')

    if gateway and not vm_mac:
        raise SystemExit("error: GATEWAY requires VM_MAC (or VM_MAC_0) to be set")

    manifest_dir = ASSETS_DIR / 'cluster-config' / 'openshift'
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / 'cluster-config').mkdir(parents=True, exist_ok=True)

    # Operator manifests → cluster-config/openshift/ (picked up by openshift-install)
    aap_cr_content = None
    ao_cr_content  = None

    if include_aap:
        aap_src = STATIC_DIR / 'openshift' / ('aap-prerelease.yaml' if aap_prerelease else 'aap.yaml')
        (manifest_dir / 'aap.yaml').write_text(
            aap_src.read_text().replace('${AAP_NAMESPACE}', aap_namespace)
        )
        aap_cr_content = (
            (STATIC_DIR / 'openshift' / 'crs' / 'aap-cr.yaml').read_text()
            .replace('${AAP_NAMESPACE}', aap_namespace)
        )

    if include_ao:
        ao_src = STATIC_DIR / 'openshift' / ('ao-prerelease.yaml' if ao_prerelease else 'ao.yaml')
        (manifest_dir / 'ao.yaml').write_text(
            ao_src.read_text().replace('${AO_NAMESPACE}', ao_namespace)
        )
        ao_cr_content = (
            (STATIC_DIR / 'openshift' / 'crs' / 'ao-cr.yaml').read_text()
            .replace('${AO_NAMESPACE}', ao_namespace)
            .replace('${AO_DB_PASSWORD}', ao_db_password)
        )

    # CRs require operator CRDs to exist before they can be applied.
    # openshift-install agent create config-image only picks up flat files in openshift/ —
    # it does not recurse into subdirectories, so we cannot rely on crs/ being included.
    # A Job polls for each CRD and applies the CRs once all are registered.
    _write_post_install_crs_job(manifest_dir, include_aap, include_ao, aap_cr_content, ao_cr_content)

    # install-config.yaml — substitute env vars then append secrets
    tmpl = (STATIC_DIR / 'config' / 'install-config.yaml').read_text()
    for var, val in [
        ('${BASE_DOMAIN}',     base_domain),
        ('${CLUSTER_NAME}',    cluster_name),
        ('${MACHINE_NETWORK}', machine_network),
    ]:
        tmpl = tmpl.replace(var, val)
    install_pull_secret = '{"auths":{"":{"auth":"dXNlcjpwYXNz"}}}' if disconnected else pull_secret
    tmpl += f'pullSecret: {literal_block(install_pull_secret)}'
    if ssh_key:
        tmpl += f'sshKey: {literal_block(ssh_key)}'
    (ASSETS_DIR / 'cluster-config' / 'install-config.yaml').write_text(tmpl)

    # agent-config.yaml — substitute rendezvousIP, optionally append static network config
    tmpl = (STATIC_DIR / 'config' / 'agent-config.yaml').read_text()
    tmpl = tmpl.replace('${RENDEZVOUS_IP}', rendezvous_ip)
    if gateway:
        net = ipaddress.ip_network(machine_network, strict=False)
        hosts = [{
            'interfaces': [{'name': 'eth0', 'macAddress': vm_mac}],
            'networkConfig': {
                'interfaces': [{
                    'name': 'eth0',
                    'type': 'ethernet',
                    'state': 'up',
                    'identifier': 'mac-address',
                    'mac-address': vm_mac,
                    'ipv4': {
                        'enabled': True,
                        'dhcp': False,
                        'address': [{'ip': rendezvous_ip, 'prefix-length': net.prefixlen}],
                    },
                }],
                'routes': {
                    'config': [{
                        'destination': '0.0.0.0/0',
                        'next-hop-address': gateway,
                        'next-hop-interface': 'eth0',
                    }],
                },
                'dns-resolver': {
                    'config': {'server': [dns_server]},
                },
            },
        }]
        tmpl += yaml.dump({'hosts': hosts}, default_flow_style=False, sort_keys=False)
    (ASSETS_DIR / 'cluster-config' / 'agent-config.yaml').write_text(tmpl)


if build_mode in ('appliance', 'all'):
    generate_appliance()
if build_mode in ('agentconfig', 'all'):
    generate_agentconfig()
