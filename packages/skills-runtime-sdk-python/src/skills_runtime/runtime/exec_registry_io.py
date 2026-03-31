"""
exec_registry.json 读写辅助函数。

职责：
- 统一 registry 的容错读取逻辑
- 统一 registry 的原子写入逻辑
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def read_exec_registry(*, exec_registry_path: Path, workspace_root: Path) -> Dict[str, Any]:
    """读取 exec registry；失败时返回带默认字段的对象。"""
    p = Path(exec_registry_path)
    root = Path(workspace_root)
    fallback = {"schema": 1, "workspace_root": str(root), "exec_sessions": {}}
    if not p.exists():
        return fallback
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(obj, dict):
        return fallback
    if not isinstance(obj.get("exec_sessions"), dict):
        obj["exec_sessions"] = {}
    if not isinstance(obj.get("workspace_root"), str):
        obj["workspace_root"] = str(root)
    if not isinstance(obj.get("schema"), int):
        obj["schema"] = 1
    return obj


def write_exec_registry(*, exec_registry_path: Path, obj: Dict[str, Any]) -> None:
    """原子写入 exec registry。"""
    p = Path(exec_registry_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
