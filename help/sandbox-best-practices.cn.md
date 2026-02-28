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

## 2.1 三档 profile（dev/balanced/prod）与回滚

内部生产建议用 `sandbox.profile` 做“分阶段收紧”：

- `dev`：可用性优先（默认不强制 OS sandbox）
- `balanced`：推荐默认（restricted + auto backend；Linux 默认网络隔离）
- `prod`：更偏生产硬化（提供更严格的基线；细节建议用 overlay 按业务调整）

说明：
- `sandbox.profile` 是高层宏，loader 会在 load 阶段将其展开为 `sandbox.default_policy` + `sandbox.os.*`；
- `sandbox.profile` 只提供“基线默认值”：显式写入的 `sandbox.default_policy` / `sandbox.os.*` 会覆盖 preset（显式 > preset）。
- 一旦进入误拦截排障，**可通过配置回滚**（例如 `prod -> balanced`），无需改代码。

最小回归（离线、可审计输出）：

```bash
bash scripts/integration/sandbox_profile_regression.sh dev
bash scripts/integration/sandbox_profile_regression.sh balanced
bash scripts/integration/sandbox_profile_regression.sh prod
```

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

---

## 8. Docker / 容器环境说明（以 Debian 13、Ubuntu 20.04/24.04 为例）

你在容器里看到的可用性，取决于“容器镜像 + 宿主内核能力 + 容器安全策略（seccomp/apparmor/capabilities）”。

### 8.1 macOS seatbelt（`sandbox-exec`）在 Linux 容器里不能用

- seatbelt 只存在于 macOS（Darwin）宿主机用户态。
- 在 Docker 容器（例如 Debian 13 / Ubuntu 20.04/24.04）里属于 Linux 用户态，无法获得 `sandbox-exec`。
- 结论：**容器里无法使用 seatbelt**；只能在 macOS 宿主机直接运行 SDK/工具进程时使用。

### 8.2 Linux bubblewrap（`bwrap`）在容器里“有条件可用”

必要条件（至少满足其一，否则常见表现为 `sandbox_denied` 或 bwrap 报 `Operation not permitted`）：
- 容器内安装了 `bubblewrap`（提供 `bwrap`）。
- 宿主机允许 user namespace（以及容器 seccomp/apparmor 未拦截 `unshare` 等系统调用）。

快速核验（在容器里执行）：

```bash
command -v bwrap || true
bwrap --version || true
cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || true
cat /proc/sys/user/max_user_namespaces 2>/dev/null || true
```

说明：
- Ubuntu 上常见开关是 `kernel.unprivileged_userns_clone`（0 表示禁用；在容器里通常无法自行打开，需要宿主机配置）。
- `max_user_namespaces` 为 0 也会导致 bwrap 无法创建 namespace。

### 8.3 Docker 探测示例（建议仅用于“可用性探测”，不要把 privileged 当成生产默认）

仓库内已提供一键探测脚本（默认用 Debian 系镜像；需要 privileged）：

```bash
bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh
```

如果你希望用 Ubuntu 镜像做同类探测（示例：Ubuntu 24.04），可以参考下面命令自行替换镜像与包管理命令：

```bash
docker run --rm \
  --privileged \
  --security-opt seccomp=unconfined \
  --entrypoint bash \
  ubuntu:24.04 -lc '
    set -eu
    apt-get update -qq
    apt-get install -y -qq bubblewrap >/dev/null
    bwrap --version
    mkdir -p /tmp/work
    echo hi >/tmp/work/hi.txt
    bwrap --die-with-parent --unshare-net \
      --proc /proc --dev /dev \
      --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib --ro-bind /etc /etc \
      --bind /tmp/work /work --chdir /work -- /bin/cat /work/hi.txt
  '
```

同理你也可以把镜像替换为 Debian 13（常见代号 trixie）或 Ubuntu 20.04（focal）进行探测；关键点在于：
- 容器是否允许创建 user namespace；
- seccomp/apparmor 是否阻断；
- 是否能安装并执行 `bwrap`。

### 8.4 macOS 宿主机 + Docker Desktop 的特殊性（容易误判）

当你的宿主机是 macOS，但你在 Docker 里跑 Debian/Ubuntu 容器时：

- 容器运行在 Docker Desktop 的 **Linux VM 内核**（不是 Darwin），所以容器内依然是 **Linux 用户态**。
- 结论 1：容器里 **不能用 seatbelt（`sandbox-exec`）**；seatbelt 只能在 macOS 宿主机直接运行 SDK/工具进程时使用。
- 结论 2：容器里如果要做 OS sandbox，只能走 **bubblewrap（`bwrap`）**，且仍然取决于 Linux VM 内核是否允许 user namespace，以及 Docker 的 seccomp/AppArmor/capabilities 配置。
- 结论 3：探测脚本 `scripts/integration/os_sandbox_bubblewrap_probe_docker.sh` 仍然有效；即便宿主机是 macOS，也建议先用它做“可用性探测”，避免凭体感判断。
