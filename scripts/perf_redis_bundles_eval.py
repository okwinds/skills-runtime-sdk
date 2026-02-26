#!/usr/bin/env python3
"""
Perf harness: Redis bundle-backed skills (production-shape).

OpenSpec change: skills-redis-bundles-actions-refread-perf

Goals:
- Populate Redis with N skills (default 1k) including minimal zip bundles (actions/ + references/).
- Measure scan/resolve/inject/tool latencies (P50/P95), throughput, and Redis bytes/calls evidence.
- Run a small matrix:
  - approvals: programmatic (RuleBased) vs interactive (delay)
  - sandbox: none vs restricted (if adapter available)

This script is intentionally offline-friendly:
- Uses FakeChatBackend to drive deterministic tool_calls (no real LLM required).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import zipfile


def _prefer_repo_skills_runtime() -> None:
    """
    Prefer the in-repo Python SDK implementation when this script is run inside the repo.

    Why:
    - The perf harness needs the *current workspace code* (not an older installed wheel) so config/schema
      like `skills.bundles.*` stays in sync.
    - This keeps the harness reproducible without requiring callers to remember `PYTHONPATH=...`.
    """

    repo_root = Path(__file__).resolve().parents[1]
    local_src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    if local_src.exists() and local_src.is_dir():
        sys.path.insert(0, str(local_src))


_prefer_repo_skills_runtime()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return float(xs[f])
    d0 = xs[f] * (c - k)
    d1 = xs[c] * (k - f)
    return float(d0 + d1)


def _mk_zip_bundle_bytes() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("actions/noop.sh", b"#!/usr/bin/env bash\nexit 0\n")
        zf.writestr("references/a.txt", b"hello\n")
    return buf.getvalue()


@dataclass
class RedisEvidence:
    calls: Dict[str, int]
    bytes_read: int


class CountingRedisClient:
    """
    A thin wrapper around a redis-py client that counts high-level calls and bytes read.

    Note:
    - This is an approximation of round-trips (scan_iter may issue multiple SCAN commands internally).
    - It still provides useful evidence for relative comparisons and budgets.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: Dict[str, int] = {"scan_iter": 0, "hgetall": 0, "get": 0}
        self.bytes_read: int = 0

    def snapshot(self) -> RedisEvidence:
        return RedisEvidence(calls=dict(self.calls), bytes_read=int(self.bytes_read))

    def delta(self, before: RedisEvidence) -> RedisEvidence:
        calls = {k: int(self.calls.get(k, 0)) - int(before.calls.get(k, 0)) for k in set(self.calls) | set(before.calls)}
        return RedisEvidence(calls=calls, bytes_read=int(self.bytes_read) - int(before.bytes_read))

    @staticmethod
    def _len_bytes(x: Any) -> int:
        if x is None:
            return 0
        if isinstance(x, (bytes, bytearray)):
            return len(x)
        if isinstance(x, str):
            return len(x.encode("utf-8", errors="replace"))
        if isinstance(x, int):
            return len(str(x).encode("utf-8"))
        try:
            return len(str(x).encode("utf-8", errors="replace"))
        except Exception:
            return 0

    def scan_iter(self, *, match: str):
        self.calls["scan_iter"] += 1
        return self._inner.scan_iter(match=match)

    def hgetall(self, key: Any) -> Any:
        self.calls["hgetall"] += 1
        out = self._inner.hgetall(key)
        if isinstance(out, dict):
            for k, v in out.items():
                self.bytes_read += self._len_bytes(k) + self._len_bytes(v)
        return out

    def get(self, key: Any) -> Any:
        self.calls["get"] += 1
        out = self._inner.get(key)
        self.bytes_read += self._len_bytes(out)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def _sleep_ms(ms: int) -> None:
    await asyncio.sleep(max(0.0, float(ms) / 1000.0))


