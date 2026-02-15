# 05_multi_agent_code_review_fix_qa_report（Review→Fix→QA→Report / Skills-First）

本示例把“代码评审 + 修复 + 回归 + 报告”做成一个可回归的多 agent 流水线，并强调 skill-first：

- Reviewer（只读）：读文件、指出问题与建议（不允许写）
- Fixer（写）：`apply_patch` 落盘最小补丁
- QA（执行）：`shell_exec` 跑最小断言
- Reporter（写）：`file_write` 输出 `report.md`（包含 evidence 指针）

约束：
- 离线可回归：Fake backend + scripted approvals
- 每个角色都必须有 Skill，任务文本带 mention 触发 `skill_injected`

## 如何运行

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
  python3 examples/workflows/05_multi_agent_code_review_fix_qa_report/run.py --workspace-root /tmp/srsdk-wf05
```

产物：
- `calc.py`（被修复）
- `report.md`（汇总报告）

