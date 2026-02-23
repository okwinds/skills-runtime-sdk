"""
配置加载器（YAML）。

参考文档：
- 配置概览与示例：`help/02-config-reference.md`
- Tools/Safety/Sandbox 心智模型：`help/06-tools-and-safety.md`
- Sandbox best practices：`help/sandbox-best-practices.md` / `help/sandbox-best-practices.cn.md`
- 默认配置：`packages/skills-runtime-sdk-python/src/agent_sdk/assets/default.yaml`

设计目标（M1）：
- 支持加载多个 YAML，并按顺序做深度合并（后者覆盖前者）。
- 使用 pydantic 做 schema 校验；未知字段允许保留（避免默认配置新增字段导致加载失败）。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool


def _deep_merge(base: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> MutableMapping[str, Any]:
    """
    深度合并两个 dict（overlay 覆盖 base）。

    合并规则：
    - dict + dict：递归合并
    - 其它类型：overlay 直接覆盖
    - list：整体覆盖（不做去重/拼接）
    """

    for key, overlay_value in overlay.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(overlay_value, Mapping)
        ):
            _deep_merge(base[key], overlay_value)  # type: ignore[arg-type]
            continue
        base[key] = deepcopy(overlay_value)
    return base


class AgentSdkLlmConfig(BaseModel):
    """LLM 连接配置（最小集合）。"""

    model_config = ConfigDict(extra="allow")

    base_url: str
    api_key_env: str
    timeout_sec: int = Field(default=60, ge=1)
    max_retries: int = Field(default=3, ge=0)


class AgentSdkModelsConfig(BaseModel):
    """模型选择（planner/executor）。"""

    model_config = ConfigDict(extra="allow")

    planner: str
    executor: str


class AgentSdkSandboxConfig(BaseModel):
    """
    OS sandbox 配置（框架层）。

    参考文档：
    - `help/02-config-reference.md`（字段说明）
    - `help/sandbox-best-practices.md` / `help/sandbox-best-practices.cn.md`（最佳实践与验证脚本）

    说明：
    - `default_policy` 默认 `none`，保证不改变既有行为；
    - 当设置为 `restricted`，且 tool 未显式覆盖 `sandbox` 参数时，`shell_exec` 会继承并要求 OS sandbox adapter 可用；
    - 本 SDK 不支持 Windows（配置可写，但执行不会进入 Windows 分支）。
    """

    model_config = ConfigDict(extra="allow")

    class Os(BaseModel):
        """平台 sandbox backend 选择与参数。"""

        model_config = ConfigDict(extra="allow")

        class Seatbelt(BaseModel):
            """macOS seatbelt（sandbox-exec）配置。"""

            model_config = ConfigDict(extra="allow")

            profile: str = Field(default="(version 1) (allow default)")

        class Bubblewrap(BaseModel):
            """Linux bubblewrap（bwrap）配置。"""

            model_config = ConfigDict(extra="allow")

            bwrap_path: str = Field(default="bwrap")
            unshare_net: bool = True

        mode: str = Field(default="auto")  # auto|none|seatbelt|bubblewrap
        seatbelt: Seatbelt = Field(default_factory=Seatbelt)
        bubblewrap: Bubblewrap = Field(default_factory=Bubblewrap)

    default_policy: str = Field(default="none")  # none|restricted
    os: Os = Field(default_factory=Os)


class AgentSdkRunConfig(BaseModel):
    """运行参数（最小集合）。"""

    model_config = ConfigDict(extra="allow")

    class ContextRecovery(BaseModel):
        """
        上下文恢复策略（context_length_exceeded）。

        说明：
        - `mode` 默认 `fail_fast`，保证不改变既有行为（兼容）。
        - 其它字段用于 `compact_first/ask_first` 的可控性与可回归性（内部生产）。
        """

        model_config = ConfigDict(extra="allow")

        mode: str = Field(default="fail_fast")  # compact_first|ask_first|fail_fast
        max_compactions_per_run: int = Field(default=2, ge=0)
        # ask_first 无 human provider 时的确定性降级：compact_first 或 fail_fast
        ask_first_fallback_mode: str = Field(default="compact_first")

        # compaction turn 的输入裁剪参数（按字符数与消息数保守控制）
        compaction_history_max_chars: int = Field(default=24_000, ge=1_000)
        compaction_keep_last_messages: int = Field(default=6, ge=0)

        # “提高预算继续”的扩容策略（最小集合）
        increase_budget_extra_steps: int = Field(default=20, ge=0)
        increase_budget_extra_wall_time_sec: int = Field(default=600, ge=0)

    max_steps: int = Field(default=40, ge=1)
    max_wall_time_sec: Optional[int] = Field(default=None, ge=1)
    human_timeout_ms: Optional[int] = Field(default=None, ge=1)
    resume_strategy: str = Field(default="summary")  # summary|replay
    context_recovery: ContextRecovery = Field(default_factory=ContextRecovery)


class AgentSdkSafetyConfig(BaseModel):
    """
    Safety 配置（Guard + Policy + Approvals）。

    参考文档：
    - `help/06-tools-and-safety.md`
    - `help/02-config-reference.md`

    说明：
    - `mode=ask` 时依赖 `ApprovalProvider` 完成审批交互；若 provider 缺失则按 denied 处理（保守策略）。
    - `approval_timeout_ms` 用于限制“等待人类审批”的最长时间，超时按 denied 处理。
    """

    model_config = ConfigDict(extra="allow")

    mode: str = Field(default="ask")  # allow|ask|deny
    allowlist: List[str] = Field(default_factory=list)
    denylist: List[str] = Field(default_factory=list)
    tool_allowlist: List[str] = Field(default_factory=list)
    tool_denylist: List[str] = Field(default_factory=list)
    approval_timeout_ms: int = Field(default=60_000, ge=1)


class AgentSdkSkillsConfig(BaseModel):
    """Skills 配置（兼容旧配置 + V2 spaces/sources/injection）。"""

    model_config = ConfigDict(extra="allow")

    class Versioning(BaseModel):
        """
        Skills 版本控制配置（占位）。

        说明：
        - 当前仅做配置模型占位与校验，运行时不会改变 SkillsManager 行为。
        - 允许额外字段（fail-open），便于后续扩展策略参数而不破坏旧配置。
        """

        model_config = ConfigDict(extra="allow")

        enabled: StrictBool = False
        strategy: str = "TODO"

    class Strictness(BaseModel):
        """Skills 严格模式配置（V2 固定约束，可读性字段）。"""

        model_config = ConfigDict(extra="allow")

        unknown_mention: str = Field(default="error")
        duplicate_name: str = Field(default="error")
        mention_format: str = Field(default="strict")

    class Space(BaseModel):
        """Skills space 配置。"""

        model_config = ConfigDict(extra="allow")

        id: str
        account: str
        domain: str
        sources: List[str] = Field(default_factory=list)
        enabled: bool = True

    class Source(BaseModel):
        """Skills source 配置。"""

        model_config = ConfigDict(extra="allow")

        id: str
        type: str
        options: Dict[str, Any] = Field(default_factory=dict)

    class Injection(BaseModel):
        """Skills 注入配置。"""

        model_config = ConfigDict(extra="allow")

        max_bytes: Optional[int] = Field(default=None, ge=1)

    class Actions(BaseModel):
        """Skills actions（skill_exec）能力开关。"""

        model_config = ConfigDict(extra="allow")

        enabled: bool = False

    class References(BaseModel):
        """Skills references（skill_ref_read）能力开关与读取限制。"""

        model_config = ConfigDict(extra="allow")

        enabled: bool = False
        allow_assets: bool = False
        default_max_bytes: int = Field(default=64 * 1024, ge=1)

    roots: List[str] = Field(default_factory=list)
    mode: str = Field(default="explicit")  # explicit|auto
    max_auto: int = Field(default=0, ge=0)
    versioning: Versioning = Field(default_factory=Versioning)
    strictness: Strictness = Field(default_factory=Strictness)
    spaces: List[Space] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    injection: Injection = Field(default_factory=Injection)
    actions: Actions = Field(default_factory=Actions)
    references: References = Field(default_factory=References)


class AgentSdkPromptHistoryConfig(BaseModel):
    """对话历史滑窗配置。"""

    model_config = ConfigDict(extra="allow")

    max_messages: int = Field(default=40, ge=1)
    max_chars: int = Field(default=120_000, ge=1)


class AgentSdkPromptConfig(BaseModel):
    """Prompt 配置（模板来源 + 注入开关）。"""

    model_config = ConfigDict(extra="allow")

    template: str = Field(default="default")
    system_text: Optional[str] = None
    developer_text: Optional[str] = None
    system_path: Optional[str] = None
    developer_path: Optional[str] = None
    include_skills_list: bool = True
    include_cwd_tree: bool = False
    history: AgentSdkPromptHistoryConfig = Field(default_factory=AgentSdkPromptHistoryConfig)


class AgentSdkConfig(BaseModel):
    """SDK 配置根对象（M1 最小字段 + 允许扩展）。"""

    model_config = ConfigDict(extra="allow")

    config_version: int = Field(default=1, ge=1)
    run: AgentSdkRunConfig
    safety: AgentSdkSafetyConfig = Field(default_factory=AgentSdkSafetyConfig)
    llm: AgentSdkLlmConfig
    models: AgentSdkModelsConfig
    sandbox: AgentSdkSandboxConfig = Field(default_factory=AgentSdkSandboxConfig)
    skills: AgentSdkSkillsConfig = Field(default_factory=AgentSdkSkillsConfig)
    prompt: AgentSdkPromptConfig = Field(default_factory=AgentSdkPromptConfig)


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    """读取 YAML 文件为 dict；空文件返回空 dict。"""

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件根节点必须为 mapping(dict)：{path}")
    return data


def load_config_dicts(config_dicts: list[Dict[str, Any]]) -> AgentSdkConfig:
    """
    加载并合并多个 dict 配置，返回校验后的 `AgentSdkConfig`。

    参数：
    - config_dicts：按顺序做深度合并（后者覆盖前者）
    """

    merged: Dict[str, Any] = {}
    for overlay in config_dicts:
        if not overlay:
            continue
        _deep_merge(merged, overlay)
    return AgentSdkConfig.model_validate(merged)


def load_config(config_paths: list[Path]) -> AgentSdkConfig:
    """
    加载并合并多个配置文件，返回校验后的 `AgentSdkConfig`。

    参数：
    - config_paths：YAML 路径列表；按顺序合并（后者覆盖前者）
    """

    overlays: list[Dict[str, Any]] = []
    for path in config_paths:
        overlays.append(_load_yaml_file(Path(path)))
    return load_config_dicts(overlays)
