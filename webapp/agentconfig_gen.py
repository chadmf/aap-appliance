"""Generate agentconfig manifests and ISO for the AAP appliance.

Port of the agentconfig phase from scripts/generate-configs.py, refactored as
pure functions operating on AgentconfigParams instead of reading os.environ.
"""
from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import os
import pathlib
import secrets
import shutil
import tempfile
from typing import Optional

import yaml

_MANIFEST_DIR = pathlib.Path(__file__).parent / 'manifests'
_CONFIG_DIR = pathlib.Path(__file__).parent / 'config'
_OCP_VERSION = '4.22.0'
_JOB_IMAGE = 'registry.appliance.openshift.com:22625/ubi9-minimal:latest'

# Validation constants
_VALID_APPLIANCE_CONTENT = ('aap', 'ao', 'aap-with-ao', 'aap-full')


@dataclasses.dataclass
class AgentconfigParams:
    base_domain: str
    rendezvous_ip: str
    pull_secret: str            # raw JSON string
    gateway: str                # always set (static networking required)
    vm_mac: str                 # normalised to colon form
    machine_network: str = '192.168.122.0/24'
    dns_server: str = '8.8.8.8'
    cluster_name: str = 'appliance'
    appliance_content: str = 'aap-full'
    aap_namespace: str = 'aap'
    ao_namespace: str = 'automation-orchestrator'
    aap_prerelease: bool = False
    ao_prerelease: bool = True
    disconnected: bool = False
    ssh_key: str = ''


def normalize_mac(mac: str) -> str:
    return mac.lower().replace('-', ':')


def literal_block(value: str) -> str:
    """Return a YAML literal block scalar (caller writes the key separately)."""
    lines = ['|\n']
    for line in value.rstrip('\n').split('\n'):
        lines.append(f'  {line}\n')
    return ''.join(lines)


def _split_yaml_docs(content: str) -> list:
    docs = []
    for part in content.split('---'):
        stripped = part.strip()
        if stripped:
            docs.append('---\n' + stripped + '\n')
    return docs


def _content_flags(appliance_content: str) -> tuple[bool, bool]:
    ac = appliance_content.lower()
    include_aap = ac in ('aap', 'aap-with-ao', 'aap-full')
    include_ao = ac in ('ao', 'aap-with-ao', 'aap-full')
    return include_aap, include_ao


def generate_install_config(params: AgentconfigParams) -> str:
    tmpl = (_CONFIG_DIR / 'install-config.yaml').read_text()
    for var, val in [
        ('${BASE_DOMAIN}', params.base_domain),
        ('${CLUSTER_NAME}', params.cluster_name),
        ('${MACHINE_NETWORK}', params.machine_network),
    ]:
        tmpl = tmpl.replace(var, val)
    ps = '{"auths":{"":{"auth":"dXNlcjpwYXNz"}}}' if params.disconnected else params.pull_secret
    tmpl += f'pullSecret: {literal_block(ps)}'
    if params.ssh_key:
        tmpl += f'sshKey: {literal_block(params.ssh_key)}'
    return tmpl


def generate_agent_config(params: AgentconfigParams) -> str:
    tmpl = (_CONFIG_DIR / 'agent-config.yaml').read_text()
    tmpl = tmpl.replace('${RENDEZVOUS_IP}', params.rendezvous_ip)
    net = ipaddress.ip_network(params.machine_network, strict=False)
    hosts = [{
        'interfaces': [{'name': 'eth0', 'macAddress': params.vm_mac}],
        'networkConfig': {
            'interfaces': [{
                'name': 'eth0',
                'type': 'ethernet',
                'state': 'up',
                'identifier': 'mac-address',
                'mac-address': params.vm_mac,
                'ipv4': {
                    'enabled': True,
                    'dhcp': False,
                    'address': [{'ip': params.rendezvous_ip, 'prefix-length': net.prefixlen}],
                },
            }],
            'routes': {
                'config': [{
                    'destination': '0.0.0.0/0',
                    'next-hop-address': params.gateway,
                    'next-hop-interface': 'eth0',
                }],
            },
            'dns-resolver': {
                'config': {'server': [params.dns_server]},
            },
        },
    }]
    tmpl += yaml.dump({'hosts': hosts}, default_flow_style=False, sort_keys=False)
    return tmpl


def generate_operator_manifests(params: AgentconfigParams) -> dict:
    """Return {filename: content} for all operator manifests based on APPLIANCE_CONTENT."""
    result: dict[str, str] = {}
    ocp_dir = _MANIFEST_DIR / 'openshift'
    include_aap, include_ao = _content_flags(params.appliance_content)

    # Infrastructure manifests — always included
    for fname in ('local-path-provisioner.yaml', 'idms-additional-images.yaml'):
        result[fname] = (ocp_dir / fname).read_text()

    # AO IDMS always included (AO only has prerelease images via quay.io redirect)
    result['idms-ao-prerelease.yaml'] = (ocp_dir / 'idms-ao-prerelease.yaml').read_text()

    if include_aap:
        src = ocp_dir / ('aap-prerelease.yaml' if params.aap_prerelease else 'aap.yaml')
        result['aap.yaml'] = src.read_text().replace('${AAP_NAMESPACE}', params.aap_namespace)
        if params.aap_prerelease:
            result['idms-aap-prerelease.yaml'] = (ocp_dir / 'idms-aap-prerelease.yaml').read_text()

    if include_ao:
        result['ao.yaml'] = (
            (ocp_dir / 'ao-prerelease.yaml').read_text()
            .replace('${AO_NAMESPACE}', params.ao_namespace)
        )

    return result


