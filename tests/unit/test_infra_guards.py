"""The cost-trap guards must actually fail when they should."""
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util

import cost_estimate

spec = importlib.util.spec_from_file_location("check_eks_version", ROOT / "scripts/check-eks-version.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_version_guard_passes_with_margin_and_fails_inside_60_days(capsys):
    assert guard.main(today=date(2026, 11, 16)) == 0
    assert guard.main(today=date(2027, 6, 5)) == 1  # 58 days before 2027-08-02
    assert "Extended support costs 6x" in capsys.readouterr().out
    assert guard.main(today=date(2027, 8, 2)) == 1


def test_version_guard_detects_a_mismatch(tmp_path, monkeypatch):
    (tmp_path / "infra/eks").mkdir(parents=True)
    (tmp_path / "infra/eks/version-support.json").write_text(
        '{"kubernetes_version": "1.35", "end_of_standard_support": "2027-03-27"}')
    (tmp_path / "infra/eks/variables.tf").write_text('variable "kubernetes_version" {\n  default = "1.36"\n}\n')
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.main(today=date(2026, 11, 16)) == 1


def test_session_estimate_stays_well_under_the_ceiling():
    two_hours = cost_estimate.estimate(2)["total"]
    four_hours = cost_estimate.estimate(4)["total"]
    assert 0.2 < two_hours < 1.0 and four_hours < 2.0  # handoff: a two-hour session well under $1
    assert four_hours < 25 / 10  # a maximum session uses under a tenth of the $25 project ceiling


def test_cost_trap_script_passes_on_the_repo_and_catches_a_nat_gateway(tmp_path):
    ok = subprocess.run(["bash", str(ROOT / "scripts/check-cost-traps.sh")], capture_output=True, text=True,
                        check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_cost_trap_script_catches_a_nat_gateway_and_an_open_endpoint():
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        shutil.copytree(ROOT / "infra", work / "infra", ignore=shutil.ignore_patterns(".terraform"))
        shutil.copytree(ROOT / "scripts", work / "scripts")
        shutil.copytree(ROOT / "charts", work / "charts")
        main = work / "infra/eks/main.tf"
        main.write_text(main.read_text().replace("enable_nat_gateway      = false", "enable_nat_gateway      = true")
                        .replace("[var.runner_cidr]", '["0.0.0.0/0"]'))
        res = subprocess.run(["bash", str(work / "scripts/check-cost-traps.sh")], capture_output=True, text=True,
                             cwd=work, check=False)
        assert res.returncode == 1
        assert "NAT gateway" in res.stdout and "must set enable_nat_gateway = false" in res.stdout
        assert "must not be open to the world" in res.stdout
