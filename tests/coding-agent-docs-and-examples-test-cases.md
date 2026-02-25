# Test Cases: docs_for_coding_agent + Examples Library（编码智能体教学文档与示例库）

## Overview

- **Feature**：为 Skills Runtime SDK 建立全能力示例库 `examples/**` 与编码智能体教学文档 `docs_for_coding_agent/**`
- **Requirements Source**：`docs/specs/skills-runtime-sdk/docs/coding-agent-docs-and-examples.md`
- **Test Coverage**：
  - 自动化：example smoke tests（pytest）
  - 静态校验：文档索引可定位（DOCS_INDEX、docs_for_coding_agent/DOCS_INDEX）
  - 手工：真模型与容器沙箱可用性探测（可选）
- **Last Updated**：2026-02-15

---

## Requirements（按能力拆分）

### REQ-EX-01：`docs_for_coding_agent/` 完整入口与索引

- 必须包含：`README.md`、`DOCS_INDEX.md`、能力清单与覆盖图、cheatsheet、task-contract、testing-strategy、common-pitfalls。
- `DOCS_INDEX.md` 可快速定位到目录内所有关键材料。

### REQ-EX-02：`examples/` 分层组织与可运行性

- `examples/` 不允许无组织散落，必须按目录契约分层（step_by_step/tools/skills/state/studio/_shared）。
- 每个示例必须提供 README + 可运行脚本/命令。
- 默认离线可运行：不得强依赖外网与真实 key。

### REQ-EX-03：离线 smoke tests（门禁）

- 新增 pytest 覆盖：至少跑通 1) step_by_step 基础示例；2) tools 示例；3) skills 示例；4) state 示例。
- 断言：
  - 进程退出码为 0；
  - 输出包含稳定的关键标记（避免 brittle 长文本断言）。

### REQ-EX-04：Backlog 对齐与 TODO 清点

- 从 `docs/specs/**` 扫描 TODO/out-of-scope/未实现声明；
- 未入 `docs/backlog.md` 的 future TODO 必须形成新的 BL-*；
- 已交付事项必须保留到 done memo（不得完全消失）。

---

## Test Case Categories

### 1) Functional Tests（核心功能）

#### TC-F-EX-001：docs_for_coding_agent 目录存在且索引完整
- **Requirement**：REQ-EX-01
- **Priority**：High
- **Steps**：
  1. 检查 `docs_for_coding_agent/` 是否存在。
  2. 检查 `docs_for_coding_agent/DOCS_INDEX.md` 是否列出核心文件。
- **Expected**：
  - 目录与核心文件存在；
  - DOCS_INDEX 可定位关键材料。
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_examples_smoke.py::test_docs_for_coding_agent_assets_exist`

#### TC-F-EX-002：examples 目录分层齐全且每个示例有 README
- **Requirement**：REQ-EX-02
- **Priority**：High
- **Steps**：
  1. 检查 `examples/apps/`（面向人类的应用示例）、`examples/studio/`、`examples/_shared/` 是否存在。
  2. 检查 `docs_for_coding_agent/examples/`（面向编码智能体的示例库）是否存在，并包含按主题分层的子目录（`step_by_step/`、`tools/`、`skills/`、`state/`、`workflows/`）。
  3. 随机抽取每类至少 1 个示例目录，验证存在 `README.md`。
- **Expected**：
  - 分层目录存在；
  - 示例目录包含 README。
- **Automation**：`packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`

#### TC-F-EX-003：离线 smoke tests 能跑通一组示例脚本
- **Requirement**：REQ-EX-03
- **Priority**：High
- **Steps**：
  1. 运行 `pytest -q packages/skills-runtime-sdk-python/tests/test_examples_smoke.py`。
- **Expected**：
  - 所选示例脚本全部 exit code=0；
  - 输出包含关键标记。

---

### 2) Error Handling Tests（错误路径）

#### TC-ERR-EX-001：缺少集成环境变量时，集成示例应跳过而不是失败
- **Requirement**：REQ-EX-02
- **Priority**：Medium
- **Steps**：
  1. 运行带集成 gate 的示例（不设置 gate env）。
- **Expected**：
  - 以明确提示退出（exit=0）或 pytest 中被 skip；
  - 不进入离线门禁失败。

---

### 3) Governance Tests（治理一致性）

#### TC-GOV-EX-001：Backlog 覆盖 specs 中明确的 future TODO
- **Requirement**：REQ-EX-04
- **Priority**：High
- **Steps**：
  1. 从 `docs/specs/**` 搜索 `TODO/未实现/out-of-scope`。
  2. 人工核对：每个明确 future 条目在 `docs/backlog.md` 中有 BL-*。
- **Expected**：
  - 无“无 ID 遗留项”；done memo 保留历史证据入口。

---

## Test Coverage Matrix

| Requirement ID | Test Cases | Coverage Status |
|---|---|---|
| REQ-EX-01 | TC-F-EX-001 | ✓ Complete |
| REQ-EX-02 | TC-F-EX-002, TC-ERR-EX-001 | ✓ Complete（离线） |
| REQ-EX-03 | TC-F-EX-003 | ✓ Complete |
| REQ-EX-04 | TC-GOV-EX-001 | ⚠ Manual（建议后续脚本化） |