def generate_post_install_job(params: AgentconfigParams) -> str:
    """Port of _write_post_install_crs_job from generate-configs.py. Returns YAML string."""
    include_aap, include_ao = _content_flags(params.appliance_content)
    ao_db_password = secrets.token_urlsafe(24)
    ocp_dir = _MANIFEST_DIR / 'openshift'

    wait_crds: list[str] = []
    cr_files: dict[str, str] = {}

    if include_aap:
        wait_crds.append('ansibleautomationplatforms.aap.ansible.com')
        aap_cr = (
            (ocp_dir / 'crs' / 'aap-cr.yaml').read_text()
            .replace('${AAP_NAMESPACE}', params.aap_namespace)
        )
        for i, doc in enumerate(_split_yaml_docs(aap_cr)):
            cr_files[f'aap-cr-{i}.yaml'] = doc

    if include_ao:
        wait_crds.append('automationorchestrators.aap.ansible.com')
        wait_crds.append('clusters.postgresql.cnpg.io')
        ao_cr = (
            (ocp_dir / 'crs' / 'ao-cr.yaml').read_text()
            .replace('${AO_NAMESPACE}', params.ao_namespace)
            .replace('${AO_DB_PASSWORD}', ao_db_password)
        )
        for i, doc in enumerate(_split_yaml_docs(ao_cr)):
            cr_files[f'ao-cr-{i}.yaml'] = doc

    if not cr_files:
        return ''

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
    /api/v1:ConfigMap)                                          result=configmaps ;;
    /api/v1:Namespace)                                          result=namespaces ;;
    /api/v1:Secret)                                             result=secrets ;;
    /api/v1:Service)                                            result=services ;;
    /api/v1:ServiceAccount)                                     result=serviceaccounts ;;
    /apis/aap.ansible.com/v1alpha1:AnsibleAutomationPlatform)  result=ansibleautomationplatforms ;;
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

    cm_data = ''
    for fname, content in cr_files.items():
        indented = '\n'.join('    ' + line for line in content.splitlines())
        cm_data += f'  {fname}: |\n{indented}\n'

    script_indented = '\n'.join('          ' + line for line in script.splitlines())

    return f"""\
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


def find_openshift_install() -> Optional[str]:
    if p := os.environ.get('OPENSHIFT_INSTALL_BIN'):
        return p if os.access(p, os.X_OK) else None
    cache = f'/assets/cache/{_OCP_VERSION}-x86_64/openshift-install'
    if os.access(cache, os.X_OK):
        return cache
    return shutil.which('openshift-install')


def find_nmstatectl() -> Optional[str]:
    if p := os.environ.get('NMSTATECTL_BIN'):
        return p if os.access(p, os.X_OK) else None
    return shutil.which('nmstatectl')


def _write_manifests(params: AgentconfigParams, work_dir: pathlib.Path) -> None:
    """Write all manifests into work_dir for consumption by openshift-install."""
    (work_dir / 'install-config.yaml').write_text(generate_install_config(params))
    (work_dir / 'agent-config.yaml').write_text(generate_agent_config(params))

    ocp_out = work_dir / 'openshift'
    ocp_out.mkdir(exist_ok=True)
    for fname, content in generate_operator_manifests(params).items():
        (ocp_out / fname).write_text(content)
    job_yaml = generate_post_install_job(params)
    if job_yaml:
        (ocp_out / 'post-install-crs-job.yaml').write_text(job_yaml)


async def create_iso(params: AgentconfigParams, output_dir: pathlib.Path) -> pathlib.Path:
    """Generate agentconfig.noarch.iso using openshift-install; returns its path.

    openshift-install consumes install-config.yaml and agent-config.yaml from
    a temp dir (it deletes them after reading), so we write manifests there and
    copy the originals to output_dir for reference.
    """
    ocp_install = find_openshift_install()
    if not ocp_install:
        raise RuntimeError(
            'openshift-install not found. Set OPENSHIFT_INSTALL_BIN or mount '
            f'/assets/cache/{_OCP_VERSION}-x86_64/ from a prior Phase 1 build.'
        )
    nmstatectl = find_nmstatectl()
    if not nmstatectl:
        raise RuntimeError(
            'nmstatectl not found. Set NMSTATECTL_BIN or install the nmstate package.'
        )

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = pathlib.Path(tmp)
        _write_manifests(params, work_dir)

        env = os.environ.copy()
        bin_dir = str(pathlib.Path(nmstatectl).parent)
        env['PATH'] = bin_dir + ':' + env.get('PATH', '')

        proc = await asyncio.create_subprocess_exec(
            ocp_install, 'agent', 'create', 'config-image', '--dir', str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f'openshift-install failed (exit {proc.returncode}):\n'
                + stderr.decode(errors='replace')
            )

        iso_src = work_dir / 'agentconfig.noarch.iso'
        if not iso_src.exists():
            raise RuntimeError('openshift-install did not produce agentconfig.noarch.iso')

        iso_dst = output_dir / 'agentconfig.noarch.iso'
        shutil.copy2(iso_src, iso_dst)

        auth_src = work_dir / 'auth'
        if auth_src.is_dir():
            shutil.copytree(auth_src, output_dir / 'auth')

        return iso_dst
