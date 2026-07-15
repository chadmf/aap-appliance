#!/bin/bash
# build-libvirt.sh — Interactive end-to-end AO/AAP appliance build for libvirt/qemu-kvm.
#
# Automates BUILD.md: merge pull secrets, refresh pins, bake the builder image,
# run the appliance build, optionally DHCP-reserve + launch the VM.
#
# Usage:
#   ./scripts/build-libvirt.sh
#   ./scripts/build-libvirt.sh --yes          # accept defaults / env; only prompt if required values missing
#   NONINTERACTIVE=1 ./scripts/build-libvirt.sh --yes
#
# Required inputs (prompted if unset):
#   OPENSHIFT_PULL_SECRET   Path to console.redhat.com pull-secret JSON
#   AAP_QUAY_AUTH           Path to JSON containing auths["quay.io/aap"]
#                           (may be the same file as OPENSHIFT_PULL_SECRET if already merged)
#                           If missing, the script can create one from a Quay robot username/token
#   SSH_PUBKEY              Path to SSH public key
#   BASE_DOMAIN             Cluster base domain
#   QUAY_AAP_USER           Robot username (e.g. aap+myname-pull) — optional; prompted if creating auth
#   QUAY_AAP_TOKEN          Robot token — optional; prompted if creating auth (prefer prompt over env)
#
# Optional (defaults shown):
#   APPLIANCE_CONTENT=ao
#   RENDEZVOUS_IP=192.168.122.100
#   MACHINE_NETWORK=192.168.122.0/24
#   VM_MAC=52:54:00:aa:bb:01
#   OUTPUT_DIR=<repo>/build
#   WORK_DIR=~/aap-appliance-output
#   IMAGE=localhost/aap-appliance:latest
#   UPDATE_PINS=yes
#   LAUNCH_VM=yes
#   RUN_LIBVIRT_PREREQS=ask
#   SKIP_AUTH_CHECK=no
#   DISCONNECTED=true
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: build-libvirt.sh [OPTIONS]

Interactive libvirt/qemu-kvm appliance build (see BUILD.md).

Options:
  --yes, -y              Non-prompting where defaults/env suffice; fail if required missing
  --skip-pins            Do not run update-*-images.sh
  --skip-image-build     Do not podman build the builder image
  --skip-appliance       Do not run the appliance podman build
  --skip-launch          Do not DHCP-reserve / launch the VM
  --launch-only          Only DHCP-reserve + launch (expects OUTPUT_DIR already built)
  --update-pins-only     Only merge auth + refresh pins, then exit
  --help, -h             Show this help

Environment variables override prompts (see script header).
EOF
}

YES=false
SKIP_PINS=false
SKIP_IMAGE_BUILD=false
SKIP_APPLIANCE=false
SKIP_LAUNCH=false
LAUNCH_ONLY=false
UPDATE_PINS_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)            YES=true; shift ;;
        --skip-pins)         SKIP_PINS=true; shift ;;
        --skip-image-build)  SKIP_IMAGE_BUILD=true; shift ;;
        --skip-appliance)    SKIP_APPLIANCE=true; shift ;;
        --skip-launch)       SKIP_LAUNCH=true; shift ;;
        --launch-only)       LAUNCH_ONLY=true; shift ;;
        --update-pins-only)  UPDATE_PINS_ONLY=true; shift ;;
        --help|-h)           usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if [[ "${NONINTERACTIVE:-}" == "1" ]]; then
    YES=true
fi

# --- helpers ---

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# prompt VAR "Question" "default"
prompt() {
    local var="$1" question="$2" default="${3:-}"
    local current="${!var-}"
    if [[ -n "$current" ]]; then
        return 0
    fi
    if [[ "$YES" == "true" ]]; then
        if [[ -n "$default" ]]; then
            printf -v "$var" '%s' "$default"
            return 0
        fi
        die "$var is required (set env or run without --yes)"
    fi
    local reply
    if [[ -n "$default" ]]; then
        read -r -p "$question [$default]: " reply
        printf -v "$var" '%s' "${reply:-$default}"
    else
        while true; do
            read -r -p "$question: " reply
            [[ -n "$reply" ]] && break
            echo "  (required)"
        done
        printf -v "$var" '%s' "$reply"
    fi
}

