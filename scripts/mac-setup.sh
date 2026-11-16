#!/usr/bin/env bash
# Adapted from ~/Downloads/mac-setup.sh for a drive whose name contains a space
# (/Volumes/CRUCIAL 525GB). Lima/Colima mishandle spaces in paths, so the real
# data lives on the drive and everything is addressed through a space-free symlink
# (/Users/Shared/ksc-cx -> "<drive>/cx"). The symlink itself is a few bytes.
# Usage: scripts/mac-setup.sh [drive]   (safe to re-run)
set -euo pipefail
DRIVE="${1:-/Volumes/CRUCIAL 525GB - Data}"  # writable Data volume; "CRUCIAL 525GB" itself is a read-only system volume
LINK="/Users/Shared/ksc-cx"
say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

[ "$(uname -s)" = Darwin ] || die "macOS only"
[ -d "$DRIVE" ] || die "drive not mounted: $DRIVE"
command -v brew >/dev/null || die "Homebrew not found"

say "Checking the drive"
INFO="$(diskutil info "$DRIVE")"
FS="$(printf '%s\n' "$INFO" | awk -F: '/File System Personality/ {sub(/^ +/,"",$2); print $2}')"
echo "File system: ${FS:-unknown}"
case "$FS" in *APFS*|*"Mac OS Extended"*|*HFS*) ;; *) die "drive must be APFS/HFS+ (found: ${FS:-unknown})";; esac
printf '%s\n' "$INFO" | grep -q "Solid State: *Yes" || warn "drive does not report as an SSD"
FREE_GB=$(df -g "$DRIVE" | awk 'NR==2{print $4}')
echo "Free on drive: ${FREE_GB} GB"
[ "${FREE_GB:-0}" -ge 40 ] || die "need at least 40 GB free on the drive"

REAL="$DRIVE/Users/Codex/cx"
mkdir -p "$REAL"/{colima,kube,cache/{uv,npm,homebrew,helm,terraform-plugins,kube,xdg},tools/{uv-python,uv-tools,uv-bin},helm/{config,data}}
[ -L "$LINK" ] || { [ -e "$LINK" ] && die "$LINK exists and is not a symlink"; ln -s "$REAL" "$LINK"; }
[ "$(readlink "$LINK")" = "$REAL" ] || die "$LINK points to $(readlink "$LINK"), expected $REAL"
CX="$LINK"
tmutil addexclusion "$REAL" 2>/dev/null || true

say "Writing $REAL/env.sh"
cat > "$REAL/env.sh" <<ENVEOF
# source this in every terminal before running make or claude for k8s-sandbox-control-plane
export CX="$CX"
export COLIMA_HOME="\$CX/colima"
export XDG_CACHE_HOME="\$CX/cache/xdg"
export UV_CACHE_DIR="\$CX/cache/uv"
export UV_PYTHON_INSTALL_DIR="\$CX/tools/uv-python"
export UV_TOOL_DIR="\$CX/tools/uv-tools"
export UV_TOOL_BIN_DIR="\$CX/tools/uv-bin"
export npm_config_cache="\$CX/cache/npm"
export HOMEBREW_CACHE="\$CX/cache/homebrew"
export HELM_CACHE_HOME="\$CX/cache/helm"
export HELM_CONFIG_HOME="\$CX/helm/config"
export HELM_DATA_HOME="\$CX/helm/data"
export TF_PLUGIN_CACHE_DIR="\$CX/cache/terraform-plugins"
export KUBECONFIG="\$CX/kube/config"
export KUBECACHEDIR="\$CX/cache/kube"
export PATH="\$UV_TOOL_BIN_DIR:\$PATH"
ENVEOF
# shellcheck disable=SC1091
source "$REAL/env.sh"

say "Installing missing CLI tools with Homebrew"
MISSING=()
for t in colima kind trivy kubeconform; do command -v "$t" >/dev/null || MISSING+=("$t"); done
[ ${#MISSING[@]} -eq 0 ] || brew install "${MISSING[@]}"
command -v docker >/dev/null || brew install docker

say "Python 3.12 and checkov (on the drive)"
uv python install 3.12
command -v checkov >/dev/null || uv tool install checkov || warn "checkov install failed; retry later"

say "Starting Colima (VM disk on the drive)"
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
MEM=8; [ "$RAM_GB" -ge 16 ] || MEM=$(( RAM_GB / 2 ))
CPUS=4; [ "$(sysctl -n hw.ncpu)" -ge 8 ] || CPUS=$(( $(sysctl -n hw.ncpu) / 2 ))
VMT=(); [ "$(uname -m)" = arm64 ] && VMT=(--vm-type vz)
echo "Mac RAM ${RAM_GB} GB -> VM: ${CPUS} CPU, ${MEM} GB, 60 GB sparse disk"
colima status >/dev/null 2>&1 || colima start --cpu "$CPUS" --memory "$MEM" --disk 60 --runtime docker "${VMT[@]}"
docker context use colima >/dev/null

say "Verifying"
[ "$(docker info --format '{{.CgroupVersion}}')" = 2 ] && echo "cgroup v2: ok" || die "not cgroup v2"
docker run --rm hello-world >/dev/null && echo "docker run: ok"
[ -d "$COLIMA_HOME/_lima" ] && echo "VM files on the drive: ok" || warn "no _lima under $COLIMA_HOME"
[ ! -e "$HOME/.colima" ] && echo "no ~/.colima: ok" || warn "~/.colima exists"
echo
echo "Done. In every terminal: source $REAL/env.sh   (or: source $LINK/env.sh)"
