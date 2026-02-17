---
name: policy_compliance_patcher
description: "读取 policy（skill_ref_read）并对 workspace 文件执行合规补丁（apply_patch + 产物落盘）。"
metadata:
  short-description: "Policy→Patch：skill_ref_read 读取可分发规则，apply_patch 修复并产出证据。"
---

# policy_compliance_patcher（workflow / Policy Compliance Patch）

## 目标

在离线可回归约束下，把“规则/政策”以可分发形态随 Skill 打包：
- policy 放在 `references/policy.md`
- 运行期通过 `skill_ref_read` 读取（默认禁用，需显式开启）
- 根据 policy 修复 workspace 里的目标文件，并落盘可审计产物

## 输入约定

- 任务文本中会包含 mention：`$[examples:workflow].policy_compliance_patcher`。
- workspace 内已存在 `target.md`，其中包含一个“政策禁止的敏感 token”。

## 必须使用的工具

- `skill_ref_read`：读取 `references/policy.md`（不需要 approvals，但默认 fail-closed）
- `read_file`：读取 `target.md`（只读）
- `apply_patch`：对 `target.md` 打最小补丁（写操作，通常需要 approvals）
- `file_write`：落盘 `patch.diff` / `result.md` / `report.md`（写操作，通常需要 approvals）

## References（可分发政策）

- `references/policy.md`：示例 policy（禁止在产物中保留明文敏感 token）

## 约束

- 默认离线：不访问外网，不依赖真实 key。
- 修复必须最小化：只替换敏感 token，不做无关格式化。

