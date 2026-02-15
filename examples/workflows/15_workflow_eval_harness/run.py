"""
Workflow eval harness：同一 workflow 多次运行 → 对比 artifacts → 输出 score（离线可用）。

设计目标：
- 以“产物一致性（artifact consistency）”为核心评分口径
- 对 artifacts 做 normalize（去除 workspace 绝对路径与 run_id 等运行时噪音）
- 输出 Markdown + JSON 两份结果，便于人类阅读与 CI 接入
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


WORKFLOWS: Dict[str, str] = {
    "04": "examples/workflows/04_map_reduce_parallel_subagents/run.py",
    "05": "examples/workflows/05_multi_agent_code_review_fix_qa_report/run.py",
}


@dataclass(frozen=True)
class ArtifactSnapshot:
    relpath: str
    normalized_text: str
    sha256: str


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _normalize_text(*, text: str, workspace_root: Path) -> str:
    """
    规范化文本，避免把“运行时噪音”当成差异：
    - 统一换行
    - 去掉 workspace 绝对路径
    - 把 `.skills_runtime_sdk/runs/<run_id>/events.jsonl` 归一化为 `<RUN_ID>`
    """

    out = text.replace("\r\n", "\n").replace("\r", "\n")
    # workspace root 绝对路径（不同机器/不同 tmp 目录会变）
    out = out.replace(str(workspace_root), "<WORKSPACE>")
    # WAL run_id（不同次运行会变）
    out = re.sub(r"\.skills_runtime_sdk/runs/[^/\s]+/events\.jsonl", ".skills_runtime_sdk/runs/<RUN_ID>/events.jsonl", out)
    return out


def _run_workflow_script(*, repo_root: Path, script_relpath: str, workspace_root: Path, timeout_sec: int = 60) -> Tuple[str, str]:
    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    script = repo_root / script_relpath
    if not script.exists():
        raise AssertionError(f"workflow script missing: {script_relpath}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["PYTHONUNBUFFERED"] = "1"

    p = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--workspace-root", str(workspace_root)],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
    )
    if p.returncode != 0:
        raise AssertionError((script_relpath, p.returncode, p.stdout, p.stderr))
    return (p.stdout, p.stderr)


def _collect_artifact_paths(*, workflow_id: str, workspace_root: Path) -> List[Path]:
    """
    返回需要纳入一致性比较的 artifacts（按稳定顺序）。

    约定：
    - 不比较 WAL events.jsonl（包含时间戳/更多噪音）；只比较“项目级产物”。
    """

    roots: List[Path] = []
    if workflow_id == "04":
        roots.append(workspace_root / "subtasks.json")
        roots.append(workspace_root / "report.md")
        outputs_dir = workspace_root / "outputs"
        if outputs_dir.exists():
            roots.extend(sorted([p for p in outputs_dir.glob("*.md") if p.is_file()], key=lambda p: p.name))
    elif workflow_id == "05":
        roots.append(workspace_root / "calc.py")
        roots.append(workspace_root / "report.md")
    else:
        raise AssertionError(f"unknown workflow_id: {workflow_id}")

    for p in roots:
        if not p.exists():
            raise AssertionError(f"missing artifact: {p}")
    return roots


def _snapshot_artifacts(*, workflow_id: str, workspace_root: Path) -> Dict[str, ArtifactSnapshot]:
    """
    读取并规范化 artifacts，返回 relpath → snapshot。
    """

    out: Dict[str, ArtifactSnapshot] = {}
    for p in _collect_artifact_paths(workflow_id=workflow_id, workspace_root=workspace_root):
        rel = str(p.relative_to(workspace_root))
        raw = p.read_text(encoding="utf-8", errors="replace")
        norm = _normalize_text(text=raw, workspace_root=workspace_root)
        out[rel] = ArtifactSnapshot(relpath=rel, normalized_text=norm, sha256=_sha256_text(norm))
    return out


def _diff_summary(*, a: str, b: str, max_lines: int = 80) -> str:
    """
    生成 unified diff 摘要（限制行数，避免报告过长）。
    """

    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(a_lines, b_lines, fromfile="baseline", tofile="candidate"))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + ["... (diff truncated)\n"]
    return "".join(diff) if diff else ""


def _format_eval_report_md(*, results: Dict[str, Dict]) -> str:
    lines: List[str] = []
    lines.append("# Workflow Eval Report（artifact consistency）")
    lines.append("")
    lines.append("评分口径：同一 workflow 运行多次后，关键 artifacts 在 normalize 后应保持一致。")
    lines.append("")

    total_scores: List[float] = []
    for wf_id, wf in results.items():
        lines.append(f"## WF{wf_id}")
        lines.append(f"- Runs: `{wf['runs']}`")
        lines.append(f"- Score: `{wf['score']:.3f}`")
        lines.append("")
        total_scores.append(float(wf["score"]))

        for art in wf["artifacts"]:
            lines.append(f"### {art['relpath']}")
            lines.append(f"- Consistent: `{art['consistent']}`")
            if not art["consistent"]:
                lines.append("- Hashes:")
                for h, count in sorted(art["hash_counts"].items(), key=lambda kv: kv[0]):
                    lines.append(f"  - {h}: {count}")
                diff = str(art.get("diff") or "")
                if diff:
                    lines.append("")
                    lines.append("```diff")
                    lines.append(diff.rstrip())
                    lines.append("```")
            lines.append("")

    overall = sum(total_scores) / max(1, len(total_scores))
    lines.append("## Overall")
    lines.append(f"- Score: `{overall:.3f}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="15_workflow_eval_harness (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--runs", type=int, default=5, help="Number of runs per workflow (default: 5)")
    parser.add_argument("--workflows", default="04,05", help="Comma-separated workflow ids (default: 04,05)")
    args = parser.parse_args()

    runs = int(args.runs)
    if runs < 2:
        raise SystemExit("--runs must be >= 2")

    wf_ids = [x.strip() for x in str(args.workflows).split(",") if x.strip()]
    for wf_id in wf_ids:
        if wf_id not in WORKFLOWS:
            raise SystemExit(f"unknown workflow id: {wf_id} (supported: {sorted(WORKFLOWS.keys())})")

    repo_root = Path(__file__).resolve().parents[3]
    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    runs_root = workspace_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    eval_results: Dict[str, Dict] = {}

    for wf_id in wf_ids:
        wf_script = WORKFLOWS[wf_id]
        wf_run_snapshots: List[Dict[str, ArtifactSnapshot]] = []

        for i in range(1, runs + 1):
            run_ws = runs_root / f"wf{wf_id}" / f"run_{i:02d}"
            run_ws.mkdir(parents=True, exist_ok=True)
            _run_workflow_script(repo_root=repo_root, script_relpath=wf_script, workspace_root=run_ws, timeout_sec=60)
            wf_run_snapshots.append(_snapshot_artifacts(workflow_id=wf_id, workspace_root=run_ws))

        # artifacts union（保持稳定顺序）
        relpaths = sorted({rp for snap in wf_run_snapshots for rp in snap.keys()})

        artifacts_out: List[Dict] = []
        consistent_flags: List[int] = []
        for rel in relpaths:
            hashes: List[str] = []
            texts: List[str] = []
            for snap in wf_run_snapshots:
                s = snap.get(rel)
                if s is None:
                    raise AssertionError(f"artifact missing in some runs: wf{wf_id} {rel}")
                hashes.append(s.sha256)
                texts.append(s.normalized_text)

            baseline_hash = hashes[0]
            consistent = all(h == baseline_hash for h in hashes)
            consistent_flags.append(1 if consistent else 0)

            hash_counts: Dict[str, int] = {}
            for h in hashes:
                hash_counts[h] = hash_counts.get(h, 0) + 1

            item = {
                "relpath": rel,
                "consistent": bool(consistent),
                "hash_counts": hash_counts,
            }
            if not consistent:
                # 找一个第一个不一致的 run 做 diff
                mismatch_idx = next((idx for idx, h in enumerate(hashes) if h != baseline_hash), 1)
                item["diff"] = _diff_summary(a=texts[0], b=texts[mismatch_idx])
            artifacts_out.append(item)

        score = sum(consistent_flags) / max(1, len(consistent_flags))
        eval_results[wf_id] = {"runs": runs, "script": wf_script, "score": float(score), "artifacts": artifacts_out}

    report_md = _format_eval_report_md(results=eval_results)
    (workspace_root / "eval_report.md").write_text(report_md, encoding="utf-8")
    (workspace_root / "eval_score.json").write_text(json.dumps(eval_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("EXAMPLE_OK: workflows_15")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

