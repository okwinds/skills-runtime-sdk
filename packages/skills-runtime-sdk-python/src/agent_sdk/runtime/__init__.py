"""
本地 Runtime 服务（跨进程持久化 exec sessions / collab child agents）。

对齐 backlog：
- BL-004：exec_command/write_stdin 跨进程会话持久化（CLI 作为常驻会话用法）
- BL-005：多 agent 协作的跨进程/可持久化运行时（工具级原语）

实现定位：
- 这是一个“本机单 workspace”的轻量服务，通过 Unix domain socket 提供 JSON RPC；
- 目标是让多次 CLI 调用或进程重启后仍能继续 write_stdin / wait / send_input；
- 不追求网络暴露/多租户；安全边界以 workspace_root 目录权限为主（socket 0600 + secret）。
"""

from __future__ import annotations

