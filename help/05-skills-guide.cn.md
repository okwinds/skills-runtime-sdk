<div align="center">

[中文](05-skills-guide.cn.md) | [English](05-skills-guide.md) | [Help](README.cn.md)

</div>

# 05. Skills 指南：语法、扫描、注入、边界

## 5.1 唯一合法 mention 语法

```text
$[account:domain].skill_name
```

示例：

```text
请按 $[web:mvp].article-writer 生成 300 字文章
```

## 5.2 mention 容错与严格校验（要区分）

### 自由文本提取

- 只提取合法 mention
- 其它疑似片段按普通文本处理，不中断 run

### Tool 参数严格校验

当工具参数要求 `skill_mention` 时：
- 必须是“一个且仅一个”完整 mention token
- 不合法会报格式错误

## 5.3 slug 规则（简版）

- 仅使用小写字母、数字、中划线
- 避免空格、特殊字符、中文标点

## 5.4 Skills 发现与来源

框架支持多来源（按规格）：
- filesystem
- in-memory
- redis
- pgsql

生产常见组合：
- dev：filesystem
- prod：filesystem + redis/pgsql（按治理要求）

## 5.5 扫描策略（metadata-only）

核心思想：
- 扫描阶段只读 frontmatter/元信息
- body 在注入时按需读取（lazy-load）

相关字段：
- `skills.scan.max_depth`
- `skills.scan.refresh_policy`
- `skills.scan.max_frontmatter_bytes`
- `skills.injection.max_bytes`

## 5.6 Skills CLI 实操

### preflight

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills preflight \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

### scan

```bash
PYTHONPATH=packages/skills-runtime-sdk-python/src \
python3 -m agent_sdk.cli.main skills scan \
  --workspace-root . \
  --config help/examples/skills.cli.overlay.yaml \
  --pretty
```

## 5.7 Studio 创建 Skill（API）

接口：`POST /studio/api/v1/sessions/{session_id}/skills`

请求体：

```json
{
  "name": "article-writer",
  "description": "写作技能",
  "body_markdown": "# 使用说明\n...",
  "target_root": "<repo_root>/packages/skills-runtime-studio-mvp/backend/.skills_runtime_sdk/skills"
}
```

## 5.8 常见错误

- `target_root must be one of session roots`
- `validation_error`（name 不合法）
- skills 扫描到同名冲突

## 5.9 最佳实践

1. 统一命名规范，避免重名
2. 每个 skills root 做明确用途分层（system/business/experiment）
3. preflight/scan 作为 CI 门禁的一部分
4. mention 写法不要依赖“猜测式输入”

---

上一章：[04. CLI 参考](04-cli-reference.cn.md) · 下一章：[06. Tools + Safety](06-tools-and-safety.cn.md)
