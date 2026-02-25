from __future__ import annotations

import os
from pathlib import Path
import re


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _should_skip_dir(p: Path) -> bool:
    parts = set(p.parts)
    return any(
        d in parts
        for d in {
            ".git",
            ".pytest_cache",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            "legacy",  # 归档区默认不参与主线护栏
        }
    )


def test_repo_has_no_legacy_account_domain_tokens() -> None:
    """
    护栏：仓库主线不得残留旧的二段式 mention/术语片段。

    说明：
    - 这是“文本层护栏”，用于防止 docs/help/examples/脚本漂移回旧口径；
    - OpenSpec 的历史归档允许保留旧文案，因此默认跳过 `openspec/changes/archive/`。
    """

    root = _repo_root()

    # 说明：
    # - 本护栏只覆盖“会被用户/开发者直接复制运行”的主线资产，避免误伤历史会议纪要/旧任务总结等追溯材料；
    # - OpenSpec 变更包属于“过程产物”，允许在其中保留历史上下文，因此本护栏不扫描 openspec/。
    scan_roots = [
        root / "README.md",
        root / "README.cn.md",
        root / "help",
        root / "docs" / "specs",
        root / "docs_for_coding_agent",
        root / "examples",
        root / "packages",
        root / "scripts",
    ]

    # 仅针对“旧口径痕迹”做精确匹配，避免误伤普通英文语境。
    banned_substrings = [
        "$[account:domain]",
        "account/domain",
        "skills.spaces[].account",
        "skills.spaces[].domain",
    ]
    banned_line_res = [
        re.compile(r"^\s*account\s*:\s*", re.IGNORECASE),
        re.compile(r"^\s*domain\s*:\s*", re.IGNORECASE),
    ]

    exts = {".md", ".py", ".txt", ".yaml", ".yml", ".sh", ".http", ".json"}

    hits: list[str] = []
    for scan_root in scan_roots:
        if scan_root.is_file():
            candidates = [scan_root]
        else:
            candidates = list(scan_root.rglob("*"))

        for path in candidates:
            if not path.is_file():
                continue
            if path.suffix not in exts:
                continue
            if _should_skip_dir(path):
                continue
            # 不扫描测试代码，避免“专门验证 legacy 拒绝”的用例与护栏冲突
            if "tests" in path.parts:
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                # fail-open：二进制或不可读文件不计入本护栏范围
                continue

            for s in banned_substrings:
                if s in text:
                    hits.append(f"{path}: contains {s!r}")

            # 对 YAML 风格 key 做行级扫描（减少误报）
            if path.suffix in {".yaml", ".yml"}:
                for idx, line in enumerate(text.splitlines(), start=1):
                    if any(r.match(line) for r in banned_line_res):
                        hits.append(f"{path}:{idx}: legacy key style: {line.strip()!r}")

    # OSS 场景下可能不包含 OpenSpec archive；但本护栏对主线文件应始终生效。
    assert not hits, "legacy account/domain tokens found:\n" + "\n".join(hits)
