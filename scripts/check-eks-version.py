#!/usr/bin/env python3
"""Cost-trap guard: the pinned EKS version must be in STANDARD support, with margin.

An EKS cluster on a version past standard support bills $0.60/hour (extended support) instead of $0.10.
Fails if the Terraform default does not match version-support.json, or if the version leaves standard
support within MARGIN_DAYS (default 60). Needs no AWS credentials; the session workflow additionally
checks the live API."""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARGIN_DAYS = 60


def main(today: date | None = None) -> int:
    today = today or date.today()
    support = json.loads((ROOT / "infra/eks/version-support.json").read_text())
    tf = (ROOT / "infra/eks/variables.tf").read_text()
    m = re.search(r'variable "kubernetes_version".*?default\s*=\s*"([^"]+)"', tf, re.DOTALL)
    if not m:
        print("FAIL: kubernetes_version default not found in infra/eks/variables.tf")
        return 1
    pinned = m.group(1)
    if pinned != support["kubernetes_version"]:
        print(f"FAIL: terraform pins {pinned} but version-support.json describes {support['kubernetes_version']}")
        return 1
    end = date.fromisoformat(support["end_of_standard_support"])
    left = (end - today).days
    if left <= MARGIN_DAYS:
        print(f"FAIL: EKS {pinned} leaves standard support on {end} ({left} days). "
              f"Extended support costs 6x. Move to a newer version and update version-support.json.")
        return 1
    print(f"ok: EKS {pinned} is in standard support until {end} ({left} days left, margin {MARGIN_DAYS})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
