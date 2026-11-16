#!/usr/bin/env bash
# Cost-trap guards (HANDOFF section 3). Fails the build if the chart could create billable things.
set -euo pipefail
cd "$(dirname "$0")/.."
FAIL=0
rendered="$(helm template ksc charts/ksc -n ksc-system -f charts/ksc/values-kind.yaml)"
for pat in 'type: LoadBalancer' 'kind: Ingress' 'kind: IngressClass'; do
  if grep -q "$pat" <<<"$rendered"; then echo "FAIL: chart renders '$pat'"; FAIL=1; fi
done
# Terraform guards (infra/).
if [ -d infra ]; then
  if grep -rEq 'aws_nat_gateway|aws_lb\b|aws_alb|aws_db_instance|aws_rds|aws_eip\b|enable_nat_gateway *= *true' --include='*.tf' --exclude-dir=.terraform infra; then
    echo "FAIL: terraform contains a NAT gateway, load balancer, RDS or Elastic IP"; FAIL=1
  fi
  eks=infra/eks/main.tf
  grep -Eq 'enable_nat_gateway *= *false' $eks || { echo "FAIL: $eks must set enable_nat_gateway = false"; FAIL=1; }
  grep -Eq 'compute_config *= *\{ *enabled *= *false *\}' $eks || { echo "FAIL: $eks must keep EKS Auto Mode off (compute_config.enabled = false)"; FAIL=1; }
  grep -Eq 'enabled_log_types *= *\[\]' $eks || { echo "FAIL: $eks must keep control-plane logging off"; FAIL=1; }
  grep -Eq 'create_kms_key *= *false' $eks || { echo "FAIL: $eks must not create a KMS key"; FAIL=1; }
  [ "$(grep -Ec 'http_put_response_hop_limit *= *1' $eks)" = 2 ] || { echo "FAIL: both node groups need IMDS hop limit 1"; FAIL=1; }
  [ "$(grep -Ec 'http_tokens *= *"required"' $eks)" = 2 ] || { echo "FAIL: both node groups need IMDSv2 required"; FAIL=1; }
  grep -Eq 'project *= *"ksc"' $eks || { echo "FAIL: $eks must tag everything project=ksc"; FAIL=1; }
  grep -Eq 'expires-at' $eks || { echo "FAIL: $eks must tag everything with expires-at"; FAIL=1; }
  if grep -Eq 'endpoint_public_access_cidrs *= *\[ *"0\.0\.0\.0/0"' $eks; then echo "FAIL: the public API endpoint must not be open to the world"; FAIL=1; fi
  python3 scripts/check-eks-version.py || FAIL=1
fi
[ "$FAIL" = 0 ] && echo "cost traps: none found"
exit "$FAIL"
