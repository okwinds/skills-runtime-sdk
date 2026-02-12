#!/usr/bin/env bash
set -euo pipefail

# OS sandbox 限制效果演示（用于“肉眼可见”地确认：确实进入了 OS sandbox，而不是仅 approvals 拦截）。
#
# 设计：
# - macOS：seatbelt 通过 profile deny /etc 的 file-read*，让 `cat /etc/hosts` 失败（稳定、无需外网）
# - Linux：bubblewrap 使用 --unshare-net，让沙箱内无法访问 host 的 127.0.0.1 服务（稳定、无需外网）
#
# 运行：
# - macOS：bash scripts/integration/os_sandbox_restriction_demo.sh
# - Linux：bash scripts/integration/os_sandbox_restriction_demo.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[sandbox-demo] repo_root=${ROOT_DIR}"

pick_python() {
  local candidates=("python3.12" "python3.11" "python3.10" "python3" "python")
  local c
  for c in "${candidates[@]}"; do
    if ! command -v "${c}" >/dev/null 2>&1; then
      continue
    fi
    # require >= 3.10 (SDK uses PEP 604 `X | Y` typing)
    if "${c}" - <<'PY' >/dev/null 2>&1; then
import sys
assert sys.version_info >= (3, 10)
PY
      echo "${c}"
      return 0
    fi
  done
  return 1
}

PY_BIN="$(pick_python || true)"
if [[ -z "${PY_BIN}" ]]; then
  echo "[sandbox-demo] Python >= 3.10 not found. This SDK requires Python 3.10+."
  exit 1
fi

echo "[sandbox-demo] python=${PY_BIN}"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if ! command -v sandbox-exec >/dev/null 2>&1; then
    echo "[sandbox-demo] macOS: sandbox-exec not found; cannot demo seatbelt restriction"
    exit 1
  fi

  echo "[sandbox-demo] macOS seatbelt: deny file-read* under /etc (cat /etc/hosts should fail in sandbox)"
  PYTHONPATH="${ROOT_DIR}/packages/skills-runtime-sdk-python/src" \
  "${PY_BIN}" - <<'PY'
from pathlib import Path

from agent_sdk.core.executor import Executor
from agent_sdk.sandbox import SeatbeltSandboxAdapter
from agent_sdk.tools.builtin.shell_exec import shell_exec
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext

ws = Path(".").resolve()

def run(label: str, *, sandbox_policy_default: str, adapter, argv: list[str]) -> None:
    ctx = ToolExecutionContext(
        workspace_root=ws,
        run_id="sandbox_demo",
        wal=None,
        executor=Executor(),
        emit_tool_events=False,
        sandbox_policy_default=sandbox_policy_default,
        sandbox_adapter=adapter,
    )
    r = shell_exec(ToolCall(call_id=label, name="shell_exec", args={"argv": argv}), ctx)
    d = r.details or {}
    meta = (d.get("data") or {}).get("sandbox") or {}
    print(f"\n[{label}] ok={d.get('ok')} exit_code={d.get('exit_code')} error_kind={d.get('error_kind')}")
    print(f"[{label}] sandbox.active={meta.get('active')} adapter={meta.get('adapter')} effective={meta.get('effective')}")
    if d.get("stderr"):
        print(f"[{label}] stderr: {str(d.get('stderr')).strip()}")

print("[host] expecting success")
run("host", sandbox_policy_default="none", adapter=None, argv=["/bin/cat", "/etc/hosts"])

profile = '(version 1) (deny file-read* (subpath "/etc")) (allow default)'
adapter = SeatbeltSandboxAdapter(profile=profile)
print("\n[sandbox] expecting failure (Operation not permitted) and sandbox.active=true")
run("sandbox", sandbox_policy_default="restricted", adapter=adapter, argv=["/bin/cat", "/etc/hosts"])
PY

  exit 0
fi

if [[ "$(uname -s)" == "Linux" ]]; then
  if ! command -v bwrap >/dev/null 2>&1; then
    echo "[sandbox-demo] Linux: bwrap not found; cannot demo bubblewrap restriction"
    exit 1
  fi

  echo "[sandbox-demo] Linux bubblewrap: unshare-net blocks host loopback (sandbox should fail)"
  PYTHONPATH="${ROOT_DIR}/packages/skills-runtime-sdk-python/src" \
  "${PY_BIN}" - <<'PY'
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agent_sdk.core.executor import Executor
from agent_sdk.sandbox import BubblewrapSandboxAdapter
from agent_sdk.tools.builtin.shell_exec import shell_exec
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext

ws = Path(".").resolve()

def pick_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port

class OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, fmt, *args):
        return

def run(label: str, *, sandbox_policy_default: str, adapter, argv: list[str]) -> None:
    ctx = ToolExecutionContext(
        workspace_root=ws,
        run_id="sandbox_demo",
        wal=None,
        executor=Executor(),
        emit_tool_events=False,
        sandbox_policy_default=sandbox_policy_default,
        sandbox_adapter=adapter,
    )
    r = shell_exec(ToolCall(call_id=label, name="shell_exec", args={"argv": argv}), ctx)
    d = r.details or {}
    meta = (d.get("data") or {}).get("sandbox") or {}
    print(f"\n[{label}] ok={d.get('ok')} exit_code={d.get('exit_code')} error_kind={d.get('error_kind')}")
    print(f"[{label}] sandbox.active={meta.get('active')} adapter={meta.get('adapter')} effective={meta.get('effective')}")
    if d.get("stderr"):
        print(f"[{label}] stderr: {str(d.get('stderr')).strip()}")

port = pick_port()
srv = HTTPServer(("127.0.0.1", port), OkHandler)
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()

try:
    print("[host] expecting success")
    run(
        "host",
        sandbox_policy_default="none",
        adapter=None,
        argv=["python3", "-c", f"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}', timeout=1).read().decode())"],
    )

    print("\n[sandbox] expecting failure and sandbox.active=true")
    adapter = BubblewrapSandboxAdapter(bwrap_path="bwrap", unshare_net=True)
    run(
        "sandbox",
        sandbox_policy_default="restricted",
        adapter=adapter,
        argv=["python3", "-c", f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}', timeout=1).read()"],
    )
finally:
    srv.shutdown()
    srv.server_close()
PY

  exit 0
fi

echo "[sandbox-demo] unsupported platform: $(uname -s)"
exit 1
