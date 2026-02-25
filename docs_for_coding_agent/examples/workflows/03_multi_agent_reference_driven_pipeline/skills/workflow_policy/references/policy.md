# Patch Policy（示例）

本文件用于演示：把可复用规则放入 skill bundle 的 `references/`，在运行期用 `skill_ref_read` 读取。

规则（示例）：
1. Patch 必须是最小改动：只修复 bug，不做无关重排与格式化。
2. Patch 必须可回归：提供一个可执行的最小 QA 断言（推荐 `python -c ...`）。
3. 报告必须提供证据指针：每个子 agent 的 `wal_locator`。

