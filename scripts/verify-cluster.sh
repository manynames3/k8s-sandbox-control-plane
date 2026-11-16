#!/usr/bin/env bash
# Phase 1 checks: facts about the running cluster. Fails on the first broken guarantee.
set -uo pipefail
FAIL=0
check() { if eval "$2" >/dev/null 2>&1; then echo "  ok    $1"; else echo "  FAIL  $1"; FAIL=1; fi; }
# On EKS the sandbox pods must be placed on the tainted sandbox node group, like the dispatcher does.
NODE_SELECTOR_YAML=""
if [ "${KSC_ENV:-kind}" = eks ]; then
  NODE_SELECTOR_YAML='nodeSelector: {ksc.dev/pool: sandbox}
  tolerations: [{key: ksc.dev/sandbox, operator: Equal, value: "true", effect: NoSchedule}]'
fi
expected_nodes=$(( ${WORKERS:-2} + 1 ))

check "nodes ready ($expected_nodes)" '[ "$(kubectl get nodes --no-headers | grep -c " Ready")" = '"$expected_nodes"' ]'
if kubectl -n kube-system get ds calico-node >/dev/null 2>&1; then
  check "calico-node on every node" '[ "$(kubectl -n kube-system get ds calico-node -o jsonpath={.status.numberReady})" = '"$expected_nodes"' ]'
else
  check "VPC CNI network policy agent present (EKS)" 'kubectl -n kube-system get ds aws-node -o jsonpath={.spec.template.spec.containers[*].name} | grep -q aws-eks-nodeagent'
fi
check "sandboxes PSA enforce=restricted" '[ "$(kubectl get ns sandboxes -o jsonpath={.metadata.labels.pod-security\\.kubernetes\\.io/enforce})" = restricted ]'
check "default-deny netpol in sandboxes" 'kubectl -n sandboxes get netpol default-deny-all'
check "resourcequota in sandboxes" 'kubectl -n sandboxes get resourcequota sandbox-quota'
check "dispatcher can create jobs in sandboxes" '[ "$(kubectl auth can-i create jobs -n sandboxes --as=system:serviceaccount:ksc-system:ksc-dispatcher)" = yes ]'
check "dispatcher cannot read secrets" '[ "$(kubectl auth can-i get secrets -n sandboxes --as=system:serviceaccount:ksc-system:ksc-dispatcher)" = no ]'
check "dispatcher has nothing cluster-wide" '[ "$(kubectl auth can-i list pods -A --as=system:serviceaccount:ksc-system:ksc-dispatcher)" = no ]'
check "sandbox SA has no rbac" '[ "$(kubectl auth can-i list pods -n sandboxes --as=system:serviceaccount:sandboxes:sandbox)" = no ]'
check "PSA rejects privileged pod" '! kubectl -n sandboxes run priv --image=busybox --privileged --restart=Never --dry-run=server'
check "admission policy bound to sandboxes" 'kubectl get validatingadmissionpolicybinding ksc-sandbox-pod-guardrails'
check "api deployment available" 'kubectl -n ksc-system rollout status deploy/ksc-api --timeout=60s'

# NetworkPolicy is really enforced: a compliant pod in sandboxes must not reach DNS or the API server.
kubectl -n sandboxes delete pod nettest --ignore-not-found --wait >/dev/null 2>&1
kubectl -n sandboxes apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata: {name: nettest}
spec:
  serviceAccountName: sandbox
  automountServiceAccountToken: false
  restartPolicy: Never
  ${NODE_SELECTOR_YAML}
  securityContext: {runAsNonRoot: true, runAsUser: 10001, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: t
      image: busybox:1.36
      command: ["sh", "-c", "sleep 3600"]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}, readOnlyRootFilesystem: true}
      resources: {limits: {cpu: 100m, memory: 64Mi, ephemeral-storage: 16Mi}}
YAML
kubectl -n sandboxes wait --for=condition=Ready pod/nettest --timeout=120s >/dev/null 2>&1
check "sandbox test pod is Running (otherwise the denials below prove nothing)" '[ "$(kubectl -n sandboxes get pod nettest -o jsonpath={.status.phase})" = Running ] && kubectl -n sandboxes exec nettest -- true'
# Positive control: the same commands DO work from the trusted namespace, so failures in sandboxes are policy, not tooling.
kubectl -n ksc-system delete pod nettest-ctl --ignore-not-found --wait >/dev/null 2>&1
kubectl -n ksc-system run nettest-ctl --image=busybox:1.36 --restart=Never -- sleep 3600 >/dev/null 2>&1
kubectl -n ksc-system wait --for=condition=Ready pod/nettest-ctl --timeout=120s >/dev/null 2>&1
check "control: DNS works from ksc-system" 'kubectl -n ksc-system exec nettest-ctl -- nslookup -timeout=3 kubernetes.default.svc.cluster.local'
check "control: ksc-api reachable from ksc-system" 'kubectl -n ksc-system exec nettest-ctl -- nc -w 3 -z ksc-api 8080'
check "sandbox pod cannot resolve DNS" '! kubectl -n sandboxes exec nettest -- nslookup -timeout=3 kubernetes.default.svc.cluster.local'
check "sandbox pod cannot reach 1.1.1.1" '! kubectl -n sandboxes exec nettest -- wget -q -T 3 -O- http://1.1.1.1'
check "sandbox pod cannot reach the trusted namespace" '! kubectl -n sandboxes exec nettest -- nc -w 3 -z ksc-api.ksc-system.svc 8080'
kubectl -n ksc-system delete pod nettest-ctl --wait=false >/dev/null 2>&1
kubectl -n sandboxes delete pod nettest --wait=false >/dev/null 2>&1
[ "$FAIL" = 0 ] && echo "verify-cluster: PASS" || { echo "verify-cluster: FAIL"; exit 1; }
