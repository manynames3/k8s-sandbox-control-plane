#!/usr/bin/env bash
# Cost-trap guards (HANDOFF section 3). Fails the build if the chart could create billable things.
set -euo pipefail
cd "$(dirname "$0")/.."
FAIL=0
rendered="$(helm template ksc charts/ksc -n ksc-system -f charts/ksc/values-kind.yaml)"
for pat in 'type: LoadBalancer' 'kind: Ingress' 'kind: IngressClass'; do
  if grep -q "$pat" <<<"$rendered"; then echo "FAIL: chart renders '$pat'"; FAIL=1; fi
done
# Terraform guards run once infra/ exists (phase 8).
if [ -d infra ] && grep -rEq 'aws_nat_gateway|aws_lb\b|aws_alb|aws_db_instance|enable_nat_gateway *= *true' infra 2>/dev/null; then
  echo "FAIL: terraform contains NAT gateway / load balancer / RDS"; FAIL=1
fi
[ "$FAIL" = 0 ] && echo "cost traps: none found"
exit "$FAIL"
