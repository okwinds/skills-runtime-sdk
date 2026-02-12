"""
Bootstrap Layer（应用层启动/配置发现/来源追踪）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/bootstrap.md`

设计目标：
- 保持 SDK 核心无隐式 I/O：Agent 不会自动读取 `.env` / 自动发现 overlays
- 提供可选 bootstrap 入口：Web/CLI 可复用，提升开箱体验与可排障性
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import yaml

from agent_sdk.config.defaults import load_default_config_dict
from agent_sdk.config.loader import AgentSdkConfig, load_config_dicts


def _get_env_prefer_new(*, new_key: str, old_key: str) -> Tuple[Optional[str], Optional[str]]:
    """读取 env：新 key 优先，旧 key fallback。

    约定：
    - 当值为空串或仅空白时，视为“未设置”。

    参数：
    - new_key：新前缀 env key（例如 `SKILLS_RUNTIME_SDK_EXECUTOR_MODEL`）
    - old_key：旧前缀 env key（例如 `AGENT_SDK_EXECUTOR_MODEL`）

    返回：
    - (value, hit_key)：若命中则返回值与命中的 key 名；否则返回 (None, None)。
    """

    v_new = os.environ.get(new_key)
    if v_new is not None:
        s = v_new.strip()
        if s:
            return s, new_key

    v_old = os.environ.get(old_key)
    if v_old is not None:
        s = v_old.strip()
        if s:
            return s, old_key

    return None, None


def _split_paths(raw: str) -> list[str]:
    """将逗号/分号分隔的路径串切分为片段列表。

    参数：
    - raw：原始字符串（允许包含空白；分隔符支持 `,` 与 `;`）。

    返回：
    - list[str]：去掉空白与空项后的片段列表（保序）。
    """
    parts: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def _parse_env_text(text: str) -> Dict[str, str]:
    """解析 `.env` 风格文本为键值字典（best-effort）。

    支持的最小语法：
    - 忽略空行与 `#` 注释行
    - 可选前缀 `export `
    - `KEY=VALUE`，并去掉 VALUE 两侧的单/双引号

    参数：
    - text：文件全文文本。

    返回：
    - dict[str, str]：解析得到的环境变量映射。
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _load_dotenv(*, path: Path, override: bool) -> int:
    """将 `.env` 文件内容写入 `os.environ`。

    参数：
    - path：`.env` 文件路径。
    - override：是否覆盖已存在的环境变量。

    返回：
    - int：本次实际写入（或覆盖）到 `os.environ` 的键数量。
    """
    if not path.exists():
        return 0
    data = _parse_env_text(path.read_text(encoding="utf-8"))
    wrote = 0
    for k, v in data.items():
        if not override and k in os.environ:
            continue
        os.environ[k] = v
        wrote += 1
    return wrote


def load_dotenv_if_present(*, workspace_root: Path, override: bool = False) -> Optional[Path]:
    """
    约定加载：
    1) 若设置 `SKILLS_RUNTIME_SDK_ENV_FILE`（新优先旧兼容），加载其指向的文件（相对路径相对 workspace_root）
    2) 否则若 `<workspace_root>/.env` 存在，加载之

    参数：
    - workspace_root：工作区根目录（相对路径锚点）
    - override：是否覆盖已存在 env（默认 False）
    """

    ws = Path(workspace_root).resolve()
    p, _hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_ENV_FILE", old_key="AGENT_SDK_ENV_FILE")
    if p is not None:
        env_path = Path(p).expanduser()
        if not env_path.is_absolute():
            env_path = (ws / env_path).resolve()
        if not env_path.exists():
            raise ValueError(f"env file not found: {env_path}")
        _load_dotenv(path=env_path, override=override)
        return env_path

    cwd_env = (ws / ".env").resolve()
    if cwd_env.exists():
        _load_dotenv(path=cwd_env, override=override)
        return cwd_env
    return None


def _discover_default_overlay_path(*, workspace_root: Path) -> Optional[Path]:
    """
    发现默认 overlay 文件路径（优先 `runtime.yaml`，兼容 `llm.yaml`）。

    发现顺序：
    1) `<workspace_root>/config/runtime.yaml`
    2) `<workspace_root>/config/llm.yaml`（legacy fallback）

    参数：
    - workspace_root：工作区根目录。

    返回：
    - 命中的默认 overlay 路径；若均不存在则返回 `None`。
    """

    ws = Path(workspace_root).resolve()
    runtime_yaml = (ws / "config" / "runtime.yaml").resolve()
    if runtime_yaml.exists():
        return runtime_yaml

    legacy_llm_yaml = (ws / "config" / "llm.yaml").resolve()
    if legacy_llm_yaml.exists():
        return legacy_llm_yaml

    return None


