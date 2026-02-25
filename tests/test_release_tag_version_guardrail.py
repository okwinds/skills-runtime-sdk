from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def _run_guardrail(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts/check_release_tag_version.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
    )


def test_guardrail_passes_when_all_versions_match() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        pyproject = root / "pyproject.toml"
        init_py = root / "__init__.py"

        pyproject.write_text(
            "\n".join(
                [
                    "[project]",
                    'name = "x"',
                    'version = "1.2.3"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        init_py.write_text('__version__ = "1.2.3"\n', encoding="utf-8")

        result = _run_guardrail("--tag", "refs/tags/v1.2.3", "--pyproject", str(pyproject), "--init", str(init_py))
        assert result.returncode == 0, (result.stdout, result.stderr)


def test_guardrail_fails_when_tag_mismatches_pyproject() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        pyproject = root / "pyproject.toml"
        init_py = root / "__init__.py"

        pyproject.write_text(
            "\n".join(
                [
                    "[project]",
                    'name = "x"',
                    'version = "1.2.3"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        init_py.write_text('__version__ = "1.2.3"\n', encoding="utf-8")

        result = _run_guardrail("--tag", "v1.2.4", "--pyproject", str(pyproject), "--init", str(init_py))
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "tag" in result.stderr.lower()


def test_guardrail_fails_when_init_mismatches_pyproject() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        pyproject = root / "pyproject.toml"
        init_py = root / "__init__.py"

        pyproject.write_text(
            "\n".join(
                [
                    "[project]",
                    'name = "x"',
                    'version = "1.2.3"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        init_py.write_text('__version__ = "1.2.4"\n', encoding="utf-8")

        result = _run_guardrail("--tag", "v1.2.3", "--pyproject", str(pyproject), "--init", str(init_py))
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "__init__" in result.stderr

