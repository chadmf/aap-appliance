#!/bin/bash
# update-aap-images.sh — Re-pins all AAP image digests in config/aap-images.yaml
# and the CatalogSource image in assets/openshift/aap.yaml.
#
# Parses relatedImages from the operator-index catalog.json directly — the catalog
# already references quay.io/aap/ paths, so no registry translation is needed.
#
# Run this whenever the operator bundle/index tags move (i.e. before each rebuild).
#
# Usage:
#   ./scripts/update-aap-images.sh --authfile /path/to/pull-secret.json
#
# Requirements: podman, skopeo, python3
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INDEX_TAG="registry.redhat.io/redhat/redhat-operator-index:v4.22"
AUTHFILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --authfile) AUTHFILE="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$AUTHFILE" ]]; then
    echo "error: --authfile is required" >&2
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Resolving index digest ..."
INDEX_DIGEST=$(skopeo inspect --authfile "$AUTHFILE" "docker://$INDEX_TAG" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['Digest'])")
echo "    index:  $INDEX_DIGEST"

INDEX_REF="registry.redhat.io/redhat/redhat-operator-index@$INDEX_DIGEST"

echo "==> Extracting catalog.json from index ..."
podman run --rm --entrypoint cat \
    --authfile "$AUTHFILE" \
    "$INDEX_REF" \
    /configs/ansible-automation-platform-operator/catalog.json \
    > "$TMPDIR/catalog.json"

echo "==> Parsing catalog for stable-2.7 head bundle and relatedImages ..."
python3 - "$INDEX_REF" "$TMPDIR/catalog.json" \
         "$REPO_ROOT/config/aap-images.yaml" "$REPO_ROOT/assets/openshift/aap.yaml" <<'PYEOF'
import sys, re, json, pathlib

index_ref    = sys.argv[1]
catalog_file = pathlib.Path(sys.argv[2])
images_yaml  = pathlib.Path(sys.argv[3])
aap_yaml     = pathlib.Path(sys.argv[4])

CHANNEL = "stable-2.7"

# Parse catalog.json — pretty-printed multi-object JSON (not NDJSON)
catalog_text = catalog_file.read_text()
objects = []
decoder = json.JSONDecoder()
pos = 0
while pos < len(catalog_text):
    while pos < len(catalog_text) and catalog_text[pos] in ' \t\n\r':
        pos += 1
    if pos >= len(catalog_text):
        break
    obj, end = decoder.raw_decode(catalog_text, pos)
    objects.append(obj)
    pos = end

# Find the channel object
channel_obj = next(
    (o for o in objects
     if o.get("schema") == "olm.channel"
     and o.get("name") == CHANNEL
     and o.get("package") == "ansible-automation-platform-operator"),
    None
)
if channel_obj is None:
    print(f"ERROR: channel {CHANNEL} not found in catalog", file=sys.stderr)
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
print(f"    bundle ref:   {bundle_ref}")

related = bundle_obj.get("relatedImages", [])
images = {entry["image"] for entry in related if "image" in entry}
images.add(bundle_ref)
print(f"    images to cache: {len(images)}")

# Write config/aap-images.yaml
lines = [
    "# AAP operator images to pre-cache in the appliance registry.",
    "# Managed by scripts/update-aap-images.sh — do not edit manually.",
    "# BEGIN AAP IMAGES",
    f"- name: {index_ref}",
]
for img in sorted(images):
    lines.append(f"- name: {img}")
lines.append("# END AAP IMAGES")
images_yaml.write_text("\n".join(lines) + "\n")
print(f"    updated {images_yaml}")

# Update CatalogSource image digest and startingCSV in Subscription (aap.yaml)
local_index_ref = (
    "registry.appliance.openshift.com:22625/redhat/redhat-operator-index@"
    + index_ref.split("@")[1]
)
content = aap_yaml.read_text()
content = re.sub(
    r'  image: registry\.appliance\.openshift\.com:22625/redhat/redhat-operator-index@sha256:\S+',
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
