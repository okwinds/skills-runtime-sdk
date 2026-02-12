from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MissingDocstring:
    path: Path
    lineno: int
    qualname: str


def _iter_python_files_under(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in {
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                "node_modules",
                "dist",
                "build",
                "venv",
                ".venv",
            }
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


def _find_missing_docstrings(py_path: Path) -> list[MissingDocstring]:
    src = py_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(py_path))

    missing: list[MissingDocstring] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _qualname(self, name: str) -> str:
            return ".".join(self.stack + [name]) if self.stack else name

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            if ast.get_docstring(node) is None:
                missing.append(MissingDocstring(py_path, node.lineno, self._qualname(node.name)))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            if ast.get_docstring(node) is None:
                missing.append(MissingDocstring(py_path, node.lineno, self._qualname(node.name)))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            if ast.get_docstring(node) is None:
                missing.append(MissingDocstring(py_path, node.lineno, self._qualname(node.name)))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

    Visitor().visit(tree)
    return missing


def test_docstrings_present_for_all_defs_under_src() -> None:
    """
    Docstring 合规护栏（对齐 `AGENTS.md` 5.6）。

    规则：
    - 扫描主线 SDK `packages/skills-runtime-sdk-python/src` 下所有 `.py` 文件；
    - 不扫描 `legacy/`（归档内容默认不作为主线护栏范围）；
    - 排除任何 `**/tests/**` 目录；
    - 对每个 `class/def/async def` 要求存在 docstring（包含嵌套定义）。
    """

    repo_root = Path(__file__).resolve().parents[1]
    roots = [
        repo_root / "packages" / "skills-runtime-sdk-python" / "src",
    ]

    missing: list[MissingDocstring] = []
    for root in roots:
        if not root.exists():
            continue
        for py_path in _iter_python_files_under(root):
            if f"{os.sep}tests{os.sep}" in f"{py_path}{os.sep}":
                continue
            missing.extend(_find_missing_docstrings(py_path))

    if not missing:
        return

    missing_sorted = sorted(missing, key=lambda m: (str(m.path), m.lineno, m.qualname))
    lines = ["missing docstrings:"]
    for m in missing_sorted:
        rel = m.path.relative_to(repo_root)
        lines.append(f"- {rel}:{m.lineno} {m.qualname}")
    raise AssertionError("\n".join(lines))