# prompt_optional VAR "Question" "default"
# Like prompt, but empty input is allowed (including with no default).
prompt_optional() {
    local var="$1" question="$2" default="${3:-}"
    local current="${!var-}"
    if [[ -n "$current" ]]; then
        return 0
    fi
    if [[ "$YES" == "true" ]]; then
        printf -v "$var" '%s' "$default"
        return 0
    fi
    local reply
    if [[ -n "$default" ]]; then
        read -r -p "$question [$default]: " reply
        printf -v "$var" '%s' "${reply:-$default}"
    else
        read -r -p "$question [Enter=create from robot]: " reply
        printf -v "$var" '%s' "$reply"
    fi
}

# prompt_yn VAR "Question" default_y_or_n
prompt_yn() {
    local var="$1" question="$2" default="${3:-y}"
    local current="${!var-}"
    if [[ -n "$current" ]]; then
        case "$current" in
            y|Y|yes|YES|true|TRUE|1) printf -v "$var" 'yes'; return 0 ;;
            n|N|no|NO|false|FALSE|0) printf -v "$var" 'no'; return 0 ;;
        esac
    fi
    if [[ "$YES" == "true" ]]; then
        case "$default" in
            y|Y) printf -v "$var" 'yes' ;;
            *)   printf -v "$var" 'no' ;;
        esac
        return 0
    fi
    local reply hint="y/n"
    [[ "$default" == "y" || "$default" == "Y" ]] && hint="Y/n"
    [[ "$default" == "n" || "$default" == "N" ]] && hint="y/N"
    read -r -p "$question [$hint]: " reply
    reply="${reply:-$default}"
    case "$reply" in
        y|Y|yes|YES) printf -v "$var" 'yes' ;;
        *)           printf -v "$var" 'no' ;;
    esac
}

expand_path() {
    # expand ~ and make absolute when possible
    local p="$1"
    p="${p/#\~/$HOME}"
    if [[ -e "$p" ]]; then
        (cd "$(dirname "$p")" && printf '%s/%s\n' "$(pwd)" "$(basename "$p")")
    else
        printf '%s\n' "$p"
    fi
}

