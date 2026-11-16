#!/usr/bin/env bash
# Second line of defence when `terraform destroy` cannot finish (lost or locked state, a dependency Terraform does not
# know about, a half-created stack). It deletes the EKS cluster, EC2 instances and VPC networking of the session by
# calling the services directly. Scope is strict: only things tagged project=ksc, plus the EKS cluster named
# ksc-session* that carries that tag. IAM roles and Terraform state are left to a following `terraform destroy`, which
# finds the resources already gone and cleans up the rest.
#
#   aws_force_cleanup.sh          dry run: prints what it would delete, deletes nothing
#   aws_force_cleanup.sh --yes    deletes
# Exit codes: 0 finished (or nothing to do), 1 something could not be deleted, 2 usage or a query failed.
# Never run by hand against an account that holds anything else tagged project=ksc.
set -uo pipefail
apply=0
case "${1:-}" in
  "") ;;
  --yes) apply=1 ;;
  *) echo "usage: $0 [--yes]" >&2; exit 2 ;;
esac
region="${AWS_REGION:-us-east-1}"
cluster="${CLUSTER_NAME:-ksc-session}"
bad=0

say() { echo "$*"; }
# act <description> <aws args...>: run (or only print, in a dry run); a failure is recorded and does not stop the rest
act() {
  local what="$1"; shift
  if [ "$apply" = 1 ]; then
    say "delete: $what"
    "$@" --region "$region" >/dev/null 2>&1 || { say "  FAILED: $what"; bad=1; }
  else
    say "would delete: $what"
  fi
}
jqr() { jq -r "$1" 2>/dev/null; }

# ---- 1. EKS: node groups first, then the cluster (only a cluster that carries the project tag)
for c in $(aws eks list-clusters --region "$region" --output json | jqr '.clusters[]' | grep "^$cluster" || true); do
  tagged="$(aws eks describe-cluster --name "$c" --region "$region" --query 'cluster.tags.project' --output text 2>/dev/null)"
  if [ "$tagged" != "ksc" ]; then say "skip cluster $c: it is not tagged project=ksc"; continue; fi
  for ng in $(aws eks list-nodegroups --cluster-name "$c" --region "$region" --output json | jqr '.nodegroups[]'); do
    act "node group $c/$ng" aws eks delete-nodegroup --cluster-name "$c" --nodegroup-name "$ng"
  done
  if [ "$apply" = 1 ]; then
    for ng in $(aws eks list-nodegroups --cluster-name "$c" --region "$region" --output json | jqr '.nodegroups[]'); do
      aws eks wait nodegroup-deleted --cluster-name "$c" --nodegroup-name "$ng" --region "$region" || { say "  node group $ng did not finish deleting"; bad=1; }
    done
  fi
  act "EKS cluster $c" aws eks delete-cluster --name "$c"
  if [ "$apply" = 1 ]; then
    aws eks wait cluster-deleted --name "$c" --region "$region" || { say "  cluster $c did not finish deleting"; bad=1; }
  fi
done

# ---- 2. EC2 instances (node instances carry the project tag through the launch template)
ids="$( { aws ec2 describe-instances --region "$region" --filters Name=tag:project,Values=ksc \
            Name=instance-state-name,Values=pending,running,stopping,stopped --output json
          aws ec2 describe-instances --region "$region" --filters "Name=tag:eks:cluster-name,Values=$cluster" \
            Name=instance-state-name,Values=pending,running,stopping,stopped --output json; } \
        | jqr '.Reservations[].Instances[].InstanceId' | sort -u | tr '\n' ' ')"
if [ -n "${ids// /}" ]; then
  # shellcheck disable=SC2086
  act "instances $ids" aws ec2 terminate-instances --instance-ids $ids
  # shellcheck disable=SC2086
  [ "$apply" = 1 ] && { aws ec2 wait instance-terminated --instance-ids $ids --region "$region" || { say "  instances did not terminate"; bad=1; }; }
fi

# ---- 3. Elastic IPs tagged project=ksc
for a in $(aws ec2 describe-addresses --region "$region" --filters Name=tag:project,Values=ksc --output json | jqr '.Addresses[].AllocationId'); do
  act "elastic IP $a" aws ec2 release-address --allocation-id "$a"
done

# ---- 4. VPCs tagged project=ksc, emptied in dependency order, then deleted
for vpc in $(aws ec2 describe-vpcs --region "$region" --filters Name=tag:project,Values=ksc --output json | jqr '.Vpcs[].VpcId'); do
  say "VPC $vpc"
  for eni in $(aws ec2 describe-network-interfaces --region "$region" --filters "Name=vpc-id,Values=$vpc" --output json \
                 | jqr '.NetworkInterfaces[] | select(.Status=="available") | .NetworkInterfaceId'); do
    act "network interface $eni" aws ec2 delete-network-interface --network-interface-id "$eni"
  done
  for natgw in $(aws ec2 describe-nat-gateways --region "$region" --filter "Name=vpc-id,Values=$vpc" --output json \
                   | jqr '.NatGateways[] | select(.State=="pending" or .State=="available") | .NatGatewayId'); do
    act "NAT gateway $natgw" aws ec2 delete-nat-gateway --nat-gateway-id "$natgw"
  done
  for sg in $(aws ec2 describe-security-groups --region "$region" --filters "Name=vpc-id,Values=$vpc" --output json \
                | jqr '.SecurityGroups[] | select(.GroupName!="default") | .GroupId'); do
    act "security group $sg" aws ec2 delete-security-group --group-id "$sg"
  done
  for sn in $(aws ec2 describe-subnets --region "$region" --filters "Name=vpc-id,Values=$vpc" --output json | jqr '.Subnets[].SubnetId'); do
    act "subnet $sn" aws ec2 delete-subnet --subnet-id "$sn"
  done
  for rt in $(aws ec2 describe-route-tables --region "$region" --filters "Name=vpc-id,Values=$vpc" --output json \
                | jqr '.RouteTables[] | select(([.Associations[]? | select(.Main==true)] | length)==0) | .RouteTableId'); do
    act "route table $rt" aws ec2 delete-route-table --route-table-id "$rt"
  done
  for igw in $(aws ec2 describe-internet-gateways --region "$region" --filters "Name=attachment.vpc-id,Values=$vpc" --output json \
                 | jqr '.InternetGateways[].InternetGatewayId'); do
    act "detach internet gateway $igw" aws ec2 detach-internet-gateway --internet-gateway-id "$igw" --vpc-id "$vpc"
    act "internet gateway $igw" aws ec2 delete-internet-gateway --internet-gateway-id "$igw"
  done
  act "VPC $vpc" aws ec2 delete-vpc --vpc-id "$vpc"
done

# ---- 5. launch templates tagged project=ksc
for lt in $(aws ec2 describe-launch-templates --region "$region" --filters Name=tag:project,Values=ksc --output json | jqr '.LaunchTemplates[].LaunchTemplateId'); do
  act "launch template $lt" aws ec2 delete-launch-template --launch-template-id "$lt"
done

[ "$apply" = 1 ] || say "dry run: nothing was deleted (pass --yes to delete)"
[ "$bad" = 0 ] || { say "some deletions failed: see above"; exit 1; }
exit 0
