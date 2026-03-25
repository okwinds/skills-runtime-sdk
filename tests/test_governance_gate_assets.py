from __future__ import annotations

from pathlib import Path
import subprocess


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_governance_gate_assets_exist_and_are_indexed() -> None:
    root = _repo_root()
    policy = root / "docs" / "policies" / "governance-gate.md"
    script = root / "scripts" / "governance" / "governance-check.sh"
    docs_index = (root / "DOCS_INDEX.md").read_text(encoding="utf-8")

    assert policy.exists() is True
    assert script.exists() is True
    assert "docs/policies/governance-gate.md" in docs_index


def test_governance_check_script_writes_latest_report() -> None:
    root = _repo_root()
    report = root / "logs" / "governance" / "latest-report.md"

    cp = subprocess.run(  # noqa: S603
        ["bash", "scripts/governance/governance-check.sh", "--full", "--report"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    assert cp.returncode == 0, cp.stderr or cp.stdout
    assert report.exists() is True
    text = report.read_text(encoding="utf-8")
    assert "Summary:" in text
    assert "Generated At:" in text
