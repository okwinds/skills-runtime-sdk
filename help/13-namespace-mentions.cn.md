<div align="center">

[中文](13-namespace-mentions.cn.md) | [English](13-namespace-mentions.md) | [Help](README.cn.md)

</div>

# 13. Namespace 多段 mention：中性、可扩展、顺序敏感

这套框架会把 Skills 的“空间键”定义为**中性**的 `namespace`，而不是限定场景的二段式业务命名。

## 13.1 唯一合法的 mention token 语法

```text
$[<namespace>].<skill_name>
```

其中：

- `<namespace>` MUST 由 `1..7` 段**有序** `segment` 组成，使用 `:` 分隔（例如 `org:team:project`）
- 每个 `segment` MUST 满足 slug 规则：小写字母/数字/中划线；字母/数字开头结尾；长度 `2..64`
- `<skill_name>` 沿用既有规则：小写字母/数字/中划线/下划线；字母/数字开头结尾；长度 `2..64`

示例：

```text
$[web].article-writer
$[web:mvp].article-writer
$[acme:platform:runtime].article-writer
```

## 13.2 为什么要用 `namespace`（而不是固定二段式业务命名）

把空间键写成固定二段式业务命名的问题在于：它会“偷换心智模型”，让框架看起来像是租户/域名/DNS 等特定业务概念，但实际常见表达可能是：

- `parent:child`（层级结构）
- `org:project` / `org:team:service`（组织/团队/工程路径）

同时，固定二段式也会在系统成长时成为结构性瓶颈：你很容易从 1 段→2 段→3 段，甚至更多段。

因此框架把契约升级为：

- **中性命名**：`namespace` + `segment`
- **可变段数**：`1..7` 段
- **顺序敏感**：把它视为有序路径

## 13.3 顺序语义（最佳实践）

`namespace` 被视为**有序路径（ordered path）**：

```text
$[a:b].x  !=  $[b:a].x
```

框架层面的理由：

- 若做“顺序无关”的 canonicalize（例如对 segments 排序），会引入不可避免的歧义与冲突，并降低可观测/可审计性。
- “容错/猜测/自动纠正”更适合由产品层（UI/交互）承担，runtime 契约应保持稳定与可验证。

建议（仅最佳实践，不是框架强制语义）：

- 尽早确定一套顺序约定（示例）：`org:team:project[:env][:region]...`
- 保持可读性，不要把业务语义硬塞进 runtime 契约；真正的业务含义由产品层定义即可。

## 13.4 常见坑位

### segment 最小长度

segment 最小长度是 **2**：

- ✅ `web`
- ❌ `a`

### 段数上限

最多 **7** 段：

- ✅ `$[a0:b1:c2:d3:e4:f5:g6].x`
- ❌ `$[a0:b1:c2:d3:e4:f5:g6:h7].x`（8 段）

### “自由文本容错” vs “工具参数严格校验”

- **自由文本提取**是容错的：只提取合法 token；不合法碎片按普通文本忽略，不会中断 run。
- **Tool 参数**若声明为 `skill_mention` 则是严格的：参数值必须“一个且仅一个”完整 token，否则 fail-fast。

## 13.5 迁移说明（强硬升级 / 不兼容）

本次升级是**刻意的不兼容变更**：

- 旧的二段式 mention 写法不再支持。
- 配置里旧的空间键字段会被拒绝；必须改为 `skills.spaces[].namespace`。

相关章节：
- mention 基础：`help/05-skills-guide.cn.md`
- 配置 schema：`help/02-config-reference.cn.md`
- 排障：`help/09-troubleshooting.cn.md`
