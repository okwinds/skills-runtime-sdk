#!/usr/bin/env bash
set -euo pipefail

# Sandbox profile（dev/balanced/prod）回归脚本（离线、可复现）。
#
# 目标：
# - 证明 `sandbox.profile` 能被配置 loader 正确展开；
# - 在 adapter 可用时，给出“肉眼可见”的限制效果证据（无需外网）：
#   - macOS：seatbelt prod profile deny /etc（cat /etc/hosts 失败）
#   - Linux：bubblewrap unshare-net 阻断对 host loopback 的访问（sandbox 内失败）
#
# 用法：
#   bash scripts/integration/sandbox_profile_regression.sh dev
#   bash scripts/integration/sandbox_profile_regression.sh balanced
#   bash scripts/integration/sandbox_profile_regression.sh prod

PROFILE="${1:-}"
if [[ -z "${PROFILE}" ]]; then
  echo "usage: $0 <dev|balanced|prod>"
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

pick_python() {
  local candidates=("python3.12" "python3.11" "python3.10" "python3" "python")
  local c
  for c in "${candidates[@]}"; do
    if ! command -v "${c}" >/dev/null 2>&1; then
      continue
    fi
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
  echo "[sandbox-profile] Python >= 3.10 not found."
  exit 1
fi

PYTHONPATH="${ROOT_DIR}/packages/skills-runtime-sdk-python/src" \
"${PY_BIN}" - <<PY
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from skills_runtime.config.loader import load_config_dicts
from skills_runtime.core.executor import Executor
from skills_runtime.sandbox import create_default_os_sandbox_adapter
from skills_runtime.tools.builtin.shell_exec import shell_exec
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext

profile = str(${PROFILE!r}).strip().lower()
ws = Path(".").resolve()

cfg = load_config_dicts([
    {
        "config_version": 1,
        "run": {},
        "llm": {"base_url": "http://example.invalid/v1", "api_key_env": "X"},
        "models": {"planner": "p", "executor": "e"},
        "sandbox": {"profile": profile},
    }
])

adapter = create_default_os_sandbox_adapter(
    mode=str(cfg.sandbox.os.mode),
    seatbelt_profile=str(cfg.sandbox.os.seatbelt.profile),
    bubblewrap_bwrap_path=str(cfg.sandbox.os.bubblewrap.bwrap_path),
    bubblewrap_unshare_net=bool(cfg.sandbox.os.bubblewrap.unshare_net),
)

evidence = {
    "profile": profile,
    "platform": sys.platform,
    "default_policy": str(cfg.sandbox.default_policy),
    "os_mode": str(cfg.sandbox.os.mode),
    "bubblewrap_unshare_net": bool(cfg.sandbox.os.bubblewrap.unshare_net),
    "adapter": type(adapter).__name__ if adapter is not None else None,
}

def run_shell(argv: list[str]) -> dict:
    ctx = ToolExecutionContext(
        workspace_root=ws,
        run_id="sandbox_profile_regression",
        wal=None,
        executor=Executor(),
        emit_tool_events=False,
        sandbox_policy_default=str(cfg.sandbox.default_policy),
        sandbox_adapter=adapter,
    )
    r = shell_exec(ToolCall(call_id="c1", name="shell_exec", args={"argv": argv, "sandbox": "inherit"}), ctx)
    d = r.details or {}
    meta = (d.get("data") or {}).get("sandbox") or {}
    return {
        "ok": bool(d.get("ok")),
        "exit_code": d.get("exit_code"),
        "error_kind": d.get("error_kind"),
        "stderr": (d.get("stderr") or "").strip(),
        "sandbox": {"effective": meta.get("effective"), "active": meta.get("active"), "adapter": meta.get("adapter")},
    }

def print_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))

print_json({"kind": "sandbox_profile_config", **evidence})

if sys.platform.startswith("darwin"):
    if adapter is None:
        print_json({"kind": "sandbox_profile_check", "check": "seatbelt_cat_etc_hosts", "skipped": True, "reason": "sandbox adapter unavailable"})
        raise SystemExit(0)
    out = run_shell(["/bin/cat", "/etc/hosts"])
    # prod：期望失败（deny /etc）；dev/balanced：通常成功（或至少不会是 sandbox_denied）
    print_json({"kind": "sandbox_profile_check", "check": "seatbelt_cat_etc_hosts", **out})
    raise SystemExit(0)

if sys.platform.startswith("linux"):
    if adapter is None:
        print_json({"kind": "sandbox_profile_check", "check": "bubblewrap_loopback", "skipped": True, "reason": "sandbox adapter unavailable"})
        raise SystemExit(0)

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
        def log_message(self, fmt, *args):  # silence
            return

    port = pick_port()
    srv = HTTPServer(("127.0.0.1", port), OkHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        out = run_shell([
            "python3",
            "-c",
            f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}', timeout=1).read()",
        ])
        print_json({"kind": "sandbox_profile_check", "check": "bubblewrap_loopback", "port": port, **out})
    finally:
        srv.shutdown()
        srv.server_close()
    raise SystemExit(0)

print_json({"kind": "sandbox_profile_check", "check": "unsupported_platform", "skipped": True, "platform": sys.platform})
PY

