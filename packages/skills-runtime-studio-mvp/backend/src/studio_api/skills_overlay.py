from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def skills_v2_config_from_roots(*, roots: List[str]) -> Dict[str, Any]:
    """
    将 Web/MVP 层的 roots 列表转换为 Skills V2（spaces/sources）配置。

    约束：
    - roots 每一项对应一个 filesystem source
    - 所有 sources 聚合到一个固定 space：account=web, domain=mvp
    """

    cleaned_roots: List[str] = []
    for r in roots or []:
        rr = str(r or "").strip()
        if rr:
            cleaned_roots.append(rr)

    source_ids: List[str] = []
    sources: List[Dict[str, Any]] = []
    for idx, root in enumerate(cleaned_roots):
        sid = f"web-fs-{idx}"
        source_ids.append(sid)
        sources.append({"id": sid, "type": "filesystem", "options": {"root": root}})

    spaces: List[Dict[str, Any]] = []
    if source_ids:
        spaces.append(
            {
                "id": "space-web-mvp",
                "account": "web",
                "domain": "mvp",
                "sources": source_ids,
                "enabled": True,
            }
        )

    return {
        "mode": "explicit",
        "spaces": spaces,
        "sources": sources,
        "injection": {"max_bytes": 65536},
    }


def write_session_skills_overlay(*, session_dir: Path, roots: List[str]) -> Path:
    """
    为某个 session 写入 skills overlay YAML，并返回路径。

    参数：
    - session_dir：session 目录（`.skills_runtime_sdk/sessions/<session_id>`）
    - roots：session 生效的 roots 列表
    """

    cfg = skills_v2_config_from_roots(roots=roots)
    overlay_path = (Path(session_dir) / "skills_overlay.yaml").resolve()
    overlay_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = [
        "config_version: 1",
        "skills:",
        "  mode: \"explicit\"",
        "  spaces:",
    ]

    spaces = cfg.get("spaces") or []
    for space in spaces:
        sources = space.get("sources") or []
        lines.extend(
            [
                f"    - id: \"{space['id']}\"",
                f"      account: \"{space['account']}\"",
                f"      domain: \"{space['domain']}\"",
                "      sources:",
                *[f"        - \"{sid}\"" for sid in sources],
                "      enabled: true",
            ]
        )

    lines.append("  sources:")
    sources_cfg = cfg.get("sources") or []
    for src in sources_cfg:
        options = src.get("options") if isinstance(src, dict) else None
        if not isinstance(options, dict):
            options = {}
        root = str(options.get("root") or "")
        root_q = json.dumps(root, ensure_ascii=False)
        lines.extend(
            [
                f"    - id: \"{src['id']}\"",
                f"      type: \"{src['type']}\"",
                "      options:",
                f"        root: {root_q}",
            ]
        )

    lines.extend(["  injection:", "    max_bytes: 65536", ""])
    overlay_path.write_text("\n".join(lines), encoding="utf-8")
    return overlay_path

