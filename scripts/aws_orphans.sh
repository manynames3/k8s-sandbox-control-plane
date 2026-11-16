#!/usr/bin/env bash
# Lists AWS resources tagged project=ksc (the Resource Groups Tagging API).
#   aws_orphans.sh list      print every tagged resource; exit 1 if there are any
#   aws_orphans.sh expired   print those whose expires-at tag is in the past; exit 1 if there are any
# Needs AWS credentials (the CI role). Never creates or deletes anything.
set -euo pipefail
mode="${1:-list}"
region="${AWS_REGION:-us-east-1}"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

all="$(aws resourcegroupstaggingapi get-resources --region "$region" \
  --tag-filters Key=project,Values=ksc \
  --query 'ResourceTagMappingList[].{arn:ResourceARN,expires:Tags[?Key==`expires-at`]|[0].Value}' --output json)"

case "$mode" in
  list)    found="$all" ;;
  expired) found="$(jq --arg now "$now" '[.[] | select(.expires != null and .expires < $now)]' <<<"$all")" ;;
  *) echo "usage: $0 list|expired" >&2; exit 2 ;;
esac

count="$(jq length <<<"$found")"
if [ "$count" -gt 0 ]; then
  echo "$count resource(s) tagged project=ksc ($mode):"
  jq -r '.[] | "  \(.arn)  expires-at=\(.expires // "none")"' <<<"$found"
  exit 1
fi
echo "no resources tagged project=ksc ($mode)"
