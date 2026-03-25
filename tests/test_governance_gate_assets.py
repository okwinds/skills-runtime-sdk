from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_governance_check(root: Path, *, require_local_docs: bool, path_env: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if require_local_docs:
        env["REQUIRE_LOCAL_DOCS"] = "1"
    else:
        env.pop("REQUIRE_LOCAL_DOCS", None)
    if path_env is not None:
        env["PATH"] = path_env

    return subprocess.run(  # noqa: S603
        ["bash", "scripts/governance/governance-check.sh", "--full", "--report"],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )


def test_public_governance_gate_assets_exist() -> None:
    root = _repo_root()
    script = root / "scripts" / "governance" / "governance-check.sh"
    public_docs_index = root / "docs_for_coding_agent" / "DOCS_INDEX.md"

    assert script.exists() is True
    assert (root / "README.md").exists() is True
    assert public_docs_index.exists() is True


def test_local_governance_gate_assets_exist_and_are_indexed() -> None:
    root = _repo_root()
    policy = root / "docs" / "policies" / "governance-gate.md"
    script = root / "scripts" / "governance" / "governance-check.sh"
    docs_index_path = root / "DOCS_INDEX.md"

    if os.environ.get("REQUIRE_LOCAL_DOCS") != "1":
        pytest.skip("local governance docs are optional in OSS (set REQUIRE_LOCAL_DOCS=1 to enforce)")

    docs_index = docs_index_path.read_text(encoding="utf-8")

    assert policy.exists() is True
    assert script.exists() is True
    assert "docs/policies/governance-gate.md" in docs_index


def test_governance_check_script_supports_public_checkout_without_rg(tmp_path: Path) -> None:
    fixture_root = tmp_path / "repo"
    (fixture_root / "scripts" / "governance").mkdir(parents=True)
    (fixture_root / "docs_for_coding_agent").mkdir(parents=True)

    source_root = _repo_root()
    shutil.copy2(
        source_root / "scripts" / "governance" / "governance-check.sh",
        fixture_root / "scripts" / "governance" / "governance-check.sh",
    )
    shutil.copy2(source_root / "README.md", fixture_root / "README.md")
    shutil.copy2(
        source_root / "docs_for_coding_agent" / "DOCS_INDEX.md",
        fixture_root / "docs_for_coding_agent" / "DOCS_INDEX.md",
    )

    cp = _run_governance_check(
        fixture_root,
        require_local_docs=False,
        path_env="/usr/bin:/bin",
    )

    assert cp.returncode == 0, cp.stderr or cp.stdout
    report = fixture_root / "logs" / "governance" / "latest-report.md"
    assert report.exists() is True
    text = report.read_text(encoding="utf-8")
    assert "Summary:" in text
    assert "Generated At:" in text
    assert "governance-script: present" in text


def test_governance_check_script_writes_latest_report_in_local_mode() -> None:
    root = _repo_root()
    report = root / "logs" / "governance" / "latest-report.md"

    if os.environ.get("REQUIRE_LOCAL_DOCS") != "1":
        pytest.skip("local governance docs are optional in OSS (set REQUIRE_LOCAL_DOCS=1 to enforce)")

    cp = _run_governance_check(root, require_local_docs=True)

    assert cp.returncode == 0, cp.stderr or cp.stdout
    assert report.exists() is True
    text = report.read_text(encoding="utf-8")
    assert "Summary:" in text
    assert "Generated At:" in text
