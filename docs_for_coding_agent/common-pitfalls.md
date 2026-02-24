# Common Pitfalls（常见坑 / 排障速查）

## 1) ripgrep 搜不到 docs/ 下的内容？

原因：本仓库开源忽略策略中，`docs/` 可能被 `.gitignore` 忽略；`rg` 默认会遵循 ignore 规则。

解决：

```bash
rg --no-ignore -n \"TODO\" docs/specs
```

## 2) 在 Docker 容器里 sandbox 能不能用？

结论与入口（以 Debian 13 / Ubuntu 20.04/24.04 为例）：
- 文档：`help/sandbox-best-practices.cn.md`（§8）
- seatbelt（macOS `sandbox-exec`）：
  - **Linux 容器里不可用**
  - 只能在 macOS 宿主机进程里用
- bubblewrap（Linux `bwrap`）：
  - **容器里“有条件可用”**
  - 取决于宿主内核 user namespace、容器 seccomp/apparmor/caps
- 一键探测脚本：`bash scripts/integration/os_sandbox_bubblewrap_probe_docker.sh`

## 3) approvals provider 没有注入，为什么工具被拒绝？

默认安全策略是保守的：
- `safety.mode=ask` 但没有 `ApprovalProvider` 时，框架会倾向于 denied（避免 silent allow）。

排查入口：
- `help/06-tools-and-safety.cn.md`
- `help/09-troubleshooting.cn.md`

## 4) skills.roots / skills.mode=auto 为什么会报错？

本 SDK 的 Skills V2 是 explicit-only（框架级严格）：
- legacy `skills.roots` / `skills.mode=auto` 明确不支持（避免“隐式发现”与不可回归）。

入口：
- `help/05-skills-guide.cn.md`
- `help/02-config-reference.cn.md`（skills 配置段落）

## 5) macOS 上跑 examples 报 typing/pydantic 错误（`str | None` 不支持）？

原因：SDK 代码要求 **Python >= 3.10**；但 macOS 系统自带的 `/usr/bin/python3` 可能仍是 3.9。

解决：
- 使用项目虚拟环境的解释器（例如 conda/venv 对应的 `python`）
- 或显式用 `python3.11 ...` 运行示例