check_tools() {
    local missing=()
    for c in podman skopeo opm jq python3; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c")
    done
    if ((${#missing[@]})); then
        die "missing tools: ${missing[*]}"
    fi
}

free_builder_ports() {
    fuser -k 55000/tcp 2>/dev/null || true
    fuser -k 5005/tcp 2>/dev/null || true
    podman ps -q --filter "ancestor=${IMAGE}" 2>/dev/null | xargs -r podman stop 2>/dev/null || true
}

# --- gather inputs ---

OPENSHIFT_PULL_SECRET="${OPENSHIFT_PULL_SECRET:-}"
AAP_QUAY_AUTH="${AAP_QUAY_AUTH:-}"
SSH_PUBKEY="${SSH_PUBKEY:-}"
BASE_DOMAIN="${BASE_DOMAIN:-}"
APPLIANCE_CONTENT="${APPLIANCE_CONTENT:-ao}"
RENDEZVOUS_IP="${RENDEZVOUS_IP:-192.168.122.100}"
MACHINE_NETWORK="${MACHINE_NETWORK:-192.168.122.0/24}"
VM_MAC="${VM_MAC:-52:54:00:aa:bb:01}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/build}"
WORK_DIR="${WORK_DIR:-$HOME/aap-appliance-output}"
IMAGE="${IMAGE:-localhost/aap-appliance:latest}"
UPDATE_PINS="${UPDATE_PINS:-}"
LAUNCH_VM="${LAUNCH_VM:-}"
RUN_LIBVIRT_PREREQS="${RUN_LIBVIRT_PREREQS:-}"
SKIP_AUTH_CHECK="${SKIP_AUTH_CHECK:-no}"
DISCONNECTED="${DISCONNECTED:-true}"
OCP_RELEASE_REF="${OCP_RELEASE_REF:-quay.io/openshift-release-dev/ocp-release:4.22.0-x86_64}"
AO_INDEX_REF="${AO_INDEX_REF:-quay.io/aap/ansible-automation-platform/automation-orchestrator-operator-index:main}"

# Expand WORK_DIR early so we can detect existing auth files there
WORK_DIR="$(expand_path "$WORK_DIR")"

authfile_has_quay_aap() {
    local f="$1"
    [[ -f "$f" ]] || return 1
    python3 - "$f" <<'PY'
import json, sys
from pathlib import Path
try:
    d = json.loads(Path(sys.argv[1]).read_text())
except Exception:
    sys.exit(1)
auths = d.get("auths") or {}
sys.exit(0 if "quay.io/aap" in auths else 1)
PY
}

# Use first existing file among candidates; set VAR and log. Returns 0 if found.
use_existing_file() {
    local var="$1"
    shift
    local current="${!var-}"
    if [[ -n "$current" && -f "${current/#\~/$HOME}" ]]; then
        printf -v "$var" '%s' "$(expand_path "$current")"
        log "Using $var=${!var}"
        return 0
    fi
    local cand
    for cand in "$@"; do
        [[ -n "$cand" && -f "$cand" ]] || continue
        printf -v "$var" '%s' "$(expand_path "$cand")"
        log "Using existing $var=${!var}"
        return 0
    done
    return 1
}

log "AAP appliance libvirt build"
echo "    repo: $REPO_ROOT"

if [[ "$LAUNCH_ONLY" != "true" ]]; then
    check_tools

    # Secrets: only prompt when nothing usable exists on disk / in env
    if ! use_existing_file OPENSHIFT_PULL_SECRET \
        "$HOME/Downloads/pull-secret.txt" \
        "$HOME/.aap-demo/pull-secret.txt" \
        "$HOME/.aap/pull-secret.txt" \
        "$WORK_DIR/pull-secret-merged.json"
    then
        prompt OPENSHIFT_PULL_SECRET \
            "Path to OpenShift pull secret (console.redhat.com JSON)" \
            ""
    fi

    # Prefer an existing quay.io/aap auth file; otherwise leave empty for ensure_aap_quay_auth
    if [[ -z "${AAP_QUAY_AUTH:-}" ]]; then
        if use_existing_file AAP_QUAY_AUTH \
            "$WORK_DIR/aap-only-auth.json" \
            "$WORK_DIR/pull-secret-merged.json"
        then
            if ! authfile_has_quay_aap "$AAP_QUAY_AUTH"; then
                warn "$AAP_QUAY_AUTH has no quay.io/aap entry; will prompt for robot credentials later"
                AAP_QUAY_AUTH=""
            fi
        elif authfile_has_quay_aap "$OPENSHIFT_PULL_SECRET"; then
            AAP_QUAY_AUTH="$OPENSHIFT_PULL_SECRET"
            log "Using quay.io/aap from OpenShift pull secret"
        fi
    elif [[ -f "${AAP_QUAY_AUTH/#\~/$HOME}" ]]; then
        AAP_QUAY_AUTH="$(expand_path "$AAP_QUAY_AUTH")"
        log "Using AAP_QUAY_AUTH=$AAP_QUAY_AUTH"
    fi

    if ! use_existing_file SSH_PUBKEY \
        "$HOME/.ssh/id_ed25519.pub" \
        "$HOME/.ssh/id_rsa.pub"
    then
        prompt SSH_PUBKEY "Path to SSH public key" ""
    fi

    prompt BASE_DOMAIN "Cluster base domain (e.g. example.com)"
    prompt APPLIANCE_CONTENT "APPLIANCE_CONTENT (aap|ao|aap-with-ao|aap-full)" "ao"
    prompt RENDEZVOUS_IP "Rendezvous IP" "192.168.122.100"
    prompt MACHINE_NETWORK "Machine network CIDR" "192.168.122.0/24"
    prompt VM_MAC "VM MAC (DHCP reservation + launch)" "52:54:00:aa:bb:01"
    prompt OUTPUT_DIR "Build output directory" "$REPO_ROOT/build"
    # WORK_DIR already resolved; allow override only if still default and user wants — keep simple:
    # re-prompt only when env/default path should be confirmed? Skip — already set.

    if [[ "$SKIP_PINS" != "true" && -z "${UPDATE_PINS}" ]]; then
        prompt_yn UPDATE_PINS "Refresh image pins (update-*-images.sh) before build?" "y"
    elif [[ "$SKIP_PINS" == "true" ]]; then
        UPDATE_PINS=no
    fi
else
    prompt OUTPUT_DIR "Build output directory" "$REPO_ROOT/build"
    prompt RENDEZVOUS_IP "Rendezvous IP" "192.168.122.100"
    prompt VM_MAC "VM MAC" "52:54:00:aa:bb:01"
    LAUNCH_VM=yes
fi

if [[ "$LAUNCH_ONLY" != "true" && "$SKIP_LAUNCH" != "true" && -z "${LAUNCH_VM}" ]]; then
    prompt_yn LAUNCH_VM "Launch libvirt VM after a successful build?" "y"
elif [[ "$SKIP_LAUNCH" == "true" ]]; then
    LAUNCH_VM=no
fi

OUTPUT_DIR="$(expand_path "$OUTPUT_DIR")"
# WORK_DIR already absolute

MERGED_AUTH="$WORK_DIR/pull-secret-merged.json"
AAP_ONLY_AUTH="$WORK_DIR/aap-only-auth.json"
CONTAINERS_AUTH_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/containers"
CONTAINERS_AUTH="$CONTAINERS_AUTH_DIR/auth.json"

# --- validate paths ---

if [[ "$LAUNCH_ONLY" != "true" ]]; then
    OPENSHIFT_PULL_SECRET="$(expand_path "$OPENSHIFT_PULL_SECRET")"
    SSH_PUBKEY="$(expand_path "$SSH_PUBKEY")"
    [[ -f "$OPENSHIFT_PULL_SECRET" ]] || die "OpenShift pull secret not found: $OPENSHIFT_PULL_SECRET"
    [[ -f "$SSH_PUBKEY" ]] || die "SSH public key not found: $SSH_PUBKEY"
    if [[ -n "${AAP_QUAY_AUTH:-}" ]]; then
        AAP_QUAY_AUTH="$(expand_path "$AAP_QUAY_AUTH")"
    fi
fi
mkdir -p "$OUTPUT_DIR"
if [[ "$LAUNCH_ONLY" != "true" ]]; then
    mkdir -p "$WORK_DIR" "$CONTAINERS_AUTH_DIR"
fi

# Write WORK_DIR/aap-only-auth.json from Quay robot username + token.
create_aap_robot_auth() {
    local out="${1:-$AAP_ONLY_AUTH}"
    mkdir -p "$(dirname "$out")"

    log "Create quay.io/aap robot auth JSON"
    cat <<'EOF'
  Get credentials from https://quay.io → org "aap" → Robot Accounts
  Username looks like: aap+myname-pull
  Token is the robot password (Read on ansible-automation-platform repos)
EOF

    local user="${QUAY_AAP_USER:-}" token="${QUAY_AAP_TOKEN:-}"
    if [[ "$YES" == "true" ]]; then
        [[ -n "$user" && -n "$token" ]] \
            || die "AAP Quay auth missing: set AAP_QUAY_AUTH to an existing file, or QUAY_AAP_USER + QUAY_AAP_TOKEN with --yes"
    else
        if [[ -z "$user" ]]; then
            read -r -p "  Quay robot username (e.g. aap+myname-pull): " user
        fi
        if [[ -z "$token" ]]; then
            read -r -s -p "  Quay robot token: " token
            echo
        fi
    fi
    [[ -n "$user" && -n "$token" ]] || die "robot username and token are required"

    QUAY_AAP_USER="$user" QUAY_AAP_TOKEN="$token" AAP_ONLY_AUTH="$out" python3 - <<'PY'
import json, base64, os
from pathlib import Path
user, token = os.environ["QUAY_AAP_USER"], os.environ["QUAY_AAP_TOKEN"]
auth = base64.b64encode(f"{user}:{token}".encode()).decode()
path = Path(os.environ["AAP_ONLY_AUTH"])
path.write_text(json.dumps({"auths": {"quay.io/aap": {"auth": auth}}}))
print(f"  wrote {path}")
PY
    unset token
    AAP_QUAY_AUTH="$out"
}

ensure_aap_quay_auth() {
    # Prefer an existing file that already has quay.io/aap — no prompts
    if [[ -n "${AAP_QUAY_AUTH:-}" ]] && authfile_has_quay_aap "$AAP_QUAY_AUTH"; then
        log "Using existing quay.io/aap auth: $AAP_QUAY_AUTH"
        return 0
    fi
    if authfile_has_quay_aap "$OPENSHIFT_PULL_SECRET"; then
        AAP_QUAY_AUTH="$OPENSHIFT_PULL_SECRET"
        log "Using quay.io/aap entry from OpenShift pull secret"
        return 0
    fi
    if [[ -f "${AAP_ONLY_AUTH}" ]] && authfile_has_quay_aap "$AAP_ONLY_AUTH"; then
        AAP_QUAY_AUTH="$AAP_ONLY_AUTH"
        log "Using existing quay.io/aap auth: $AAP_ONLY_AUTH"
        return 0
    fi
    if [[ -f "${MERGED_AUTH}" ]] && authfile_has_quay_aap "$MERGED_AUTH"; then
        AAP_QUAY_AUTH="$MERGED_AUTH"
        log "Using quay.io/aap from merged pull secret: $MERGED_AUTH"
        return 0
    fi

    # Missing — prompt for robot credentials only
    if [[ "$YES" == "true" && -z "${QUAY_AAP_USER:-}" ]]; then
        die "no quay.io/aap auth found. Pass AAP_QUAY_AUTH=... or QUAY_AAP_USER/QUAY_AAP_TOKEN, or run without --yes"
    fi
    create_aap_robot_auth "$AAP_ONLY_AUTH"
}

# Resolve / create quay.io/aap auth before merge
if [[ "$LAUNCH_ONLY" != "true" ]]; then
    ensure_aap_quay_auth
    [[ -f "$AAP_QUAY_AUTH" ]] || die "AAP Quay auth not found: $AAP_QUAY_AUTH"
fi

# --- merge auth files ---

merge_auth() {
    log "Merging pull secrets → $MERGED_AUTH"
    OPENSHIFT_PULL_SECRET="$OPENSHIFT_PULL_SECRET" \
    AAP_QUAY_AUTH="$AAP_QUAY_AUTH" \
    MERGED_AUTH="$MERGED_AUTH" \
    AAP_ONLY_AUTH="$AAP_ONLY_AUTH" \
    python3 - <<'PY'
import json, os, sys
from pathlib import Path

openshift = json.loads(Path(os.environ["OPENSHIFT_PULL_SECRET"]).read_text())
aap_src = json.loads(Path(os.environ["AAP_QUAY_AUTH"]).read_text())

o_auths = openshift.get("auths") or {}
a_auths = aap_src.get("auths") or {}

aap_cred = a_auths.get("quay.io/aap") or o_auths.get("quay.io/aap")
if not aap_cred:
    # allow aap-only file that only has quay.io set to the robot
    if "quay.io" in a_auths and a_auths.get("quay.io") and Path(os.environ["AAP_QUAY_AUTH"]).resolve() != Path(os.environ["OPENSHIFT_PULL_SECRET"]).resolve():
        aap_cred = a_auths["quay.io"]
if not aap_cred:
    sys.exit("error: neither auth file contains auths['quay.io/aap'] "
             "(needed for AO / AAP pre-release). Add a quay.io/aap robot token.")

merged = {"auths": dict(o_auths)}
# Keep OpenShift quay.io; only overlay quay.io/aap
merged["auths"]["quay.io/aap"] = aap_cred

aap_only = {"auths": {"quay.io/aap": aap_cred, "quay.io": aap_cred}}

Path(os.environ["MERGED_AUTH"]).write_text(json.dumps(merged))
Path(os.environ["AAP_ONLY_AUTH"]).write_text(json.dumps(aap_only))
print("  merged keys:", sorted(merged["auths"]))
print("  wrote", os.environ["MERGED_AUTH"])
print("  wrote", os.environ["AAP_ONLY_AUTH"])
PY
}

auth_checks() {
    case "$SKIP_AUTH_CHECK" in y|Y|yes|YES|true|TRUE|1) return 0 ;; esac
    log "Checking registry auth (skopeo)"
    if skopeo inspect --authfile "$MERGED_AUTH" "docker://$OCP_RELEASE_REF" >/dev/null; then
        echo "  OCP: ok"
    else
        die "cannot pull $OCP_RELEASE_REF with merged secret — OpenShift quay.io creds missing/wrong"
    fi
    if skopeo inspect --authfile "$MERGED_AUTH" "docker://$AO_INDEX_REF" >/dev/null; then
        echo "  AO:  ok"
    else
        warn "cannot pull AO index with merged secret; pin update / AO mirror may fail"
        if [[ "$YES" != "true" ]]; then
            prompt_yn _cont "Continue anyway?" "n"
            [[ "$_cont" == "yes" ]] || exit 1
        fi
    fi
}

update_pins() {
    log "Refreshing image pins"
    case "$APPLIANCE_CONTENT" in
        aap|aap-with-ao|aap-full)
            log "update-aap-images.sh"
            "$REPO_ROOT/scripts/update-aap-images.sh" --authfile "$MERGED_AUTH"
            ;;
    esac
    case "$APPLIANCE_CONTENT" in
        ao|aap-with-ao|aap-full)
            log "update-ao-images.sh (AAP-only auth + containers/auth.json)"
            cp "$AAP_ONLY_AUTH" "$CONTAINERS_AUTH"
            "$REPO_ROOT/scripts/update-ao-images.sh" --authfile "$AAP_ONLY_AUTH"
            # restore merged creds for host tooling / subsequent pulls
            cp "$MERGED_AUTH" "$CONTAINERS_AUTH"
            ;;
    esac
}

