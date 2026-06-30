#!/bin/bash
# update-ao-images.sh — Re-pins all AO and CloudNativePG image digests.
#
# AO (always pre-release): pulls from quay.io/aap/ansible-automation-platform/automation-orchestrator-operator-index:main,
#   updates config/ao-images-prerelease.yaml and assets/openshift/ao-prerelease.yaml.
#
# CloudNativePG: extracted from quay.io/operatorhubio/catalog (OperatorHub community catalog).
#   package: cloudnative-pg, channel: stable-v1.
#   The certified-operator-index and community-operator-index for OCP 4.22 do not carry
#   this package; the Hypershift "certified-operators" gRPC service aggregates it from a
#   different source. For standalone SNO, the OperatorHub catalog is the correct OCI source.
#   Its images and the ghcr.io postgres image are merged into config/ao-images-prerelease.yaml.
#
# Run before each rebuild to pick up the latest digests.
#
# Usage:
#   ./scripts/update-ao-images.sh --authfile /path/to/pull-secret.json
#
# Requirements: opm, skopeo, python3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTHFILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --authfile)  AUTHFILE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$AUTHFILE" ]]; then
    echo "error: --authfile is required" >&2
    exit 1
fi

AO_INDEX_TAG="quay.io/aap/ansible-automation-platform/automation-orchestrator-operator-index:main"
RH_INDEX_TAG="quay.io/operatorhubio/catalog:latest"
CNPG_POSTGRES_TAG="ghcr.io/cloudnative-pg/postgresql:15"

AO_YAML="$REPO_ROOT/assets/openshift/ao-prerelease.yaml"
AO_CR_YAML="$REPO_ROOT/assets/openshift/crs/ao-cr.yaml"
IMAGES_YAML="$REPO_ROOT/config/ao-images-prerelease.yaml"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# --- AO operator index ---

echo "==> Resolving AO operator index digest ..."
AO_INDEX_DIGEST="$(skopeo inspect --format '{{.Digest}}' --authfile "$AUTHFILE" "docker://$AO_INDEX_TAG")"
echo "    index:  $AO_INDEX_DIGEST"

AO_INDEX_REF="${AO_INDEX_TAG%:*}@$AO_INDEX_DIGEST"
AO_LOCAL_PREFIX="registry.appliance.openshift.com:22625/aap/ansible-automation-platform/automation-orchestrator-operator-index@"

echo "==> Rendering AO catalog entries from index ..."
REGISTRY_AUTH_FILE="$AUTHFILE" opm render "$AO_INDEX_REF" --output=json \
    | jq -c 'select(.package == "automation-orchestrator-operator" or (.schema == "olm.package" and .name == "automation-orchestrator-operator"))' \
    > "$TMPDIR/ao-catalog.ndjson"

# --- OperatorHub community catalog (for CloudNativePG) ---

echo "==> Resolving OperatorHub catalog digest (for CloudNativePG) ..."
RH_INDEX_DIGEST="$(skopeo inspect --format '{{.Digest}}' --override-os linux --override-arch amd64 "docker://$RH_INDEX_TAG")"
echo "    index:  $RH_INDEX_DIGEST"

RH_INDEX_REF="${RH_INDEX_TAG%:*}@$RH_INDEX_DIGEST"
RH_LOCAL_PREFIX="registry.appliance.openshift.com:22625/operatorhubio/catalog@"

echo "==> Rendering CloudNativePG catalog entries from OperatorHub catalog ..."
opm render "$RH_INDEX_REF" --output=json \
    | jq -c 'select(.package == "cloudnative-pg" or (.schema == "olm.package" and .name == "cloudnative-pg"))' \
    > "$TMPDIR/cnpg-catalog.ndjson"

# --- CloudNativePG postgres image ---

echo "==> Resolving CloudNativePG postgres image digest ..."
CNPG_POSTGRES_DIGEST="$(skopeo inspect --format '{{.Digest}}' "docker://$CNPG_POSTGRES_TAG")"
CNPG_POSTGRES_REF="${CNPG_POSTGRES_TAG%:*}:${CNPG_POSTGRES_TAG##*:}@$CNPG_POSTGRES_DIGEST"
echo "    image:  $CNPG_POSTGRES_REF"

# --- Parse both catalogs, write image list and update manifests ---

echo "==> Parsing catalogs ..."
python3 - \
    "$AO_INDEX_REF" "$TMPDIR/ao-catalog.ndjson" \
    "$RH_INDEX_REF" "$TMPDIR/cnpg-catalog.ndjson" \
    "$CNPG_POSTGRES_REF" \
    "$IMAGES_YAML" "$AO_YAML" "$AO_CR_YAML" \
    "$AO_LOCAL_PREFIX" "$RH_LOCAL_PREFIX" <<'PYEOF'
import sys, re, json, pathlib

ao_index_ref          = sys.argv[1]
ao_catalog_file       = pathlib.Path(sys.argv[2])
rh_index_ref          = sys.argv[3]
cnpg_catalog_file     = pathlib.Path(sys.argv[4])
cnpg_postgres_ref     = sys.argv[5]
images_yaml           = pathlib.Path(sys.argv[6])
ao_yaml               = pathlib.Path(sys.argv[7])
ao_cr_yaml            = pathlib.Path(sys.argv[8])
ao_local_prefix       = sys.argv[9]
rh_local_prefix       = sys.argv[10]