class DelayedApprovalProvider:
    """Interactive-style ApprovalProvider: always approve after an artificial delay."""

    def __init__(self, *, delay_ms: int) -> None:
        self.delay_ms = int(delay_ms)

    async def request_approval(self, *, request, timeout_ms=None):  # type: ignore[no-untyped-def]
        _ = request
        _ = timeout_ms
        await _sleep_ms(self.delay_ms)
        from skills_runtime.safety.approvals import ApprovalDecision

        return ApprovalDecision.APPROVED


def _populate_redis(
    *,
    redis_client: Any,
    key_prefix: str,
    namespace: str,
    num_skills: int,
    bundle_bytes: bytes,
    bundle_sha256: str,
    bundle_key_override: Optional[str] = None,
) -> None:
    pipe = redis_client.pipeline(transaction=False)
    for i in range(int(num_skills)):
        skill_name = f"skill_{i:04d}"
        meta_key = f"{key_prefix}meta:{namespace}:{skill_name}"
        body_key = f"{key_prefix}body:{namespace}:{skill_name}"
        bundle_key = bundle_key_override or f"{key_prefix}bundle:{namespace}:{skill_name}"

        metadata_obj = {
            "actions": {
                "noop": {
                    "kind": "shell",
                    "argv": ["bash", "actions/noop.sh"],
                    "timeout_ms": 10_000,
                    "env": {"X": "1"},
                }
            }
        }
        pipe.hset(
            meta_key,
            mapping={
                "skill_name": skill_name,
                "description": "d",
                "created_at": "2026-02-07T00:00:00Z",
                "metadata": json.dumps(metadata_obj, ensure_ascii=False),
                "body_key": body_key,
                "body_size": 4,
                "bundle_sha256": bundle_sha256,
                "bundle_format": "zip",
                # bundle_key optional by contract; keep default path for the harness
            },
        )
        pipe.set(body_key, b"body")
        pipe.set(bundle_key, bundle_bytes)
    pipe.execute()


def _mk_skills_manager(
    *,
    workspace_root: Path,
    namespace: str,
    source_id: str,
    key_prefix: str,
    dsn_env: str,
    injected_redis_client: Any,
    refresh_policy: str,
    ttl_sec: int,
    bundle_max_bytes: int,
    bundle_cache_dir: str,
) -> Any:
    from skills_runtime.skills.manager import SkillsManager

    return SkillsManager(
        workspace_root=workspace_root,
        skills_config={
            "spaces": [{"id": "space-perf", "namespace": namespace, "sources": [source_id]}],
            "sources": [{"id": source_id, "type": "redis", "options": {"dsn_env": dsn_env, "key_prefix": key_prefix}}],
            "scan": {"refresh_policy": refresh_policy, "ttl_sec": int(ttl_sec)},
            "injection": {"max_bytes": 64 * 1024},
            "actions": {"enabled": True},
            "references": {"enabled": True, "allow_assets": False, "default_max_bytes": 64 * 1024},
            "bundles": {"max_bytes": int(bundle_max_bytes), "cache_dir": str(bundle_cache_dir)},
        },
        source_clients={source_id: injected_redis_client},
    )