build_image() {
    log "Building builder image: $IMAGE"
    (cd "$REPO_ROOT" && podman build -t "$IMAGE" .)
}

build_appliance() {
    free_builder_ports
    log "Building appliance → $OUTPUT_DIR"
    echo "    content=$APPLIANCE_CONTENT domain=$BASE_DOMAIN ip=$RENDEZVOUS_IP"
    # shellcheck disable=SC2086
    podman run --rm --privileged --net=host \
        -e "BASE_DOMAIN=$BASE_DOMAIN" \
        -e "RENDEZVOUS_IP=$RENDEZVOUS_IP" \
        -e "MACHINE_NETWORK=$MACHINE_NETWORK" \
        -e "APPLIANCE_CONTENT=$APPLIANCE_CONTENT" \
        -e "DISCONNECTED=$DISCONNECTED" \
        -v "$MERGED_AUTH:/run/secrets/pull-secret:Z" \
        -v "$SSH_PUBKEY:/run/secrets/ssh-key:Z" \
        -v "$OUTPUT_DIR:/assets:Z" \
        "$IMAGE"
}

ensure_libvirt() {
    if command -v virsh >/dev/null 2>&1 && sudo virsh net-info default >/dev/null 2>&1; then
        sudo virsh net-start default 2>/dev/null || true
        return 0
    fi
    if [[ -z "${RUN_LIBVIRT_PREREQS}" ]]; then
        prompt_yn RUN_LIBVIRT_PREREQS "libvirt default network not ready. Run libvirt-prereqs.sh?" "y"
    fi
    if [[ "${RUN_LIBVIRT_PREREQS}" == "yes" ]]; then
        "$REPO_ROOT/scripts/libvirt-prereqs.sh"
    else
        die "libvirt is required to launch the VM"
    fi
}