AO_PACKAGE   = "automation-orchestrator-operator"
AO_CHANNEL   = "candidate"
CNPG_PACKAGE = "cloudnative-pg"
CNPG_CHANNEL = "stable-v1"


def parse_ndjson(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rewrite_ao_image(img):
    # PRE-RELEASE WORKAROUND: the CSV references registry.redhat.io but that registry
    # requires entitlements not available for pre-release builds. The same content is
    # accessible at quay.io/aap. Rewrite so oc-mirror pulls from there; the IDMS entry
    # for registry.redhat.io/ansible-automation-platform/automation-orchestrator in
    # idms-ao-prerelease.yaml handles the redirect at runtime.
    # AT GA: remove this function and its call sites; images will be pullable directly
    # from registry.redhat.io with a standard pull secret.
    OLD = "registry.redhat.io/ansible-automation-platform/automation-orchestrator"
    NEW = "quay.io/aap/ansible-automation-platform/automation-orchestrator"
    return img.replace(OLD, NEW, 1) if img.startswith(OLD) else img


def find_channel_head(objects, package, channel):
    channels = [
        o for o in objects
        if o.get("schema") == "olm.channel"
        and o.get("package") == package
        and o.get("name") == channel
    ]
    if not channels:
        available = [
            o.get("name") for o in objects
            if o.get("schema") == "olm.channel" and o.get("package") == package
        ]
        print(f"ERROR: channel '{channel}' not found for '{package}'. Available: {available}", file=sys.stderr)
        sys.exit(1)
    ch = channels[0]
    entries = ch.get("entries", [])
    replaced = {e.get("replaces") for e in entries if e.get("replaces")}
    heads = [e["name"] for e in entries if e["name"] not in replaced]
    head_name = heads[0] if len(heads) == 1 else entries[-1]["name"]
    bundle_obj = next(
        (o for o in objects if o.get("schema") == "olm.bundle" and o.get("name") == head_name),
        None,
    )
    if bundle_obj is None:
        print(f"ERROR: bundle '{head_name}' not found in catalog", file=sys.stderr)
        sys.exit(1)
    return head_name, bundle_obj


# AO
ao_objects = parse_ndjson(ao_catalog_file)
ao_head, ao_bundle = find_channel_head(ao_objects, AO_PACKAGE, AO_CHANNEL)
print(f"    AO channel head: {ao_head}")
ao_images = {rewrite_ao_image(e["image"]) for e in ao_bundle.get("relatedImages", []) if "image" in e}
ao_images.add(rewrite_ao_image(ao_bundle["image"]))
ao_images.add(ao_index_ref)

# CNPG
cnpg_objects = parse_ndjson(cnpg_catalog_file)
cnpg_head, cnpg_bundle = find_channel_head(cnpg_objects, CNPG_PACKAGE, CNPG_CHANNEL)
print(f"    CNPG channel head: {cnpg_head}")
cnpg_images = {e["image"] for e in cnpg_bundle.get("relatedImages", []) if "image" in e}
cnpg_images.add(cnpg_bundle["image"])
cnpg_images.add(rh_index_ref)
cnpg_images.add(cnpg_postgres_ref)

# Merged image list
all_images = sorted(ao_images | cnpg_images)
print(f"    images: {len(all_images)}")

header = (
    "# AO operator images + CloudNativePG images to pre-cache in the appliance registry (pre-release / quay.io/aap).\n"
    "# Managed by scripts/update-ao-images.sh — do not edit manually."
)
lines = header.splitlines() + ["# BEGIN AO IMAGES"]
for img in all_images:
    lines.append(f"- name: {img}")
lines.append("# END AO IMAGES")
images_yaml.write_text("\n".join(lines) + "\n")
print(f"    updated {images_yaml}")

# Update ao-prerelease.yaml: AO CatalogSource digest + startingCSV + redhat-operator-index digest
content = ao_yaml.read_text()
content = re.sub(
    r'(  image: ' + re.escape(ao_local_prefix) + r')sha256:\S+',
    r'\g<1>' + ao_index_ref.split("@")[1],
    content,
)
content = re.sub(
    r'(  image: ' + re.escape(rh_local_prefix) + r')sha256:\S+',
    r'\g<1>' + rh_index_ref.split("@")[1],
    content,
)  # cs-operatorhub CatalogSource digest
content = re.sub(r'  startingCSV: \S+', f'  startingCSV: {ao_head}', content, count=1)
ao_yaml.write_text(content)
print(f"    updated {ao_yaml} (startingCSV: {ao_head})")

# Update ao-cr.yaml: pin the CNPG postgres image digest
cr_content = ao_cr_yaml.read_text()
# Match the imageName line regardless of current digest value (tag:digest or tag or placeholder)
cr_content = re.sub(
    r'(  imageName: ghcr\.io/cloudnative-pg/postgresql:15)(?:@sha256:\S+)?',
    r'\1@' + cnpg_postgres_ref.split("@")[1],
    cr_content,
)
ao_cr_yaml.write_text(cr_content)
print(f"    updated {ao_cr_yaml} (postgres: {cnpg_postgres_ref})")
PYEOF

echo ""
echo "Done. Review the changes with: git diff"
echo "Then rebuild:"
echo "  sudo podman build -t localhost/aap-appliance:latest ."
