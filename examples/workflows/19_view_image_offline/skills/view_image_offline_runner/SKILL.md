---
name: view_image_offline_runner
description: "离线演示：在 workspace 内生成小 PNG，并通过 view_image 读取返回 base64/mime/bytes，最后落盘 image_meta.json 与 report.md。"
metadata:
  short-description: "view_image：离线读本地图片 + 可审计 WAL + 产物落盘。"
---

# view_image_offline_runner（workflow / View Image Offline）

## 目标

在离线可回归的约束下，演示 `view_image` 的端到端使用方式：

- 读取 workspace 内的 `generated.png`
- 返回 `mime` / `bytes` / `base64`
- 产出 `image_meta.json` 与 `report.md`（都写入 workspace）

## 输入约定

- 你的任务文本中会包含 mention：`$[examples:workflow].view_image_offline_runner`。
- 图片文件必须已存在且位于 workspace 内（相对路径优先，例如 `generated.png`）。

## 必须使用的工具

- `view_image`：读取本地图片并返回 base64/mime/bytes（不依赖外网）
- `file_write`：落盘 `image_meta.json` / `report.md`（写操作，通常需要 approvals）

## 约束

- 默认离线：不使用外部网络，不要求真实 key。
- 路径边界：`view_image.path` 必须在 workspace 内，否则应被拒绝（permission）。
- 产物必须写在 workspace 内（相对路径优先）。

