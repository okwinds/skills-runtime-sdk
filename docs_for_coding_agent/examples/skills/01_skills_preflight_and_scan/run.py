"""
最小 skills preflight + scan 示例（离线）。

用途：
- 展示 Skills 的最小配置形态（spaces/sources）；
- 演示 preflight（零 I/O）与 scan（filesystem 扫描）；
- 输出 ScanReport 的关键统计，便于编码智能体理解 scan 的“结构化产物”。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.skills.manager import SkillsManager


def main() -> int:
    """脚本入口：preflight + scan。"""

    parser = argparse.ArgumentParser(description="01_skills_preflight_and_scan")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    skills_root = workspace_root / "skills_demo"
    skill_dir = skills_root / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: \"a minimal demo skill\"\n---\n\n# Demo Skill\n",
        encoding="utf-8",
    )

    cfg = AgentSdkSkillsConfig.model_validate(
        {
            "spaces": [
                {
                    "id": "local-space",
                    "namespace": "local:demo",
                    "sources": ["fs1"],
                    "enabled": True,
                }
            ],
            "sources": [
                {
                    "id": "fs1",
                    "type": "filesystem",
                    "options": {"root": str(skills_root)},
                }
            ],
            "injection": {"max_bytes": None},
        }
    )

    mgr = SkillsManager(workspace_root=workspace_root, skills_config=cfg)

    issues = mgr.preflight()
    print("[example] preflight_issues_total:", len(issues))
    if issues:
        print(json.dumps([i.model_dump() for i in issues], ensure_ascii=False, indent=2))

    report = mgr.scan()
    print("[example] scan.stats:", json.dumps(report.stats, ensure_ascii=False))
    print("[example] scan.skills_total:", len(report.skills))

    assert len(report.skills) == 1
    print("EXAMPLE_OK: skills_preflight_scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
