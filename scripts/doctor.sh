#!/usr/bin/env bash
# make doctor: storage + toolchain preflight (HANDOFF Addendum A). Fails loudly.
FAIL=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
warn() { printf '  warn  %s\n' "$*"; }

echo "== drive and env"
if [ -z "${CX:-}" ]; then bad "CX is not set (source \$CX/env.sh first)"; elif [ -d "$CX" ]; then ok "CX=$CX exists"; else bad "CX=$CX does not exist (drive unmounted?)"; fi
for v in COLIMA_HOME UV_CACHE_DIR npm_config_cache HOMEBREW_CACHE TF_PLUGIN_CACHE_DIR HELM_CACHE_HOME KUBECONFIG KUBECACHEDIR; do
  val="$(printenv "$v" || true)"
  if [ -z "$val" ]; then bad "$v is unset"
  elif [ -n "${CX:-}" ] && case "$val" in "$CX"/*) true;; *) false;; esac; then ok "$v under CX"
  else bad "$v=$val is not under \$CX"; fi
done

echo "== container runtime"
if docker info >/dev/null 2>&1; then
  [ "$(docker info --format '{{.CgroupVersion}}')" = 2 ] && ok "cgroup v2" || bad "docker is not cgroup v2"
  ctx="$(docker context show 2>/dev/null)"
  case "$ctx" in colima*) ok "docker context $ctx";; *) bad "docker context is '$ctx', expected colima";; esac
else bad "docker daemon not reachable (colima start?)"; fi
[ ! -e "$HOME/.colima" ] && ok "no ~/.colima" || bad "~/.colima exists on internal disk"

echo "== tools"
for t in colima docker kind kubectl helm uv node gh jq terraform tflint trivy kubeconform gitleaks; do
  command -v "$t" >/dev/null && ok "$t" || bad "$t missing"
done

echo "== free space"
dfree() { df -g "$1" | awk 'NR==2{print $4}'; }
if [ -n "${CX:-}" ] && [ -d "$CX" ]; then d=$(dfree "$CX"); [ "$d" -gt 15 ] && ok "drive ${d} GB free" || bad "drive only ${d} GB free (need >15)"; fi
i=$(dfree /); [ "$i" -gt 3 ] && ok "internal ${i} GB free" || warn "internal only ${i} GB free (want >3)"

echo "== internal-disk spots"
du -sh "$HOME/Library/Caches" "$HOME/.docker" "$HOME/.cache" "$HOME/.npm" 2>/dev/null || true

[ "$FAIL" = 0 ] && echo "doctor: PASS" || { echo "doctor: FAIL"; exit 1; }
