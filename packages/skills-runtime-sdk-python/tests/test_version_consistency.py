from __future__ import annotations

from pathlib import Path
import tomllib


def test_sdk_version_matches_pyproject() -> None:
    """
    版本一致性护栏：
    - `skills_runtime.__version__` 必须与 pyproject.toml 的 project.version 一致。
    - 避免出现“打了 tag/发了 release，但构建出来的包版本没变”的问题（PyPI 会拒绝重复文件名）。
    """

    repo_root = Path(__file__).resolve().parents[3]
    pyproject = repo_root / "packages" / "skills-runtime-sdk-python" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    expected = data["project"]["version"]

    from skills_runtime import __version__

    assert __version__ == expected