launch_vm() {
    ensure_libvirt
    local iso="$OUTPUT_DIR/appliance.iso"
    local cfg="$OUTPUT_DIR/cluster-config/agentconfig.noarch.iso"
    [[ -f "$iso" ]] || die "missing $iso — build the appliance first"
    [[ -f "$cfg" ]] || die "missing $cfg — build the appliance first"

    log "DHCP reserve $VM_MAC → $RENDEZVOUS_IP"
    "$REPO_ROOT/scripts/dhcp-reserve.sh" --mac "$VM_MAC" --ip "$RENDEZVOUS_IP"

    log "Launching libvirt VM (virt-manager domain: aap-appliance)"
    VM_MAC="$VM_MAC" RENDEZVOUS_IP="$RENDEZVOUS_IP" \
        "$REPO_ROOT/scripts/launch-appliance.sh" --output-dir "$OUTPUT_DIR" --replace

    cat <<EOF

Appliance VM launching.

  Console:  virt-manager  (or: sudo virt-viewer aap-appliance)
  Monitor:  ssh core@${RENDEZVOUS_IP} sudo journalctl -fu assisted-service
  Kubeconfig (after install):
            export KUBECONFIG=${OUTPUT_DIR}/cluster-config/auth/kubeconfig
EOF
}

# --- main ---

