#!/usr/bin/env python3
"""Prints the estimated cost of one EKS session. Prices are constants below (us-east-1, on-demand) and are
NOT looked up live: verify them with the AWS Pricing Calculator (docs/COST.md). Usage: cost_estimate.py <hours>"""
import sys

EKS_CONTROL_PLANE = 0.10     # $/cluster-hour, standard support  (extended support: 0.60)   [unverified]
T3_MEDIUM = 0.0416           # $/instance-hour, Linux on-demand                                [third-party sourced]
PUBLIC_IPV4 = 0.005          # $/address-hour                                                  [unverified]
GP3_PER_GB_MONTH = 0.08      # $/GB-month, 20 GB root volume per node                          [unverified]
NODES = 3
VOLUME_GB = 20
HOURS_PER_MONTH = 730


def estimate(hours: float) -> dict[str, float]:
    parts = {
        "EKS control plane": EKS_CONTROL_PLANE * hours,
        f"{NODES} x t3.medium": T3_MEDIUM * NODES * hours,
        f"{NODES} public IPv4 addresses": PUBLIC_IPV4 * NODES * hours,
        f"{NODES} x {VOLUME_GB} GB gp3": GP3_PER_GB_MONTH * VOLUME_GB * NODES / HOURS_PER_MONTH * hours,
    }
    parts["total"] = sum(parts.values())
    return parts


if __name__ == "__main__":
    h = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    print(f"Estimated cost of a {h:g}-hour session (us-east-1, on-demand; verify with the Pricing Calculator):")
    for k, v in estimate(h).items():
        print(f"  {k:<30} ${v:6.2f}")
    print("  Not included: data transfer (small), Neon (free plan), GHCR (free). Self-imposed project ceiling: $25.")
