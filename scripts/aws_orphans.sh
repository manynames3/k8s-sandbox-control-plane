#!/usr/bin/env bash
# Looks for AWS resources left behind by an EKS session. Never creates or deletes anything.
#   aws_orphans.sh list      every resource tagged project=ksc (Resource Groups Tagging API)
#   aws_orphans.sh expired   those whose expires-at tag is in the past OR missing
#   aws_orphans.sh deep      direct service queries that do not depend on tags or on the tag index (EKS clusters,
#                            EC2 instances, VPCs, ENIs, volumes, addresses, NAT gateways, security groups, launch
#                            templates, the three IAM roles). The tag index lags and some resources are never indexed.
# Exit codes: 0 nothing found, 1 resources found, 2 the check itself failed (never mistaken for "found").
# Needs AWS credentials (the CI role).
set -uo pipefail
mode="${1:-list}"
region="${AWS_REGION:-us-east-1}"
cluster="${CLUSTER_NAME:-ksc-session}"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

fail() { echo "the AWS query failed: $*" >&2; exit 2; }

if [ "$mode" = "deep" ]; then
  failed=0
  out=""
  # q <label> <jq filter> <aws args...>: append "label id" for every id the filter extracts
  q() {
    local label="$1" filter="$2"; shift 2
    local json ids
    if ! json="$("$@" --region "$region" --output json 2>/dev/null)"; then
      echo "query failed: $label" >&2; failed=1; return
    fi
    if ! ids="$(jq -r "$filter" <<<"$json" 2>/dev/null)"; then
      echo "could not parse the answer for: $label" >&2; failed=1; return
    fi
    [ -z "$ids" ] || out+="$(sed "s/^/$label /" <<<"$ids")"$'\n'
  }
  live='Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down'
  q eks-cluster '.clusters[] | select(startswith("'"$cluster"'"))' aws eks list-clusters
  q ec2-instance '.Reservations[].Instances[].InstanceId' aws ec2 describe-instances --filters Name=tag:project,Values=ksc "$live"
  q ec2-instance '.Reservations[].Instances[].InstanceId' aws ec2 describe-instances --filters "Name=tag:eks:cluster-name,Values=$cluster" "$live"
  q vpc '.Vpcs[].VpcId' aws ec2 describe-vpcs --filters Name=tag:project,Values=ksc
  q network-interface '.NetworkInterfaces[].NetworkInterfaceId' aws ec2 describe-network-interfaces --filters Name=tag:project,Values=ksc
  q network-interface '.NetworkInterfaces[].NetworkInterfaceId' aws ec2 describe-network-interfaces --filters "Name=tag:cluster.k8s.amazonaws.com/name,Values=$cluster"
  q volume '.Volumes[].VolumeId' aws ec2 describe-volumes --filters Name=tag:project,Values=ksc
  q elastic-ip '.Addresses[].AllocationId' aws ec2 describe-addresses --filters Name=tag:project,Values=ksc
  q nat-gateway '.NatGateways[] | select(.State=="pending" or .State=="available") | .NatGatewayId' aws ec2 describe-nat-gateways --filter Name=tag:project,Values=ksc
  q security-group '.SecurityGroups[].GroupId' aws ec2 describe-security-groups --filters Name=tag:project,Values=ksc
  q launch-template '.LaunchTemplates[].LaunchTemplateId' aws ec2 describe-launch-templates --filters Name=tag:project,Values=ksc
  for role in "$cluster-cluster" "$cluster-system-node" "$cluster-sandbox-node"; do
    if json="$(aws iam get-role --role-name "$role" --output json 2>&1)"; then
      out+="iam-role $role"$'\n'
    elif ! grep -q NoSuchEntity <<<"$json"; then
      echo "query failed: iam-role $role" >&2; failed=1
    fi
  done
  out="$(sed '/^$/d' <<<"$out" | sort -u)"
  if [ -n "$out" ]; then
    echo "resources found by direct queries (deep):"
    sed 's/^/  /' <<<"$out"
    exit 1
  fi
  [ "$failed" = 0 ] || exit 2
  echo "no resources found by direct queries (deep)"
  exit 0
fi

all="$(aws resourcegroupstaggingapi get-resources --region "$region" \
  --tag-filters Key=project,Values=ksc \
  --query 'ResourceTagMappingList[].{arn:ResourceARN,expires:Tags[?Key==`expires-at`]|[0].Value}' --output json)" \
  || fail "tagging API"

case "$mode" in
  list)    found="$all" ;;
  # A resource with no expires-at tag has no deadline at all, so it counts as expired. Without this, one missing tag
  # would make a resource immune to the reaper.
  expired) found="$(jq --arg now "$now" '[.[] | select(.expires == null or .expires < $now)]' <<<"$all")" || exit 2 ;;
  *) echo "usage: $0 list|expired|deep" >&2; exit 2 ;;
esac

count="$(jq length <<<"$found")" || exit 2
if [ "$count" -gt 0 ]; then
  echo "$count resource(s) tagged project=ksc ($mode):"
  jq -r '.[] | "  \(.arn)  expires-at=\(.expires // "none")"' <<<"$found"
  exit 1
fi
echo "no resources tagged project=ksc ($mode)"