def discover_overlay_paths(*, workspace_root: Path) -> list[Path]:
    """
    overlay 路径发现规则（固定，顺序稳定）：
    1) 默认 overlay：优先 `<workspace_root>/config/runtime.yaml`，兼容 `<workspace_root>/config/llm.yaml`
    2) `SKILLS_RUNTIME_SDK_CONFIG_PATHS`（新优先旧兼容；逗号/分号分隔；作为显式 overlay）
    """

    ws = Path(workspace_root).resolve()
    overlays: list[Path] = []

    default_overlay = _discover_default_overlay_path(workspace_root=ws)
    if default_overlay is not None:
        overlays.append(default_overlay)

    raw, _hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_CONFIG_PATHS", old_key="AGENT_SDK_CONFIG_PATHS")
    raw = raw or ""
    for p in _split_paths(raw):
        pp = Path(p).expanduser()
        if not pp.is_absolute():
            pp = (ws / pp).resolve()
        else:
            pp = pp.resolve()
        overlays.append(pp)

    # 去重（按 canonical path；保序）
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in overlays:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def _record_leaf_sources(value: Any, *, prefix: str, sources: Dict[str, str], label: str) -> None:
    """递归记录 mapping 的叶子字段来源（用于 sources 追踪）。

    约定：
    - 若 value 是 mapping，则继续向下展开；否则视为叶子并写入 `sources[prefix]=label`。

    参数：
    - value：当前节点的值。
    - prefix：当前节点的 dotted path（如 `llm.base_url`）。
    - sources：输出字典（被就地修改）。
    - label：来源标签（如 `embedded_default` 或 `overlay:/abs/path`）。
    """
    if isinstance(value, Mapping):
        for k, v in value.items():
            k2 = str(k)
            path = f"{prefix}.{k2}" if prefix else k2
            _record_leaf_sources(v, prefix=path, sources=sources, label=label)
        return
    sources[prefix] = label


def _deep_merge_with_sources(
    base: Dict[str, Any],
    overlay: Mapping[str, Any],
    *,
    sources: Dict[str, str],
    label: str,
    prefix: str = "",
) -> None:
    """将 overlay 深度合并到 base，并同步写入叶子字段 sources。

    合并语义：
    - 当 base[key] 与 overlay_value 均为 mapping 时，递归合并；
    - 否则 overlay_value 覆盖 base[key]（深拷贝），并记录其叶子字段来源。

    参数：
    - base：被合并的目标 dict（就地修改）。
    - overlay：覆盖层 mapping。
    - sources：叶子来源追踪字典（就地修改）。
    - label：本次 overlay 的来源标签。
    - prefix：当前递归的 dotted path 前缀。
    """
    for key, overlay_value in overlay.items():
        k = str(key)
        path = f"{prefix}.{k}" if prefix else k

        if k in base and isinstance(base[k], dict) and isinstance(overlay_value, Mapping):
            _deep_merge_with_sources(base[k], overlay_value, sources=sources, label=label, prefix=path)  # type: ignore[arg-type]
            continue

        base[k] = deepcopy(overlay_value)
        _record_leaf_sources(overlay_value, prefix=path, sources=sources, label=label)


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    """读取 YAML 文件并确保根节点是 mapping(dict)。

    参数：
    - path：YAML 文件路径。

    返回：
    - dict[str, Any]：YAML mapping 内容。

    异常：
    - ValueError：文件不存在或 YAML 根节点不是 mapping。
    """
    if not path.exists():
        raise ValueError(f"overlay config not found: {path}")
    obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(obj, dict):
        raise ValueError(f"overlay config root must be a mapping(dict): {path}")
    return obj