def _run_scan_resolve_inject_bench(
    *,
    mgr: Any,
    namespace: str,
    num_ops: int,
    rng: random.Random,
    redis_counter: CountingRedisClient,
) -> Dict[str, Any]:
    scan_durs: List[float] = []
    resolve_durs: List[float] = []
    inject_durs: List[float] = []
    inject_bytes: List[int] = []

    # 1) scan (force refresh once)
    before = redis_counter.snapshot()
    t0 = time.perf_counter()
    mgr.scan(force_refresh=True)
    scan_durs.append(time.perf_counter() - t0)
    scan_ev = redis_counter.delta(before)

    skills = mgr.list_skills(enabled_only=True)
    if not skills:
        raise RuntimeError("no skills found after scan (check redis population and config)")

    # 2) resolve + inject loop
    for _ in range(int(num_ops)):
        skill = rng.choice(skills)
        mention = f"$[{namespace}].{skill.skill_name}"

        before_r = redis_counter.snapshot()
        t1 = time.perf_counter()
        resolved = mgr.resolve_mentions(mention)
        resolve_durs.append(time.perf_counter() - t1)
        _ = redis_counter.delta(before_r)  # keep counters global; per-op bytes are noisy

        s0, m0 = resolved[0]
        t2 = time.perf_counter()
        injected = mgr.render_injected_skill(s0, source="perf", mention_text=m0.mention_text)
        inject_durs.append(time.perf_counter() - t2)
        inject_bytes.append(len(injected.encode("utf-8", errors="replace")))

    return {
        "scan": {
            "durations_sec": scan_durs,
            "redis": {"calls": scan_ev.calls, "bytes_read": scan_ev.bytes_read},
        },
        "resolve_mentions": {"durations_sec": resolve_durs},
        "inject": {"durations_sec": inject_durs, "bytes": {"p50": _percentile([float(x) for x in inject_bytes], 50), "p95": _percentile([float(x) for x in inject_bytes], 95)}},
    }