cd "$REPO_ROOT"

if [[ "$LAUNCH_ONLY" == "true" ]]; then
    launch_vm
    exit 0
fi

merge_auth
cp "$MERGED_AUTH" "$CONTAINERS_AUTH"
auth_checks

if [[ "$UPDATE_PINS_ONLY" == "true" ]]; then
    update_pins
    log "Pins updated. Review with: git diff"
    exit 0
fi

if [[ "${UPDATE_PINS:-no}" == "yes" ]]; then
    update_pins
fi

if [[ "$SKIP_IMAGE_BUILD" != "true" ]]; then
    # Always rebuild after pin updates; otherwise ask if image missing
    if [[ "${UPDATE_PINS:-no}" == "yes" ]] || ! podman image exists "$IMAGE" 2>/dev/null; then
        build_image
    else
        _rebuild=""
        prompt_yn _rebuild "Rebuild builder image $IMAGE?" "y"
        [[ "$_rebuild" == "yes" ]] && build_image
    fi
fi

if [[ "$SKIP_APPLIANCE" != "true" ]]; then
    # Offer to clear corrupt cache
    if [[ -d "$OUTPUT_DIR/cache" ]]; then
        _wipe=""
        if [[ "$YES" == "true" ]]; then
            _wipe=no
        else
            prompt_yn _wipe "Wipe existing mirror cache under $OUTPUT_DIR/cache? (only if prior run was killed mid-copy)" "n"
        fi
        if [[ "$_wipe" == "yes" ]]; then
            rm -rf "$OUTPUT_DIR/cache"
            log "Cleared $OUTPUT_DIR/cache"
        fi
    fi
    build_appliance
fi

if [[ "${LAUNCH_VM:-no}" == "yes" ]]; then
    launch_vm
else
    cat <<EOF

Build finished.

  ISO:      ${OUTPUT_DIR}/appliance.iso
  Config:   ${OUTPUT_DIR}/cluster-config/agentconfig.noarch.iso
  Launch:   ./scripts/build-libvirt.sh --launch-only --yes \\
              OUTPUT_DIR=${OUTPUT_DIR} RENDEZVOUS_IP=${RENDEZVOUS_IP} VM_MAC=${VM_MAC}
            or see BUILD.md
EOF
fi