@dataclass(frozen=True)
class ResolvedRunConfig:
    """bootstrap 解析后的有效运行配置摘要（含来源追踪）。

    字段：
    - planner_model/executor_model：模型名（最终生效值）。
    - base_url/api_key_env：LLM 连接配置（最终生效值）。
    - overlay_paths：参与合并的 overlay 文件路径列表（字符串化）。
    - env_file：实际加载的 .env 路径（若无则为 None）。
    - sources：关键字段来源追踪（例如 `llm.base_url` 来源于 env/session/yaml）。
    """
    planner_model: str
    executor_model: str
    base_url: str
    api_key_env: str
    overlay_paths: list[str]
    env_file: Optional[str]
    sources: Dict[str, str]


def resolve_effective_run_config(*, workspace_root: Path, session_settings: Dict[str, Any]) -> ResolvedRunConfig:
    """
    解析有效配置（session > env > yaml），并返回来源追踪。
    """

    ws = Path(workspace_root).resolve()
    env_file = load_dotenv_if_present(workspace_root=ws, override=False)

    overlay_paths = discover_overlay_paths(workspace_root=ws)
    entries: list[Tuple[str, Dict[str, Any]]] = [("embedded_default", load_default_config_dict())]
    for p in overlay_paths:
        entries.append((f"overlay:{p}", _load_yaml_mapping(p)))

    merged: Dict[str, Any] = {}
    yaml_sources: Dict[str, str] = {}
    for label, d in entries:
        _deep_merge_with_sources(merged, d, sources=yaml_sources, label=label)

    cfg: AgentSdkConfig = load_config_dicts([merged])

    models = (session_settings.get("models") or {}) if isinstance(session_settings, dict) else {}
    llm = (session_settings.get("llm") or {}) if isinstance(session_settings, dict) else {}

    sources: Dict[str, str] = {}

    # planner
    if isinstance(models, dict) and models.get("planner"):
        planner_model = str(models["planner"])
        sources["models.planner"] = "session_settings:models.planner"
    else:
        v, hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_PLANNER_MODEL", old_key="AGENT_SDK_PLANNER_MODEL")
        if v is not None and hit is not None:
            planner_model = str(v)
            sources["models.planner"] = f"env:{hit}"
        else:
            planner_model = str(cfg.models.planner)
            sources["models.planner"] = f"yaml:{yaml_sources.get('models.planner','embedded_default')}#models.planner"

    # executor
    if isinstance(models, dict) and models.get("executor"):
        executor_model = str(models["executor"])
        sources["models.executor"] = "session_settings:models.executor"
    else:
        v, hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_EXECUTOR_MODEL", old_key="AGENT_SDK_EXECUTOR_MODEL")
        if v is not None and hit is not None:
            executor_model = str(v)
            sources["models.executor"] = f"env:{hit}"
        else:
            executor_model = str(cfg.models.executor)
            sources["models.executor"] = f"yaml:{yaml_sources.get('models.executor','embedded_default')}#models.executor"

    # base_url
    if isinstance(llm, dict) and llm.get("base_url"):
        base_url = str(llm["base_url"])
        sources["llm.base_url"] = "session_settings:llm.base_url"
    else:
        v, hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_LLM_BASE_URL", old_key="AGENT_SDK_LLM_BASE_URL")
        if v is not None and hit is not None:
            base_url = str(v)
            sources["llm.base_url"] = f"env:{hit}"
        else:
            base_url = str(cfg.llm.base_url)
            sources["llm.base_url"] = f"yaml:{yaml_sources.get('llm.base_url','embedded_default')}#llm.base_url"

    # api_key_env
    if isinstance(llm, dict) and llm.get("api_key_env"):
        api_key_env = str(llm["api_key_env"])
        sources["llm.api_key_env"] = "session_settings:llm.api_key_env"
    else:
        v, hit = _get_env_prefer_new(new_key="SKILLS_RUNTIME_SDK_LLM_API_KEY_ENV", old_key="AGENT_SDK_LLM_API_KEY_ENV")
        if v is not None and hit is not None:
            api_key_env = str(v)
            sources["llm.api_key_env"] = f"env:{hit}"
        else:
            api_key_env = str(cfg.llm.api_key_env)
            sources["llm.api_key_env"] = f"yaml:{yaml_sources.get('llm.api_key_env','embedded_default')}#llm.api_key_env"

    return ResolvedRunConfig(
        base_url=base_url,
        api_key_env=api_key_env,
        planner_model=planner_model,
        executor_model=executor_model,
        overlay_paths=[str(p) for p in overlay_paths],
        env_file=str(env_file) if env_file is not None else None,
        sources=sources,
    )