def _run_agent_tool_bench(
    *,
    workspace_root: Path,
    mgr: Any,
    namespace: str,
    tool: str,
    action_id: str,
    ref_path: str,
    approvals_mode: str,
    approvals_delay_ms: int,
    sandbox_default_policy: str,
    num_ops: int,
    rng: random.Random,
    redis_counter: CountingRedisClient,
) -> Dict[str, Any]:
    from skills_runtime.core.agent import Agent
    from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
    from skills_runtime.llm.chat_sse import ChatStreamEvent
    from skills_runtime.tools.protocol import ToolCall
    from skills_runtime.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
    from skills_runtime.safety.approvals import ApprovalDecision

    skills = mgr.list_skills(enabled_only=True)
    if not skills:
        raise RuntimeError("no skills found (scan first)")

    tool_durs: List[float] = []
    approvals_overhead_durs: List[float] = []
    approvals_wait_durs: List[float] = []
    sandbox_denied = 0
    approval_denied = 0

    if approvals_mode == "programmatic":
        ap = RuleBasedApprovalProvider(
            rules=[
                ApprovalRule(tool="skill_exec", decision=ApprovalDecision.APPROVED),
                ApprovalRule(tool="shell_exec", decision=ApprovalDecision.APPROVED),
            ]
        )
        ap_cfg: Dict[str, Any] = {"mode": "programmatic"}
    elif approvals_mode == "interactive":
        ap = DelayedApprovalProvider(delay_ms=approvals_delay_ms)
        ap_cfg = {"mode": "interactive", "delay_ms": int(approvals_delay_ms)}
    else:
        raise ValueError(f"unknown approvals_mode: {approvals_mode}")

    overlay = workspace_root / ".skills_runtime_sdk" / "perf" / f"runtime.overlay.{tool}.{approvals_mode}.{sandbox_default_policy}.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML (YAML is a superset of JSON).
    overlay.write_text(
        json.dumps(
            {
                "safety": {"mode": "ask", "approval_timeout_ms": 60_000},
                "sandbox": {"default_policy": sandbox_default_policy},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Build a single run with multiple tool calls to reduce init noise.
    calls: List[ToolCall] = []
    for i in range(int(num_ops)):
        skill = rng.choice(skills)
        mention = f"$[{namespace}].{skill.skill_name}"
        call_id = f"c{i+1}"
        if tool == "skill_exec":
            calls.append(ToolCall(call_id=call_id, name="skill_exec", args={"skill_mention": mention, "action_id": action_id}))
        elif tool == "skill_ref_read":
            calls.append(ToolCall(call_id=call_id, name="skill_ref_read", args={"skill_mention": mention, "ref_path": ref_path, "max_bytes": 1024}))
        else:
            raise ValueError(f"unsupported tool: {tool}")

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=calls, finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    # Collect per-call latency evidence via event hooks.
    t_requested: Dict[str, float] = {}
    t_finished: Dict[str, float] = {}
    t_approval_requested: Dict[str, float] = {}
    t_approval_decided: Dict[str, float] = {}

    def _hook(ev):  # type: ignore[no-untyped-def]
        nonlocal sandbox_denied, approval_denied
        now = time.perf_counter()
        payload = getattr(ev, "payload", None) or {}
        et = str(getattr(ev, "type", "") or "")
        if et == "tool_call_requested":
            cid = str(payload.get("call_id") or "")
            if cid:
                t_requested[cid] = now
        if et == "tool_call_finished":
            cid = str(payload.get("call_id") or "")
            if cid:
                t_finished[cid] = now
            result = payload.get("result") or {}
            if isinstance(result, dict):
                if str(result.get("error_kind") or "") == "sandbox_denied":
                    sandbox_denied += 1
                if str(result.get("error_kind") or "") == "approval_denied":
                    approval_denied += 1
        if et == "approval_requested":
            cid = str(payload.get("call_id") or "")
            if cid:
                t_approval_requested[cid] = now
        if et == "approval_decided":
            cid = str(payload.get("call_id") or "")
            if cid:
                t_approval_decided[cid] = now

    before = redis_counter.snapshot()
    r = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        skills_manager=mgr,
        approval_provider=ap,
        config_paths=[overlay],
        event_hooks=[_hook],
    ).run("perf", run_id=f"perf_{tool}_{_utc_now_compact()}_{approvals_mode}_{sandbox_default_policy}")
    _ = r
    redis_ev = redis_counter.delta(before)

    for call in calls:
        cid = str(call.call_id)
        if cid in t_requested and cid in t_finished:
            tool_durs.append(t_finished[cid] - t_requested[cid])
        if cid in t_requested and cid in t_approval_requested:
            approvals_overhead_durs.append(t_approval_requested[cid] - t_requested[cid])
        if cid in t_approval_requested and cid in t_approval_decided:
            approvals_wait_durs.append(t_approval_decided[cid] - t_approval_requested[cid])

    return {
        "tool": tool,
        "approvals": ap_cfg,
        "sandbox_default_policy": sandbox_default_policy,
        "durations_sec": tool_durs,
        "approvals_timing_sec": {
            "overhead_durations_sec": approvals_overhead_durs,
            "wait_durations_sec": approvals_wait_durs,
        },
        "redis": {
            "calls": dict(redis_ev.calls),
            "bytes_read": int(redis_ev.bytes_read),
        },
        "failures": {"sandbox_denied": sandbox_denied, "approval_denied": approval_denied},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redis-dsn-env", default="SKILLS_REDIS_DSN")
    ap.add_argument("--key-prefix", default="skills:")
    ap.add_argument("--namespace", default="perf")
    ap.add_argument("--num-skills", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--refresh-policy", choices=["always", "ttl", "manual"], default="ttl")
    ap.add_argument("--ttl-sec", type=int, default=600)
    ap.add_argument("--bundle-max-bytes", type=int, default=1 * 1024 * 1024)
    ap.add_argument("--bundle-cache-dir", default=".skills_runtime_sdk/bundles")

    ap.add_argument("--ops", type=int, default=200, help="number of resolve/inject ops per run (default 200)")
    ap.add_argument("--tool-ops", type=int, default=50, help="number of tool ops per scenario (default 50)")
    ap.add_argument("--approvals-delay-ms", type=int, default=250, help="interactive approvals artificial delay")
    ap.add_argument("--out-dir", default=".skills_runtime_sdk/perf/redis_bundles")
    args = ap.parse_args()

    dsn_env = str(args.redis_dsn_env)
    dsn = __import__("os").environ.get(dsn_env)
    if not dsn:
        raise SystemExit(f"missing env var: {dsn_env}")

    try:
        import redis  # type: ignore
    except Exception as exc:
        raise SystemExit(f"missing dependency: redis ({exc})")

    real = redis.from_url(dsn)
    counter = CountingRedisClient(real)

    bundle_bytes = _mk_zip_bundle_bytes()
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()

    # Populate Redis with 1k skills (idempotent overwrite)
    _populate_redis(
        redis_client=real,
        key_prefix=str(args.key_prefix),
        namespace=str(args.namespace),
        num_skills=int(args.num_skills),
        bundle_bytes=bundle_bytes,
        bundle_sha256=bundle_sha,
    )

    workspace_root = Path(".").resolve()
    mgr = _mk_skills_manager(
        workspace_root=workspace_root,
        namespace=str(args.namespace),
        source_id="src-redis",
        key_prefix=str(args.key_prefix),
        dsn_env=dsn_env,
        injected_redis_client=counter,
        refresh_policy=str(args.refresh_policy),
        ttl_sec=int(args.ttl_sec),
        bundle_max_bytes=int(args.bundle_max_bytes),
        bundle_cache_dir=str(args.bundle_cache_dir),
    )

    rng = random.Random(int(args.seed))

    scan_res_inj = _run_scan_resolve_inject_bench(
        mgr=mgr,
        namespace=str(args.namespace),
        num_ops=int(args.ops),
        rng=rng,
        redis_counter=counter,
    )

    tool_scenarios: list[dict[str, Any]] = []
    for approvals_mode in ["programmatic", "interactive"]:
        for sandbox_policy in ["none", "restricted"]:
            tool_scenarios.append(
                _run_agent_tool_bench(
                    workspace_root=workspace_root,
                    mgr=mgr,
                    namespace=str(args.namespace),
                    tool="skill_exec",
                    action_id="noop",
                    ref_path="references/a.txt",
                    approvals_mode=approvals_mode,
                    approvals_delay_ms=int(args.approvals_delay_ms),
                    sandbox_default_policy=sandbox_policy,
                    num_ops=int(args.tool_ops),
                    rng=rng,
                    redis_counter=counter,
                )
            )
            tool_scenarios.append(
                _run_agent_tool_bench(
                    workspace_root=workspace_root,
                    mgr=mgr,
                    namespace=str(args.namespace),
                    tool="skill_ref_read",
                    action_id="noop",
                    ref_path="references/a.txt",
                    approvals_mode=approvals_mode,
                    approvals_delay_ms=int(args.approvals_delay_ms),
                    sandbox_default_policy=sandbox_policy,
                    num_ops=int(args.tool_ops),
                    rng=rng,
                    redis_counter=counter,
                )
            )

    def _summarize(name: str, durs: List[float]) -> Dict[str, Any]:
        return {
            "name": name,
            "count": len(durs),
            "p50_ms": _percentile([x * 1000.0 for x in durs], 50),
            "p95_ms": _percentile([x * 1000.0 for x in durs], 95),
            "mean_ms": (statistics.mean(durs) * 1000.0) if durs else 0.0,
        }

    report: Dict[str, Any] = {
        "generated_at_utc": _utc_now_compact(),
        "inputs": {
            "redis_dsn_env": dsn_env,
            "key_prefix": str(args.key_prefix),
            "namespace": str(args.namespace),
            "num_skills": int(args.num_skills),
            "seed": int(args.seed),
            "refresh_policy": str(args.refresh_policy),
            "ttl_sec": int(args.ttl_sec),
            "bundle_max_bytes": int(args.bundle_max_bytes),
            "bundle_cache_dir": str(args.bundle_cache_dir),
            "ops": int(args.ops),
            "tool_ops": int(args.tool_ops),
            "approvals_delay_ms": int(args.approvals_delay_ms),
            "bundle_sha256": bundle_sha,
        },
        "scan_resolve_inject": {
            "scan": _summarize("scan(force_refresh=True)", scan_res_inj["scan"]["durations_sec"]),
            "resolve_mentions": _summarize("resolve_mentions", scan_res_inj["resolve_mentions"]["durations_sec"]),
            "inject": _summarize("render_injected_skill", scan_res_inj["inject"]["durations_sec"]),
            "redis_scan_evidence": scan_res_inj["scan"]["redis"],
        },
        "tools": [],
    }

    for sc in tool_scenarios:
        durs = list(sc.get("durations_sec") or [])
        at = sc.get("approvals_timing_sec") or {}
        overhead = list((at.get("overhead_durations_sec") or []))
        wait = list((at.get("wait_durations_sec") or []))
        report["tools"].append(
            {
                "tool": sc.get("tool"),
                "approvals": sc.get("approvals"),
                "sandbox_default_policy": sc.get("sandbox_default_policy"),
                "latency": _summarize(f"{sc.get('tool')}", durs),
                "approvals_timing": {
                    "overhead_p50_ms": _percentile([x * 1000.0 for x in overhead], 50) if overhead else 0.0,
                    "overhead_p95_ms": _percentile([x * 1000.0 for x in overhead], 95) if overhead else 0.0,
                    "wait_p50_ms": _percentile([x * 1000.0 for x in wait], 50) if wait else 0.0,
                    "wait_p95_ms": _percentile([x * 1000.0 for x in wait], 95) if wait else 0.0,
                    "interactive_delay_config_ms": int(args.approvals_delay_ms) if (sc.get("approvals") or {}).get("mode") == "interactive" else 0,
                },
                "throughput_ops_per_sec": (len(durs) / sum(durs)) if durs and sum(durs) > 0 else 0.0,
                "redis_evidence": sc.get("redis"),
                "failures": sc.get("failures"),
            }
        )

    out_dir = Path(str(args.out_dir)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"perf_report.{_utc_now_compact()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_lines: List[str] = []
    summary_lines.append("# Redis bundles perf summary\n")
    summary_lines.append(f"- generated_at_utc: `{report['generated_at_utc']}`")
    summary_lines.append(f"- skills: `{args.num_skills}` namespace=`{args.namespace}` key_prefix=`{args.key_prefix}`")
    summary_lines.append(f"- refresh_policy=`{args.refresh_policy}` ttl_sec=`{args.ttl_sec}`")
    summary_lines.append(f"- bundle_max_bytes=`{args.bundle_max_bytes}` bundle_sha256=`{bundle_sha}`")
    summary_lines.append("")
    sri = report["scan_resolve_inject"]
    summary_lines.append("## Core\n")
    summary_lines.append(f"- scan p50/p95 (ms): {sri['scan']['p50_ms']:.2f} / {sri['scan']['p95_ms']:.2f}")
    summary_lines.append(f"- resolve_mentions p50/p95 (ms): {sri['resolve_mentions']['p50_ms']:.2f} / {sri['resolve_mentions']['p95_ms']:.2f}")
    summary_lines.append(f"- inject p50/p95 (ms): {sri['inject']['p50_ms']:.2f} / {sri['inject']['p95_ms']:.2f}")
    summary_lines.append("")
    summary_lines.append("## Tools\n")
    for t in report["tools"]:
        summary_lines.append(
            f"- {t['tool']} approvals={t['approvals']} sandbox={t['sandbox_default_policy']} "
            f"p50/p95(ms)={t['latency']['p50_ms']:.2f}/{t['latency']['p95_ms']:.2f} "
            f"throughput_ops_per_sec={t['throughput_ops_per_sec']:.2f} "
            f"redis_bytes_read={int(t['redis_evidence'].get('bytes_read', 0))}"
        )
    summary_lines.append("")
    summary_lines.append(f"- report_path: `{report_path}`")

    summary_path = out_dir / f"perf_summary.{_utc_now_compact()}.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(str(report_path))
    print(str(summary_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
