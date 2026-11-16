#!/usr/bin/env bash
# Create (or reuse) the kind cluster, install Calico, wait until Ready.
set -euo pipefail
cd "$(dirname "$0")/../.."
WORKERS="${WORKERS:-2}"
NAME="${CLUSTER_NAME:-ksc}"
IMAGE="kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed"
if [ -z "${CI:-}" ]; then  # local only: the drive/Colima setup (CI runners have neither)
  [ -n "${CX:-}" ] || { echo "CX unset: source \$CX/env.sh" >&2; exit 1; }
  [ "$(docker context show)" = colima ] || { echo "docker context is not colima" >&2; exit 1; }
fi
if kind get clusters 2>/dev/null | grep -qx "$NAME"; then
  echo "cluster $NAME exists; reusing"
else
  workers=""
  for _ in $(seq 1 "$WORKERS"); do workers+=$'  - role: worker\n    image: '"$IMAGE"$'\n'; done
  cfg="$(mktemp)"
  python3 - "$cfg" "$IMAGE" "$workers" <<'PY'
import sys
t=open("deploy/kind/kind-config.yaml.tmpl").read()
open(sys.argv[1],"w").write(t.replace("@IMAGE@",sys.argv[2]).replace("@WORKER_NODES@",sys.argv[3].rstrip("\n")))
PY
  kind create cluster --name "$NAME" --config "$cfg" --wait 0s
  rm -f "$cfg"
fi
kubectl config use-context "kind-$NAME" >/dev/null
kubectl apply -f deploy/kind/calico-v3.33.1.yaml >/dev/null
kubectl -n kube-system set env daemonset/calico-node CALICO_IPV4POOL_CIDR=10.244.0.0/16 >/dev/null
kubectl -n kube-system rollout status daemonset/calico-node --timeout=300s
kubectl wait --for=condition=Ready nodes --all --timeout=300s
kubectl get nodes -o wide
