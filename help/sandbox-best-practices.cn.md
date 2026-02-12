<div align="center">

[中文](sandbox-best-practices.cn.md) | [English](sandbox-best-practices.md) | [Help](README.cn.md)

</div>

# Sandbox 最佳实践（SDK + Studio）

> 目标：默认不打扰正常操作，同时拦截异常/高风险操作。

## 1. 一句话理解

- **Approval（审批）像门卫**：先决定“要不要放行这个操作”。
- **Sandbox（沙箱）像围栏**：放行后，命令也只能在受限边界里执行。

这两层要同时存在，不能互相替代。

## 2. 推荐默认配置（平衡模式）

适用：大多数业务接入场景。

- `safety.mode: ask`
- `safety.allowlist`: 放行高频低风险命令
- `safety.denylist`: 直接拒绝高危命令
- `sandbox.default_policy: restricted`
- `sandbox.os.mode: auto`

## 3. Studio MVP 当前建议（macOS 开发 + Linux 生产）

当前落点文件：`packages/skills-runtime-studio-mvp/backend/config/runtime.yaml`

### 3.1 macOS（当前启用）

```yaml
sandbox:
  default_policy: "restricted"
  os:
    mode: "auto"
    seatbelt:
      # 最小可跑通（默认）：不打扰开发
      profile: |
        (version 1)
        (allow default)

      # 平衡示例（更偏生产；建议先在本机验证不会误拦截）：
      # profile: |
      #   (version 1)
      #   (allow default)
      #   ; 仅用于“可见限制”的最小 deny（示例）
      #   (deny file-read* (subpath "/etc"))
```

核验：

```bash
command -v sandbox-exec
```

### 3.2 Linux（生产示例，默认先注释）

```yaml
# sandbox:
#   default_policy: "restricted"
#   os:
#     mode: "auto"
#     bubblewrap:
#       bwrap_path: "bwrap"
#       unshare_net: true
```

核验：

```bash
command -v bwrap
```

## 4. 业务接入 SDK 的配置建议

### 场景 A：开发阶段，优先不打断

- 保持 `mode=ask`
- allowlist 尽量覆盖开发常用只读命令
- denylist 先拦截明显危险命令
- 默认仍可用 `restricted`，但 profile 不要一次收得太死

### 场景 B：生产阶段，优先稳与可审计

- 保持 `mode=ask`
- 逐步收紧 allowlist（只保留真实高频）
- denylist 保持保守，不给破坏性命令机会
- Linux 建议启用 `bubblewrap.unshare_net=true`

### 场景 C：误拦截排障

先看拒绝类型：

1. `sandbox_denied`：通常是适配器不可用（`sandbox-exec` / `bwrap` 缺失）或策略不满足。
2. `approval_denied`：通常是审批拒绝/超时。
3. `permission`：通常是路径越界（非 workspace root）。

## 5. 常见问题（FAQ）

### Q1：为什么我感觉“已经在沙箱里了”？

因为即使不看 OS sandbox，系统还有审批门禁与 workspace 边界限制；这两层本身就会显著改变执行体验。

### Q2：`restricted` 会不会影响正常命令？

会有可能，尤其在环境依赖不齐（如 `bwrap` 不存在）或 profile 太严时。建议先按“平衡模式”上线，再逐步收紧。

### Q3：能否临时放宽？

可以。短期应急可把 `sandbox.default_policy` 设为 `none`，但建议保留 `safety.mode=ask` + denylist，避免裸奔。

## 6. 最小核验清单（上线前）

- [ ] `sandbox-exec`（mac）/`bwrap`（linux）可用
- [ ] allowlist 覆盖高频正常命令
- [ ] denylist 含破坏性命令
- [ ] 能稳定返回并识别 `sandbox_denied` / `approval_denied`
- [ ] 回滚方案已记录（`restricted -> none`）

## 7. 如何验证“真沙箱”而不是“仅审批”

先看事件，不要只看 UI 体感：

1. **审批事件**：若出现 `approval_requested` / `approval_decided`，说明命中了门卫（policy gate）。
2. **沙箱错误**：若 `tool_call_finished.result.error_kind == sandbox_denied`，说明是沙箱层拒绝（例如 adapter 不可用/策略不满足）。
3. **沙箱已启用（不代表已严格限制）**：查看 `tool_call_finished.result.data.sandbox`：
   - `effective`：本次实际策略（`none|restricted`）
   - `active`：是否实际走了 sandbox adapter
   - `adapter`：适配器类型（例如 `SeatbeltSandboxAdapter`）

注意：
- 当前 macOS 示例 profile 为 `(allow default)`，它是“可跑通优先”的宽松 profile；
- 所以即使 `active=true`，你仍可能看到绝对路径等“看起来像宿主机”的输出。
- 想验证“限制真的在生效”，要用更严格 profile 做反例验证（例如刻意禁止某类文件访问）。

推荐两种“可见限制”验证方式（无需外网）：

1) 一键演示脚本（macOS/Linux 都支持）：

```bash
bash scripts/integration/os_sandbox_restriction_demo.sh
```

2) 离线回归测试（环境缺少 adapter 时会 skip）：

```bash
pytest -q packages/skills-runtime-sdk-python/tests/test_os_sandbox_restriction_effects.py
```

补充说明（避免误判）：
- macOS seatbelt 不会提供“容器路径假象”；你仍可能看到物理机绝对路径，这不等于没进沙箱。
- 要判断是否“真进 OS sandbox”，请看 `tool_call_finished.result.data.sandbox.active/adapter/effective`，不要只凭体感。
