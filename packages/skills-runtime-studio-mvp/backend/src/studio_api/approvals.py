from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest


@dataclass(frozen=True)
class PendingApproval:
    """
    一次待审批请求（Studio 产品层）。

    说明：
    - SDK 发出 `approval_requested` 事件后，会 await ApprovalProvider 的 decision；
    - Studio 通过该结构把“等待中的 approval_key”与 asyncio Future 绑定；
    - 前端通过 API 回传 decision 后，后端将 Future resolve。
    """

    run_id: str
    approval_key: str
    tool: str
    summary: str
    request: Dict[str, object]
    created_at_monotonic: float
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[ApprovalDecision]


def _parse_decision(value: str) -> ApprovalDecision:
    v = str(value or "").strip().lower()
    if v == "approved":
        return ApprovalDecision.APPROVED
    if v in ("approved_for_session", "approved-session", "approved_for_sess"):
        return ApprovalDecision.APPROVED_FOR_SESSION
    if v == "denied":
        return ApprovalDecision.DENIED
    if v == "abort":
        return ApprovalDecision.ABORT
    raise ValueError("invalid decision")


class ApprovalHub:
    """
    approvals 中枢（进程内，MVP 最小实现）。

    约束：
    - 进程重启会丢失 pending approvals（MVP 可接受）。
    - key 由 `(run_id, approval_key)` 唯一定位，避免不同 run 冲突。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[Tuple[str, str], PendingApproval] = {}

    def provider_for_run(self, *, run_id: str) -> ApprovalProvider:
        """为某个 run 构造一个 run-scoped ApprovalProvider。"""

        return _RunScopedApprovalProvider(hub=self, run_id=run_id)

    def list_pending(self, *, run_id: str) -> List[Dict[str, object]]:
        """列出某个 run 的 pending approvals（用于前端刷新/断线恢复）。"""

        with self._lock:
            items = [p for (rid, _k), p in self._pending.items() if rid == run_id]
        items_sorted = sorted(items, key=lambda p: p.created_at_monotonic)
        return [
            {
                "run_id": p.run_id,
                "approval_key": p.approval_key,
                "tool": p.tool,
                "summary": p.summary,
                "request": p.request,
                "age_ms": int((time.monotonic() - p.created_at_monotonic) * 1000),
            }
            for p in items_sorted
        ]

    def _register(self, *, run_id: str, req: ApprovalRequest) -> PendingApproval:
        """
        注册一个 pending approval（若已存在则复用）。

        注意：
        - 同一 approval_key 可能在模型重试下重复出现；复用可避免泄露 future。
        """

        key = (run_id, req.approval_key)
        loop = asyncio.get_running_loop()

        with self._lock:
            existing = self._pending.get(key)
            if existing is not None and (not existing.future.done()):
                return existing

            fut: asyncio.Future[ApprovalDecision] = loop.create_future()
            pending = PendingApproval(
                run_id=run_id,
                approval_key=req.approval_key,
                tool=req.tool,
                summary=req.summary,
                request=req.details if isinstance(req.details, dict) else {"details": req.details},
                created_at_monotonic=time.monotonic(),
                loop=loop,
                future=fut,
            )
            self._pending[key] = pending
            return pending

    def decide(self, *, run_id: str, approval_key: str, decision: str) -> bool:
        """
        写入 decision（从任意线程调用）。

        返回：
        - True：找到 pending 并成功 resolve
        - False：未找到（不存在/过期/已完成）
        """

        key = (run_id, str(approval_key))
        with self._lock:
            pending = self._pending.get(key)
        if pending is None:
            return False

        try:
            parsed = _parse_decision(decision)
        except Exception:
            raise ValueError("invalid decision") from None

        def _resolve() -> None:
            try:
                if pending.future.done():
                    return
                pending.future.set_result(parsed)
            finally:
                with self._lock:
                    # best-effort 清理
                    cur = self._pending.get(key)
                    if cur is pending:
                        self._pending.pop(key, None)

        try:
            pending.loop.call_soon_threadsafe(_resolve)
        except Exception:
            # loop 已关闭等情况：直接清理并返回 False
            with self._lock:
                cur = self._pending.get(key)
                if cur is pending:
                    self._pending.pop(key, None)
            return False

        return True


class _RunScopedApprovalProvider(ApprovalProvider):
    """将 SDK 的 ApprovalProvider 协议适配到 Studio ApprovalHub（run 维度隔离）。"""

    def __init__(self, *, hub: ApprovalHub, run_id: str) -> None:
        self._hub = hub
        self._run_id = str(run_id)

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        pending = self._hub._register(run_id=self._run_id, req=request)
        if timeout_ms is None:
            return await pending.future
        return await asyncio.wait_for(pending.future, timeout=float(timeout_ms) / 1000.0)

