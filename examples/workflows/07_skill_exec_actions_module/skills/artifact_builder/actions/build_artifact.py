from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    """
    Skill action 脚本：生成一个确定性的 JSON 产物。

    约定：
    - 当前工作目录应为 workspace_root（由 skill_exec -> shell_exec 默认 cwd 保证）
    - 可用环境变量：
      - SKILLS_RUNTIME_SDK_WORKSPACE_ROOT / SKILLS_RUNTIME_SDK_SKILL_BUNDLE_ROOT / ...
      - ARTIFACT_KIND（来自 SKILL.md frontmatter actions.build.env）
    """

    workspace_root = Path(os.environ.get("SKILLS_RUNTIME_SDK_WORKSPACE_ROOT") or os.getcwd()).resolve()
    bundle_root = Path(os.environ.get("SKILLS_RUNTIME_SDK_SKILL_BUNDLE_ROOT") or "").resolve()
    mention = os.environ.get("SKILLS_RUNTIME_SDK_SKILL_MENTION") or ""
    action_id = os.environ.get("SKILLS_RUNTIME_SDK_SKILL_ACTION_ID") or ""
    kind = os.environ.get("ARTIFACT_KIND") or "unknown"

    obj = {
        "ok": True,
        "kind": kind,
        "skill": {"mention": mention, "action_id": action_id},
        "paths": {"workspace_root": str(workspace_root), "bundle_root": str(bundle_root)},
    }
    out_path = workspace_root / "action_artifact.json"
    out_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ACTION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
