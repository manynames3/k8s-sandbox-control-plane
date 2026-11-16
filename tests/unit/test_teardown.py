"""The teardown scripts, run against a stub `aws` (nothing here talks to AWS).

These prove the logic: what counts as an orphan, that a failed query is never read as "clean", that the direct cleanup is
a dry run by default, and that it touches only what carries the project tag. They cannot prove the real AWS answers have
the shape the stub assumes; that is checked by the first real session.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

STUB = r"""#!/usr/bin/env python3
import json, os, sys
args = " ".join(sys.argv[1:])
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(args + "\n")
for needle, out, rc in json.loads(os.environ.get("STUB_RESPONSES", "[]")):
    if needle in args:
        sys.stdout.write(out)
        sys.exit(rc)
EMPTY = {
    "eks list-clusters": {"clusters": []}, "eks list-nodegroups": {"nodegroups": []},
    "ec2 describe-instances": {"Reservations": []}, "ec2 describe-vpcs": {"Vpcs": []},
    "ec2 describe-network-interfaces": {"NetworkInterfaces": []}, "ec2 describe-volumes": {"Volumes": []},
    "ec2 describe-addresses": {"Addresses": []}, "ec2 describe-nat-gateways": {"NatGateways": []},
    "ec2 describe-security-groups": {"SecurityGroups": []}, "ec2 describe-launch-templates": {"LaunchTemplates": []},
    "ec2 describe-subnets": {"Subnets": []}, "ec2 describe-route-tables": {"RouteTables": []},
    "ec2 describe-internet-gateways": {"InternetGateways": []}, "resourcegroupstaggingapi get-resources": [],
}
for key, val in EMPTY.items():
    if key in args:
        print(json.dumps(val)); sys.exit(0)
if "iam get-role" in args:
    sys.stderr.write("An error occurred (NoSuchEntity) when calling the GetRole operation"); sys.exit(254)
