#!/bin/bash
# update-aap-images.sh — Re-pins all AAP image digests and CatalogSource digest in aap.yaml.
#
# Default (released): pulls from registry.redhat.io/redhat/redhat-operator-index:v4.22,
#   updates config/aap-images.yaml and assets/openshift/aap.yaml.
#
# Pre-release (--prerelease): pulls from quay.io/aap/ansible-automation-platform/operator-index:2.7-next,
#   updates config/aap-images-prerelease.yaml and assets/openshift/aap-prerelease.yaml.
#
# Run before each rebuild to pick up the latest digests.
#
# Usage:
#   ./scripts/update-aap-images.sh --authfile /path/to/pull-secret.json [--prerelease]
#
# Requirements: opm, skopeo, python3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTHFILE=""
PRERELEASE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --authfile)  AUTHFILE="$2"; shift 2 ;;
        --prerelease) PRERELEASE=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$AUTHFILE" ]]; then
    echo "error: --authfile is required" >&2
    exit 1
fi

if [[ "$PRERELEASE" == "true" ]]; then
    INDEX_TAG="quay.io/aap/ansible-automation-platform/operator-index:2.7-next"
    IMAGES_YAML="$REPO_ROOT/config/aap-images-prerelease.yaml"
    AAP_YAML="$REPO_ROOT/assets/openshift/aap-prerelease.yaml"
    LOCAL_REGISTRY_PREFIX="registry.appliance.openshift.com:22625/aap/ansible-automation-platform/operator-index@"
    IMAGES_YAML_HEADER="# AAP operator images to pre-cache in the appliance registry (pre-release / quay.io/aap).
# Managed by scripts/update-aap-images.sh --prerelease — do not edit manually."
    echo "==> Mode: pre-release (quay.io/aap)"
else
    INDEX_TAG="registry.redhat.io/redhat/redhat-operator-index:v4.22"
    IMAGES_YAML="$REPO_ROOT/config/aap-images.yaml"
    AAP_YAML="$REPO_ROOT/assets/openshift/aap.yaml"
    LOCAL_REGISTRY_PREFIX="registry.appliance.openshift.com:22625/redhat/redhat-operator-index@"
    IMAGES_YAML_HEADER="# AAP operator images to pre-cache in the appliance registry.
# Managed by scripts/update-aap-images.sh — do not edit manually."
    echo "==> Mode: released (registry.redhat.io)"
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Resolving index digest ..."
INDEX_DIGEST="$(skopeo inspect --format '{{.Digest}}' --authfile "$AUTHFILE" "docker://$INDEX_TAG")"
echo "    index:  $INDEX_DIGEST"

INDEX_REF="${INDEX_TAG%:*}@$INDEX_DIGEST"

echo "==> Rendering AAP catalog entries from index ..."
REGISTRY_AUTH_FILE="$AUTHFILE" opm render "$INDEX_REF" --output=json \
    | jq -c 'select(.package == "ansible-automation-platform-operator" or (.schema == "olm.package" and .name == "ansible-automation-platform-operator"))' \
    > "$TMPDIR/catalog.ndjson"

echo "==> Parsing catalog ..."
python3 - "$INDEX_REF" "$TMPDIR/catalog.ndjson" \
         "$IMAGES_YAML" "$AAP_YAML" "$LOCAL_REGISTRY_PREFIX" "$IMAGES_YAML_HEADER" <<'PYEOF'
import sys, re, json, pathlib

index_ref             = sys.argv[1]
catalog_file          = pathlib.Path(sys.argv[2])
images_yaml           = pathlib.Path(sys.argv[3])
aap_yaml              = pathlib.Path(sys.argv[4])
local_registry_prefix = sys.argv[5]
images_yaml_header    = sys.argv[6]

CHANNEL = "stable-2.7"

# Parse NDJSON output from opm render (one JSON object per line)
objects = [json.loads(line) for line in catalog_file.read_text().splitlines() if line.strip()]

# Find the channel object
channel_obj = next(
    (o for o in objects
     if o.get("schema") == "olm.channel"
     and o.get("name") == CHANNEL
     and o.get("package") == "ansible-automation-platform-operator"),
    None
)
if channel_obj is None:
    available = [o.get("name") for o in objects if o.get("schema") == "olm.channel"
                 and o.get("package") == "ansible-automation-platform-operator"]
    print(f"ERROR: channel {CHANNEL} not found in catalog. Available: {available}", file=sys.stderr)
    sys.exit(1)

entries = channel_obj.get("entries", [])
# Head is the entry that no other entry replaces
replaced = {e.get("replaces") for e in entries if e.get("replaces")}
heads = [e["name"] for e in entries if e["name"] not in replaced]
if len(heads) != 1:
    heads = [entries[-1]["name"]]  # fallback: take the last listed entry
head_name = heads[0]
print(f"    channel head: {head_name}")

# Find the bundle object for that head
bundle_obj = next(
    (o for o in objects
     if o.get("schema") == "olm.bundle"
     and o.get("name") == head_name),
    None
)
if bundle_obj is None:
    print(f"ERROR: bundle {head_name} not found in catalog", file=sys.stderr)
    sys.exit(1)

bundle_ref = bundle_obj["image"]

related = bundle_obj.get("relatedImages", [])
images = {entry["image"] for entry in related if "image" in entry}
images.add(bundle_ref)
print(f"    images: {len(images)}")

# Write images yaml
lines = images_yaml_header.splitlines()
lines += [
    "# BEGIN AAP IMAGES",
    f"- name: {index_ref}",
]
for img in sorted(images):
    lines.append(f"- name: {img}")
lines.append("# END AAP IMAGES")
images_yaml.write_text("\n".join(lines) + "\n")
print(f"    updated {images_yaml}")

# Update CatalogSource image digest and startingCSV in aap yaml
local_index_ref = local_registry_prefix + index_ref.split("@")[1]
content = aap_yaml.read_text()
content = re.sub(
    r'  image: ' + re.escape(local_registry_prefix) + r'sha256:\S+',
    f'  image: {local_index_ref}',
    content
)
if re.search(r'  startingCSV:', content):
    content = re.sub(r'  startingCSV: \S+', f'  startingCSV: {head_name}', content)
aap_yaml.write_text(content)
print(f"    updated {aap_yaml} (startingCSV: {head_name})")
PYEOF

echo ""
echo "Done. Review the changes with: git diff"
echo "Then rebuild:"
echo "  sudo podman build -t localhost/aap-appliance:latest ."
