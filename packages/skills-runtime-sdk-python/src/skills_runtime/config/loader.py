"""
配置加载器（YAML）。

参考文档：
- 配置概览与示例：`help/02-config-reference.md`
- Tools/Safety/Sandbox 心智模型：`help/06-tools-and-safety.md`
- Sandbox best practices：`help/sandbox-best-practices.md` / `help/sandbox-best-practices.cn.md`
- 默认配置：`packages/skills-runtime-sdk-python/src/skills_runtime/assets/default.yaml`

设计目标（M1）：
- 支持加载多个 YAML，并按顺序做深度合并（后者覆盖前者）。
- 使用 pydantic 做 schema 校验；默认拒绝未知字段（避免拼写错误与误配置被静默吞掉）。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Literal, Mapping, MutableMapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

_SEGMENT_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])$")
_NAMESPACE_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])(?::[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])){0,6}$"
)


def _is_valid_namespace(value: str) -> bool:
    """校验 namespace（1..7 个有序 segments，每段长度 2..64）。"""

    if not isinstance(value, str) or not _NAMESPACE_RE.match(value):
        return False
    return all(_SEGMENT_SLUG_RE.match(segment) for segment in value.split(":"))


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

    model_config = ConfigDict(extra="forbid")

    class Retry(BaseModel):
        """
        LLM 重试/退避策略（生产级可控）。

        说明：
        - base/cap/jitter 只影响“无 Retry-After 头”时的指数退避计算。
        """

        model_config = ConfigDict(extra="forbid")

        max_retries: int = Field(default=3, ge=0)
        base_delay_sec: float = Field(default=0.5, ge=0.0)
        cap_delay_sec: float = Field(default=8.0, ge=0.0)
        jitter_ratio: float = Field(default=0.1, ge=0.0, le=1.0)

    base_url: str
    api_key_env: str
    timeout_sec: int = Field(default=60, ge=1)
    retry: Retry = Field(default_factory=Retry)


class AgentSdkModelsConfig(BaseModel):
    """模型选择（planner/executor）。"""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    class Os(BaseModel):
        """平台 sandbox backend 选择与参数。"""

        model_config = ConfigDict(extra="forbid")

        class Seatbelt(BaseModel):
            """macOS seatbelt（sandbox-exec）配置。"""

            model_config = ConfigDict(extra="forbid")

            profile: str = Field(default="(version 1) (allow default)")

        class Bubblewrap(BaseModel):
            """Linux bubblewrap（bwrap）配置。"""

            model_config = ConfigDict(extra="forbid")

            bwrap_path: str = Field(default="bwrap")
            unshare_net: bool = True

        mode: Literal["auto", "none", "seatbelt", "bubblewrap"] = Field(default="auto")
        seatbelt: Seatbelt = Field(default_factory=Seatbelt)
        bubblewrap: Bubblewrap = Field(default_factory=Bubblewrap)

    default_policy: Literal["none", "restricted"] = Field(default="none")
    # 高层 profile（dev/balanced/prod）：用于“分阶段收紧”的可配置入口（必须可回归）。
    profile: Literal["dev", "balanced", "prod"] = Field(default="dev")
    os: Os = Field(default_factory=Os)


class AgentSdkRunConfig(BaseModel):
    """运行参数（最小集合）。"""

    model_config = ConfigDict(extra="forbid")

    class ContextRecovery(BaseModel):
        """
        上下文恢复策略（context_length_exceeded）。

        说明：
        - `mode` 默认 `fail_fast`，保证不改变既有行为（兼容）。
        - 其它字段用于 `compact_first/ask_first` 的可控性与可回归性（内部生产）。
        """

        model_config = ConfigDict(extra="forbid")

        mode: Literal["compact_first", "ask_first", "fail_fast"] = Field(default="fail_fast")
        max_compactions_per_run: int = Field(default=2, ge=0)
        # ask_first 无 human provider 时的确定性降级：compact_first 或 fail_fast
        ask_first_fallback_mode: Literal["compact_first", "fail_fast"] = Field(default="compact_first")

        # compaction turn 的输入裁剪参数（按字符数与消息数保守控制）
        compaction_history_max_chars: int = Field(default=24_000, ge=1_000)
        compaction_keep_last_messages: int = Field(default=6, ge=0)

        # “提高预算继续”的扩容策略（最小集合）
        increase_budget_extra_steps: int = Field(default=20, ge=0)
        increase_budget_extra_wall_time_sec: int = Field(default=600, ge=0)

    max_steps: int = Field(default=40, ge=1)
    max_wall_time_sec: Optional[int] = Field(default=None, ge=1)
    human_timeout_ms: Optional[int] = Field(default=None, ge=1)
    resume_strategy: Literal["summary", "replay"] = Field(default="summary")
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

    model_config = ConfigDict(extra="forbid")

    mode: Literal["allow", "ask", "deny"] = Field(default="ask")
    allowlist: List[str] = Field(default_factory=list)
    denylist: List[str] = Field(default_factory=list)
    tool_allowlist: List[str] = Field(default_factory=list)
    tool_denylist: List[str] = Field(default_factory=list)
    approval_timeout_ms: int = Field(default=60_000, ge=1)


class AgentSdkSkillsConfig(BaseModel):
    """Skills 配置（spaces/sources/injection 为唯一入口）。"""

    model_config = ConfigDict(extra="forbid")

    class Bundles(BaseModel):
        """
        Skills bundles（Phase 3 资产：actions/references）的运行时预算与缓存策略。

        说明：
        - 仅影响 bundle-backed 的 Phase 3 工具路径（例如 Redis bundles）；
        - 默认值应偏保守（fail-closed），避免大对象导致的内存/磁盘/延迟风险；
        - cache_dir 为 runtime-owned 目录（可安全删除并重建）。
        """

        model_config = ConfigDict(extra="forbid")

        max_bytes: StrictInt = Field(default=1 * 1024 * 1024, ge=1)
        cache_dir: str = Field(default=".skills_runtime_sdk/bundles")

    class Versioning(BaseModel):
        """
        Skills 版本控制配置（占位）。

        说明：
        - 当前仅做配置模型占位与校验，运行时不会改变 SkillsManager 行为。
        - 严格拒绝未知字段（extra=forbid），避免误配置被静默忽略。
        """

        model_config = ConfigDict(extra="forbid")

        enabled: StrictBool = False
        strategy: str = "TODO"

    class Strictness(BaseModel):
        """Skills 严格模式配置（固定约束，可读性字段）。"""

        model_config = ConfigDict(extra="forbid")

        unknown_mention: str = Field(default="error")
        duplicate_name: str = Field(default="error")
        mention_format: str = Field(default="strict")

    class Space(BaseModel):
        """Skills space 配置。"""

        model_config = ConfigDict(extra="forbid")

        id: str
        namespace: str
        sources: List[str] = Field(default_factory=list)
        enabled: bool = True

        @model_validator(mode="before")
        @classmethod
        def _reject_legacy_fields(cls, data: Any) -> Any:
            """显式拒绝历史二段式空间键字段（不提供兼容层）。"""

            if isinstance(data, Mapping):
                legacy = [k for k in ("account", "domain") if k in data]
                if legacy:
                    fields = ",".join(legacy)
                    raise ValueError(
                        f"skills.spaces[] only accepts namespace; legacy fields are forbidden: {fields}"
                    )
            return data

        @field_validator("namespace")
        @classmethod
        def _validate_namespace(cls, value: str) -> str:
            """校验 namespace（1..7 segments，segment slug 2..64）。"""

            if not _is_valid_namespace(value):
                raise ValueError("skills.spaces[].namespace is invalid")
            return value

    class Source(BaseModel):
        """Skills source 配置。"""

        model_config = ConfigDict(extra="forbid")

        id: str
        type: str
        options: Dict[str, Any] = Field(default_factory=dict)

    class Injection(BaseModel):
        """Skills 注入配置。"""

        model_config = ConfigDict(extra="forbid")

        max_bytes: Optional[int] = Field(default=None, ge=1)

    class Actions(BaseModel):
        """Skills actions（skill_exec）能力开关。"""

        model_config = ConfigDict(extra="forbid")

        enabled: bool = False

    class References(BaseModel):
        """Skills references（skill_ref_read）能力开关与读取限制。"""

        model_config = ConfigDict(extra="forbid")

        enabled: bool = False
        allow_assets: bool = False
        default_max_bytes: int = Field(default=64 * 1024, ge=1)

    class Scan(BaseModel):
        """Skills 扫描参数（必须可回归；拒绝隐式扩展字段）。"""

        model_config = ConfigDict(extra="forbid")

        ignore_dot_entries: StrictBool = True
        max_depth: StrictInt = Field(default=99, ge=0)
        max_dirs_per_root: StrictInt = Field(default=100000, ge=0)
        max_frontmatter_bytes: StrictInt = Field(default=65536, ge=1)

        refresh_policy: Literal["always", "ttl", "manual"] = Field(default="always")
        ttl_sec: StrictInt = Field(default=300, ge=1)

    # skill 依赖的 env var 缺失时的处理策略（云端无人值守建议 fail_fast 或 skip_skill）。
    env_var_missing_policy: Literal["fail_fast", "ask_human", "skip_skill"] = Field(default="ask_human")
    versioning: Versioning = Field(default_factory=Versioning)
    strictness: Strictness = Field(default_factory=Strictness)
    spaces: List[Space] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    scan: Scan = Field(default_factory=Scan)
    injection: Injection = Field(default_factory=Injection)
    bundles: Bundles = Field(default_factory=Bundles)
    actions: Actions = Field(default_factory=Actions)
    references: References = Field(default_factory=References)


class AgentSdkPromptHistoryConfig(BaseModel):
    """对话历史滑窗配置。"""

    model_config = ConfigDict(extra="forbid")

    max_messages: int = Field(default=40, ge=1)
    max_chars: int = Field(default=120_000, ge=1)


class AgentSdkPromptConfig(BaseModel):
    """Prompt 配置（模板来源 + 注入开关）。"""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

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
    _apply_sandbox_profile_overrides(merged)
    return AgentSdkConfig.model_validate(merged)


def _apply_sandbox_profile_overrides(merged: Dict[str, Any]) -> None:
    """
    将 `sandbox.profile`（dev/balanced/prod）展开为具体字段。

    约束：
    - `sandbox.profile` 仅允许 `dev|balanced|prod`；缺失则默认 `dev`；未知值必须 fail-fast（不得静默 no-op）。
    - profile 的展开结果会覆盖 `sandbox.default_policy` 与 `sandbox.os.*`（profile 是更高层的“宏”）。
    """

    sandbox = merged.get("sandbox")
    if not isinstance(sandbox, dict):
        return
    raw = sandbox.get("profile", "dev")
    profile = str(raw or "").strip().lower()
    if not profile:
        raise ValueError("sandbox.profile must be one of: dev|balanced|prod")

    # 约定：三档 profile 的目标是从“可跑通”→“平衡”→“更偏生产硬化”，并提供可回归的默认值。
    # 注意：seatbelt/bwrap 的细节策略仍建议通过 overlay 精细化；profile 只提供可复用的基线。
    presets: Dict[str, Dict[str, Any]] = {
        # dev：优先可用性（与默认配置保持一致，避免“默认更严格”造成误拦截）
        "dev": {
            "default_policy": "none",
            "os": {"mode": "auto", "seatbelt": {"profile": "(version 1) (allow default)"}, "bubblewrap": {"unshare_net": True}},
        },
        # balanced：推荐默认（restricted + auto backend；Linux 默认隔离网络）
        "balanced": {
            "default_policy": "restricted",
            "os": {"mode": "auto", "seatbelt": {"profile": "(version 1) (allow default)"}, "bubblewrap": {"unshare_net": True}},
        },
        # prod：更偏生产硬化（在 macOS 上提供一个“更可见的 deny 基线”，Linux 保持 unshare-net）
        "prod": {
            "default_policy": "restricted",
            "os": {
                "mode": "auto",
                "seatbelt": {
                    "profile": "(version 1)\n(allow default)\n; prod baseline: visible deny under /etc (adjust via overlay if needed)\n(deny file-read* (subpath \"/etc\"))\n(deny file-write* (subpath \"/etc\"))\n"
                },
                "bubblewrap": {"unshare_net": True},
            },
        },
    }

    preset = presets.get(profile)
    if preset is None:
        raise ValueError(f"sandbox.profile must be one of: {sorted(presets.keys())}; got: {profile}")

    # profile 展开：覆盖到 sandbox 下（宏级别优先）
    _deep_merge(sandbox, preset)


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