sys.exit(0)
"""


@pytest.fixture
def aws(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "aws"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.write_text("")

    def run(script, *args, responses=()):
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "STUB_LOG": str(log),
               "STUB_RESPONSES": json.dumps(list(responses)), "AWS_REGION": "us-east-1"}
        r = subprocess.run(["bash", str(ROOT / "scripts" / script), *args], capture_output=True, text=True, env=env, check=False)
        return r, log.read_text().splitlines()
    return run


# ------------------------------------------------------------------ the checks (aws_orphans.sh)

def test_deep_check_is_clean_when_nothing_exists(aws):
    r, _ = aws("aws_orphans.sh", "deep")
    assert r.returncode == 0, r.stdout + r.stderr


def test_deep_check_finds_a_cluster_even_though_the_tag_index_is_empty(aws):
    r, _ = aws("aws_orphans.sh", "deep", responses=[("eks list-clusters", json.dumps({"clusters": ["ksc-session"]}), 0)])
    assert r.returncode == 1 and "eks-cluster ksc-session" in r.stdout


def test_deep_check_ignores_clusters_that_are_not_ours(aws):
    r, _ = aws("aws_orphans.sh", "deep", responses=[("eks list-clusters", json.dumps({"clusters": ["someone-elses"]}), 0)])
    assert r.returncode == 0


def test_deep_check_finds_a_leftover_iam_role(aws):
    r, _ = aws("aws_orphans.sh", "deep", responses=[("iam get-role --role-name ksc-session-sandbox-node", "{}", 0)])
    assert r.returncode == 1 and "iam-role ksc-session-sandbox-node" in r.stdout


def test_a_failed_query_is_never_read_as_clean(aws):
    r, _ = aws("aws_orphans.sh", "deep", responses=[("ec2 describe-vpcs", "", 255)])
    assert r.returncode == 2
    r, _ = aws("aws_orphans.sh", "expired", responses=[("resourcegroupstaggingapi", "", 255)])
    assert r.returncode == 2
    r, _ = aws("aws_orphans.sh", "deep", responses=[("ec2 describe-volumes", "not json", 0)])
    assert r.returncode == 2


def test_found_wins_over_a_failed_query_so_cleanup_still_runs(aws):
    r, _ = aws("aws_orphans.sh", "deep", responses=[
        ("ec2 describe-vpcs", "", 255), ("eks list-clusters", json.dumps({"clusters": ["ksc-session"]}), 0)])
    assert r.returncode == 1


def test_expired_counts_a_missing_deadline_as_expired(aws):
    tagged = [{"arn": "arn:aws:ec2:::vpc/a", "expires": None},
              {"arn": "arn:aws:ec2:::vpc/b", "expires": "2999-01-01T00:00:00Z"},
              {"arn": "arn:aws:ec2:::vpc/c", "expires": "2000-01-01T00:00:00Z"}]
    r, _ = aws("aws_orphans.sh", "expired", responses=[("resourcegroupstaggingapi", json.dumps(tagged), 0)])
    assert r.returncode == 1
    assert "vpc/a" in r.stdout and "vpc/c" in r.stdout and "vpc/b" not in r.stdout


def test_expired_is_clean_when_every_deadline_is_in_the_future(aws):
    tagged = [{"arn": "arn:aws:ec2:::vpc/b", "expires": "2999-01-01T00:00:00Z"}]
    r, _ = aws("aws_orphans.sh", "expired", responses=[("resourcegroupstaggingapi", json.dumps(tagged), 0)])
    assert r.returncode == 0


# ------------------------------------------------------------------ the direct cleanup (aws_force_cleanup.sh)

SCENE = [
    ("eks list-clusters", json.dumps({"clusters": ["ksc-session", "ksc-session-other", "unrelated"]}), 0),
    ("eks describe-cluster --name ksc-session-other", "None\n", 0),
    ("eks describe-cluster --name ksc-session", "ksc\n", 0),
    ("ec2 describe-vpcs", json.dumps({"Vpcs": [{"VpcId": "vpc-ours"}]}), 0),
    ("ec2 describe-subnets", json.dumps({"Subnets": [{"SubnetId": "subnet-1"}]}), 0),
    ("ec2 describe-internet-gateways", json.dumps({"InternetGateways": [{"InternetGatewayId": "igw-1"}]}), 0),
    ("ec2 describe-instances", json.dumps({"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}]}), 0),
]
DESTRUCTIVE = ("delete-", "terminate-", "release-", "detach-")


def test_cleanup_is_a_dry_run_by_default(aws):
    r, calls = aws("aws_force_cleanup.sh", responses=SCENE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would delete" in r.stdout and "dry run" in r.stdout
    assert not [c for c in calls if any(d in c for d in DESTRUCTIVE)], calls


def test_cleanup_with_yes_deletes_only_what_is_tagged(aws):
    r, calls = aws("aws_force_cleanup.sh", "--yes", responses=SCENE)
    assert r.returncode == 0, r.stdout + r.stderr
    deleted = [c for c in calls if any(d in c for d in DESTRUCTIVE)]
    text = "\n".join(deleted)
    assert "delete-cluster --name ksc-session" in text
    assert "terminate-instances --instance-ids i-1" in text
    assert "delete-subnet --subnet-id subnet-1" in text and "delete-vpc --vpc-id vpc-ours" in text
    assert "other" not in text and "unrelated" not in text and "skip cluster ksc-session-other" in r.stdout
    # dependency order: instances and subnets before the VPC; the internet gateway is detached before it is deleted
    assert text.index("delete-subnet") < text.index("delete-vpc")
    assert text.index("detach-internet-gateway") < text.index("delete-internet-gateway") < text.index("delete-vpc")


def test_cleanup_reports_failure_but_keeps_going(aws):
    r, calls = aws("aws_force_cleanup.sh", "--yes", responses=[("ec2 delete-subnet", "", 255)] + SCENE)
    assert r.returncode == 1 and "FAILED" in r.stdout
    assert any("delete-vpc" in c for c in calls)  # the remaining deletions were still attempted


def test_cleanup_rejects_unknown_arguments(aws):
    r, _ = aws("aws_force_cleanup.sh", "--everything")
    assert r.returncode == 2


# ------------------------------------------------------------------ the workflows keep their safety properties

def test_workflows_keep_the_teardown_properties():
    session = (ROOT / ".github/workflows/eks-session.yml").read_text()
    reaper = (ROOT / ".github/workflows/aws-reaper.yml").read_text()
    # destroy runs whatever happened before, with fresh credentials, and has a direct-delete fallback
    assert "id: destroy\n        if: always()" in session
    assert "aws_force_cleanup.sh --yes" in session and "aws_force_cleanup.sh --yes" in reaper
    assert "-lock=false" in session and "-lock-timeout=5m" in session
    # both independent post-destroy checks run
    assert "aws_orphans.sh list" in session and "aws_orphans.sh deep" in session
    # the reaper runs hourly, never alongside a session, and destroys only when something was actually found
    assert 'cron: "17 * * * *"' in reaper and "group: eks-session" in reaper
    assert "steps.expired.outputs.found == 'true'" in reaper
    # every long step is bounded so a hang cannot eat the teardown window
    for step in ("Terraform apply", "Benchmark", "Chaos tests", "Attack suite"):
        block = session.split(f"- name: {step}")[1].split("      - name:")[0]
        assert "timeout-minutes:" in block, step
