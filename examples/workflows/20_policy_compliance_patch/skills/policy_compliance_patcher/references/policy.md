# Policy（示例：敏感信息合规）

本 policy 作为示例“可分发规则/标准”，用于演示 skill bundle `references/` 的用法。

## 规则

1) **禁止明文敏感 token**

在任何 workspace 产物（例如 `target.md`、`report.md`、`result.md`）中，不允许出现形如：
- `SECRET_TOKEN=<...>`
- `API_KEY=<...>`

如必须保留字段名，只允许写成：
- `SECRET_TOKEN=[REDACTED]`
- `API_KEY=[REDACTED]`

2) **最小补丁**

修复时优先使用 `apply_patch`，并确保补丁仅覆盖必要的行（不要重排/大范围格式化）。

## 输出要求

必须输出：
- `patch.diff`：用于审计的补丁文本（与 apply_patch 输入一致）
- `result.md`：修复结果摘要（必须说明是否已 redacted）
- `report.md`：包含 `events_path`（WAL 指针）与关键证据摘要

