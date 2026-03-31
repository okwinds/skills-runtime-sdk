"""
Source client 注册表（连接生命周期管理）。

职责：
- 为 redis/pgsql source 提供 client 获取（优先注入，其次按 dsn_env 初始化）
- 缓存运行时创建的 client（避免每次 scan 重新连接）
- 提供 close() 释放运行时创建的 client

约束：
- 注入的 client（source_clients）由调用方管理，本类不关闭
- 运行时创建的 client（_runtime_clients）由本类管理，close() 时释放
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Dict, Optional

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.skills.sources._utils import source_dsn_from_env as _source_dsn_from_env
from skills_runtime.skills.sources.redis import (
    get_redis_client as _get_redis_client_impl,
)
from skills_runtime.skills.sources.pgsql import (
    get_pgsql_client as _get_pgsql_client_impl,
)


class SourceClientRegistry:
    """Source client 注册表。"""

    def __init__(
        self,
        *,
        source_clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        创建 source client 注册表。

        参数：
        - source_clients：调用方注入的 client（按 source_id 索引；不由本类管理生命周期）
        """
        self._source_clients: Dict[str, Any] = dict(source_clients or {})
        self._runtime_clients: Dict[str, Any] = {}

    def source_dsn_from_env(self, source: AgentSdkSkillsConfig.Source) -> str:
        """从环境变量读取 source DSN（fail-closed）。"""
        return _source_dsn_from_env(source)

    def get_redis_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """
        获取 redis client（优先注入，其次按 dsn_env 初始化并缓存）。

        参数：
        - source：source 配置（需要 id、options.dsn_env）
        """
        return _get_redis_client_impl(
            source=source,
            source_clients=self._source_clients,
            runtime_source_clients=self._runtime_clients,
            source_dsn_from_env=self.source_dsn_from_env,
        )

    def get_pgsql_client(self, source: AgentSdkSkillsConfig.Source) -> Any:
        """
        获取 pgsql client（优先注入，其次按 dsn_env 初始化）。

        参数：
        - source：source 配置（需要 id、options.dsn_env）
        """
        return _get_pgsql_client_impl(
            source=source,
            source_clients=self._source_clients,
            source_dsn_from_env=self.source_dsn_from_env,
        )

    @property
    def injected_clients(self) -> Dict[str, Any]:
        """返回注入 client 的只读快照。"""
        return dict(self._source_clients)

    def close(self) -> None:
        """
        释放运行时创建的 source client。

        约束：
        - 仅关闭 _runtime_clients 中的 client
        - 注入的 _source_clients 不关闭
        """
        clients = list(self._runtime_clients.values())
        self._runtime_clients.clear()
        for client in clients:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass

    def __enter__(self) -> "SourceClientRegistry":
        """上下文管理器入口。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """上下文管理器退出：释放运行态资源。"""
        _ = (exc_type, exc, tb)
        self.close()
