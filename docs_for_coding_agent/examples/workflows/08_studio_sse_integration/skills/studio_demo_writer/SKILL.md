---
name: studio_demo_writer
description: "Studio 集成演示：在 Studio backend 中执行最小副作用（写文件）以触发 approvals + SSE 事件。"
metadata:
  short-description: "Studio：file_write 触发 approval_requested，并通过 SSE 可观测。"
---

# studio_demo_writer（Studio / Integration Demo）

## 目标

用于 Studio 端到端集成演示：
- 在 Studio backend 的 workspace 中写入一个文件（触发 approvals）
- 让 SSE 流中出现 `approval_requested/approval_decided/tool_call_finished` 等证据事件

## 注意（namespace）

Studio MVP 当前会把 session skills roots 映射到固定 space：
- `namespace=web:mvp`

因此在 Studio 的 message 里应使用：

```text
$[web:mvp].studio_demo_writer
```

## 必须使用的工具

- `file_write`：写 `studio_demo_output.txt`

## 输出要求

- 写入 `studio_demo_output.txt`，内容包含稳定标记：`STUDIO_DEMO_OK`
