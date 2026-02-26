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

### 字符集与大小写

mention 的语法刻意保持严格：
- 仅小写
- 仅允许稳定的 slug 字符集

这样更容易在产品/UI 层做校验，也能减少“看起来像但其实不是”的歧义。

### 段数上限

最多 **7** 段：

- ✅ `$[a0:b1:c2:d3:e4:f5:g6].x`
- ❌ `$[a0:b1:c2:d3:e4:f5:g6:h7].x`（8 段）

### “token 合法”不等于“一定能解析成功”

自由文本提取只负责“把语法合法的 token 找出来”；真正的解析/映射会更严格：

- mention 的 namespace 未配置或被禁用 → 失败：`SKILL_SPACE_NOT_CONFIGURED`
- mention 指向的 skill 在该 namespace 下不存在 → 失败：`SKILL_UNKNOWN`

最佳实践：把“写出一个合法 mention token”视为**明确请求注入该 skill**，不要随手写在不希望触发注入的地方。

### “自由文本容错” vs “工具参数严格校验”

- **自由文本提取**是容错的：只提取合法 token；不合法碎片按普通文本忽略，不会中断 run。
- **Tool 参数**若声明为 `skill_mention` 则是严格的：参数值必须“一个且仅一个”完整 token，否则 fail-fast。

### 一个常见 typo：会被刻意忽略

如果出现这种形态：

```text
$[web].article-writer]  # 多了一个右中括号
```

提取阶段会刻意忽略它，用来降低“复制粘贴括号不匹配导致误注入”的风险。

## 13.5 迁移说明（强硬升级 / 不兼容）

本次升级是**刻意的不兼容变更**：

- 旧的二段式 mention 写法不再支持。
- 配置里旧的空间键字段会被拒绝；必须改为 `skills.spaces[].namespace`。

相关章节：
- mention 基础：`help/05-skills-guide.cn.md`
- 配置 schema：`help/02-config-reference.cn.md`
- 排障：`help/09-troubleshooting.cn.md`

## 13.6 场景示例合集（排列组合 + 预期行为）

### 13.6.1 纯文本里的单个 mention

```text
请按 $[web].article-writer 的流程写一篇草稿。
```

预期行为：
- mention 会被提取
- skill 正文只会注入一次（即便后文重复出现）

### 13.6.2 一次请求里多个 mentions（顺序有意义）

```text
先用 $[web].outline-generator 做大纲，再用 $[web].article-writer 写正文。
```

预期行为：
- 按“首次出现顺序”提取与注入
- 注入顺序可预测，便于排障与复现

### 13.6.3 重复 mention 会被去重（first wins）

```text
$[web].article-writer …… 后面又写了一次 …… $[web].article-writer
```

预期行为：
- 只注入一次
- 避免浪费 token 预算

### 13.6.4 同名 skill，不同 namespace（显式优于猜测）

```text
$[docs].reviewer
$[code].reviewer
```

预期行为：
- 二者不同，因为唯一 key 是 `(namespace, skill_name)`
- 这是避免“全局同名冲突”的推荐方式

### 13.6.5 Markdown 里的 mention 仍然是 mention

mention 提取是对原始文本扫描，因此下面仍然会触发：

```md
- 用 $[web].article-writer 做 A
- 用 $[web:mvp].sanity-checker 做 B
```

最佳实践（UX）：产品文档/教程里如果不希望用户复制粘贴后触发注入，避免展示“完全合法的 mention token”。

### 13.6.6 如何“谈论 mention”但不触发注入（转义/规避写法）

如果你只是写文档，不希望它被当成真实 mention，可以避免出现精确的 `$[...].name` 序列，例如：

```text
$ [web].article-writer          # `$` 后加空格
$[web] .article-writer          # `.` 前加空格
$[web]\\.article-writer         # 转义点号
$[WEB].article-writer           # 大写使其不合法（若可接受）
```

注意：用反引号包裹（`` `$[web].x` ``）并不能阻止提取，因为提取是扫描原始文本。

### 13.6.7 token 看起来合法，但 namespace 未配置（会失败）

```text
$[acme:secret].article-writer
```

如果 `skills.spaces[]` 里没有配置 `namespace=acme:secret`（或该 space 被禁用），解析会失败。

修复：
- 补齐 space/source 配置（`skills.spaces[]` + `skills.sources[]`）
- 或把 mention 改成已配置 namespace

### 13.6.8 token 看起来合法，但 skill 不存在（会失败）

```text
$[web].does-not-exist
```

即便 token 语法合法，只要扫描索引里不存在该 `(namespace, skill_name)`，解析仍会失败。

修复：
- 确认 skill 确实存在于该 namespace 的 sources 中
- 开发态可用 `refresh_policy=always` 保持“改动即时可见”

## 13.7 最佳实践（安全 + 体验）

### 13.7.1 namespace 的设计建议

- 把 namespace 当成稳定、可读的路径（顺序敏感）。
- 优先表达“归属/结构”（org/team/project），少用短期/临时段。
- 只用必要深度；不要把敏感信息编码进 namespace。

### 13.7.2 产品/UI 的体验建议

- 做 mention 的自动补全与即时校验，避免用户手打。
- 未知 mention 在“发起 run 之前”就提示清楚（不要把错误推到运行时）。
- 提供可用 skills 列表/选择器，减少猜测。

### 13.7.3 注入预算与工程卫生

- skill 正文保持聚焦、可组合。
- 少量精准 skills 优于一次注入一大堆。
- 配置并关注 `skills.injection.max_bytes`，避免 prompt 体积失控。
