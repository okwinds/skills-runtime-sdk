"""
AgentBuilder：用于更安全、可复用地构造 Agent。

对齐 OpenSpec（本仓重构）：
- `openspec/changes/sdk-production-refactor-p0/specs/agent-builder/spec.md`

设计目标：
- 集成方不必手动拼接 Agent 的大量参数，减少“漏传/错传”；
- 支持注入生产级组件（wal_backend、approvals、event_hooks 等）；
- 提供一个云端无人值守 preset（不写死业务名词）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import yaml

from agent_sdk.core.agent import Agent, ChatBackend
from agent_sdk.safety.approvals import ApprovalProvider
from agent_sdk.safety.rule_approvals import RuleBasedApprovalProvider
from agent_sdk.skills.manager import SkillsManager
from agent_sdk.state.wal_protocol import WalBackend
from agent_sdk.tools.protocol import HumanIOProvider


def _stable_json_hash(obj: object) -> str:
    """
    计算一个“内容稳定”的短 hash（用于 overlay 文件名去重/可预测）。

    参数：
    - obj：任意可 JSON 序列化对象

    返回：
    - hash 前缀字符串（固定长度）
    """

    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    import hashlib

    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class AgentBuilder:
    """
    AgentBuilder：链式构造 Agent。

    说明：
    - Builder 只负责“组装/默认值/小范围 preset”，不改变 Agent 的核心语义；
    - Builder 产生的 overlay 文件不包含 secrets（仅策略/开关类字段）。
    """

    _workspace_root: Optional[Path] = None
    _backend: Optional[ChatBackend] = None
    _config_paths: List[Path] = field(default_factory=list)
    _config_overlay_dicts: List[Dict[str, Any]] = field(default_factory=list)

    _model: Optional[str] = None
    _planner_model: Optional[str] = None
    _executor_model: Optional[str] = None

    _skills_manager: Optional[SkillsManager] = None
    _env_vars: Optional[Dict[str, str]] = None

    _human_io: Optional[HumanIOProvider] = None
    _approval_provider: Optional[ApprovalProvider] = None
    _cancel_checker: Optional[Callable[[], bool]] = None

    _wal_backend: Optional[WalBackend] = None
    _event_hooks: Optional[Sequence[Callable[..., None]]] = None

    def workspace_root(self, root: Path) -> "AgentBuilder":
        """
        设置 workspace_root。

        参数：
        - root：工作区根目录（会 resolve）
        """

        self._workspace_root = Path(root).resolve()
        return self

    def backend(self, backend: ChatBackend) -> "AgentBuilder":
        """
        设置 LLM backend。

        参数：
        - backend：实现 ChatBackend 协议的实例
        """

        self._backend = backend
        return self

    def config_paths(self, paths: Sequence[Path]) -> "AgentBuilder":
        """
        覆盖 config_paths（overlay YAML 路径列表）。

        参数：
        - paths：按顺序合并（后者覆盖前者）
        """

        self._config_paths = [Path(p).resolve() for p in paths]
        return self

    def add_config_path(self, path: Path) -> "AgentBuilder":
        """
        追加一个 overlay YAML 路径。

        参数：
        - path：YAML 文件路径（会 resolve）
        """

        self._config_paths.append(Path(path).resolve())
        return self

    def add_config_overlay(self, overlay: Dict[str, Any]) -> "AgentBuilder":
        """
        追加一个“内存 overlay dict”（build 时会 materialize 为文件并加入 config_paths）。

        参数：
        - overlay：配置 dict（不得包含 secrets）
        """

        self._config_overlay_dicts.append(dict(overlay or {}))
        return self

    def model(self, model: str) -> "AgentBuilder":
        """
        设置 model（兼容入口；默认作为 executor_model）。

        参数：
        - model：模型名字符串
        """

        self._model = str(model)
        return self

    def planner_model(self, model: str) -> "AgentBuilder":
        """
        设置 planner_model。

        参数：
        - model：模型名字符串
        """

        self._planner_model = str(model)
        return self

    def executor_model(self, model: str) -> "AgentBuilder":
        """
        设置 executor_model。

        参数：
        - model：模型名字符串
        """

        self._executor_model = str(model)
        return self

    def skills_manager(self, manager: SkillsManager) -> "AgentBuilder":
        """
        注入 SkillsManager（用于复用 scan 缓存/自定义来源策略）。

        参数：
        - manager：SkillsManager 实例
        """

        self._skills_manager = manager
        return self

    def env_vars(self, env_vars: Dict[str, str]) -> "AgentBuilder":
        """
        设置 session-only env_store（仅内存，不落盘）。

        参数：
        - env_vars：环境变量字典（value 不会写入事件/WAL）
        """

        self._env_vars = dict(env_vars or {})
        return self

    def human_io(self, human_io: Optional[HumanIOProvider]) -> "AgentBuilder":
        """
        注入 HumanIOProvider（用于 ask_human/env_var 收集等交互）。

        参数：
        - human_io：人类输入适配器；无人值守可传 None
        """

        self._human_io = human_io
        return self

    def approval_provider(self, provider: Optional[ApprovalProvider]) -> "AgentBuilder":
        """
        注入 ApprovalProvider（用于危险工具的审批决策）。

        参数：
        - provider：审批提供者；无人值守推荐 RuleBasedApprovalProvider
        """

        self._approval_provider = provider
        return self

    def cancel_checker(self, cancel_checker: Optional[Callable[[], bool]]) -> "AgentBuilder":
        """
        注入 cancel_checker（Stop/Cancel 支持）。

        参数：
        - cancel_checker：返回 True 表示取消；异常时应 fail-open
        """

        self._cancel_checker = cancel_checker
        return self

    def wal_backend(self, backend: Optional[WalBackend]) -> "AgentBuilder":
        """
        注入 WalBackend（用于 WAL 持久化/回放）。

        参数：
        - backend：WAL 后端；为 None 时使用默认 JsonlWal（文件型）
        """

        self._wal_backend = backend
        return self

    def event_hooks(self, hooks: Optional[Sequence[Callable[..., None]]]) -> "AgentBuilder":
        """
        设置事件 hooks（可观测性）。

        参数：
        - hooks：callable 列表（接收 AgentEvent；顺序与 stream 输出一致）
        """

        self._event_hooks = hooks
        return self

    @classmethod
    def cloud_unattended_preset(
        cls,
        *,
        workspace_root: Path,
        backend: ChatBackend,
        wal_backend: WalBackend,
        approval_provider: Optional[ApprovalProvider] = None,
        event_hooks: Optional[Sequence[Callable[..., None]]] = None,
    ) -> "AgentBuilder":
        """
        云端无人值守 preset（推荐）。

        行为目标：
        - env var 缺失：fail_fast（不请求人类输入）
        - approvals：使用规则审批（默认 DENIED，fail-closed；无交互）
        - WAL：必须显式注入（例如远端/内存/集中式后端）
        """

        overlay = {
            "config_version": 1,
            "skills": {"env_var_missing_policy": "fail_fast"},
            "safety": {"mode": "ask"},
        }

        provider = approval_provider or RuleBasedApprovalProvider(rules=[])
        return (
            cls()
            .workspace_root(workspace_root)
            .backend(backend)
            .wal_backend(wal_backend)
            .approval_provider(provider)
            .event_hooks(event_hooks)
            .add_config_overlay(overlay)
        )

    def _materialize_overlay_files(self) -> List[Path]:
        """把 overlay dict 写入 workspace 下的稳定路径，并返回路径列表。"""

        if not self._config_overlay_dicts:
            return []
        if self._workspace_root is None:
            raise ValueError("workspace_root is required to materialize overlays")

        out_paths: List[Path] = []
        base_dir = (self._workspace_root / ".skills_runtime_sdk" / "builder_overlays").resolve()
        base_dir.mkdir(parents=True, exist_ok=True)

        for overlay in self._config_overlay_dicts:
            h = _stable_json_hash(overlay)
            path = base_dir / f"overlay_{h}.yaml"
            text = yaml.safe_dump(overlay, sort_keys=True, allow_unicode=True).rstrip() + "\n"
            path.write_text(text, encoding="utf-8")
            out_paths.append(path)
        return out_paths

    def build(self) -> Agent:
        """
        构造 Agent。

        异常：
        - ValueError：当缺少必需字段（workspace_root/backend）时抛出。
        """

        if self._workspace_root is None:
            raise ValueError("workspace_root is required")
        if self._backend is None:
            raise ValueError("backend is required")

        overlay_paths = self._materialize_overlay_files()
        config_paths = [*self._config_paths, *overlay_paths]

        return Agent(
            model=self._model,
            planner_model=self._planner_model,
            executor_model=self._executor_model,
            workspace_root=self._workspace_root,
            skills_manager=self._skills_manager,
            env_vars=self._env_vars,
            backend=self._backend,
            config_paths=config_paths or None,
            human_io=self._human_io,
            approval_provider=self._approval_provider,
            cancel_checker=self._cancel_checker,
            wal_backend=self._wal_backend,
            event_hooks=self._event_hooks,  # type: ignore[arg-type]
        )
