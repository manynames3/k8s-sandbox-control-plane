#!/usr/bin/env bash
# OPTIONAL, BEST EFFORT, NOT YET RUN ON A REAL CLUSTER (written without an AWS account; it needs the owner's session
# to find out whether it works). Installs gVisor (runsc) on the tainted sandbox node group, adds a RuntimeClass, points
# the dispatcher at it, then reruns the attack suite and the benchmark under it and records the overhead.
# If any step fails the script exits non-zero and the session workflow records exactly where; nothing else depends on it.
#
# What it changes on the nodes: it downloads runsc and containerd-shim-runsc-v1, registers a `runsc` runtime in
# /etc/containerd/config.toml and restarts containerd on the SANDBOX nodes only (the tainted pool holds nothing but
# sandboxes). It never touches the system node.
set -euo pipefail
cd "$(dirname "$0")/../.."
ARCH="$(uname -m)"   # x86_64 on the EKS nodes
NS=gvisor-install

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace "$NS" pod-security.kubernetes.io/enforce=privileged --overwrite

cat <<YAML | kubectl apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata: {name: gvisor-installer, namespace: $NS}
spec:
  selector: {matchLabels: {app: gvisor-installer}}
  template:
    metadata: {labels: {app: gvisor-installer}}
    spec:
      hostPID: true
      nodeSelector: {ksc.dev/pool: sandbox}
      tolerations: [{key: ksc.dev/sandbox, operator: Equal, value: "true", effect: NoSchedule}]
      containers:
        - name: install
          image: public.ecr.aws/amazonlinux/amazonlinux:2023
          securityContext: {privileged: true}
          volumeMounts: [{name: host, mountPath: /host}]
          command: ["bash", "-ec"]
          args:
            - |
              dnf install -y -q curl >/dev/null
              base=https://storage.googleapis.com/gvisor/releases/release/latest/$ARCH
              for f in runsc containerd-shim-runsc-v1; do
                curl -fsSL "\$base/\$f" -o "/host/usr/local/bin/\$f"; chmod 755 "/host/usr/local/bin/\$f"
              done
              if ! grep -q 'runtimes.runsc' /host/etc/containerd/config.toml; then
                cat >> /host/etc/containerd/config.toml <<'TOML'

              [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
                runtime_type = "io.containerd.runsc.v1"
              TOML
                nsenter -t 1 -m -u -i -n -p -- systemctl restart containerd
              fi
              echo installed; sleep infinity
      volumes: [{name: host, hostPath: {path: /}}]
YAML
kubectl -n "$NS" rollout status ds/gvisor-installer --timeout=600s
sleep 20   # containerd restart

cat <<'YAML' | kubectl apply -f -
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: gvisor}
handler: runsc
YAML

kubectl -n ksc-system set env deploy/ksc-dispatcher SANDBOX_RUNTIME_CLASS=gvisor
kubectl -n ksc-system rollout status deploy/ksc-dispatcher --timeout=300s

echo "== attack suite under gVisor"
make attack
echo "== benchmark under gVisor"
BENCH_ENV=eks-gvisor uv run python bench/bench.py --runs 100 --concurrency 5

kubectl -n ksc-system set env deploy/ksc-dispatcher SANDBOX_RUNTIME_CLASS=
kubectl delete namespace "$NS" --wait=false
