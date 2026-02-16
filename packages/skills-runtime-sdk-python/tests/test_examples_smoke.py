from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_example(*, repo_root: Path, script_relpath: str, tmp_path: Path, workspace_name: str) -> str:
    """
    以子进程方式运行 examples 脚本，并返回 stdout（用于关键字断言）。

    约束：
    - 必须离线可跑通（不依赖外网与真实 key）
    - 输出断言只做少量稳定标记，避免 brittle
    """

    src = repo_root / "packages" / "skills-runtime-sdk-python" / "src"
    script = repo_root / script_relpath
    assert script.exists(), f"example script missing: {script_relpath}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    env["PYTHONUNBUFFERED"] = "1"

    workspace_root = (tmp_path / workspace_name).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    p = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--workspace-root", str(workspace_root)],
        cwd=str(workspace_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert p.returncode == 0, (script_relpath, p.stdout, p.stderr)
    return p.stdout


def test_docs_for_coding_agent_assets_exist() -> None:
    """
    smoke：docs_for_coding_agent 的核心教学材料必须存在（供编码智能体可定位）。
    """

    repo_root = Path(__file__).resolve().parents[3]
    base = repo_root / "docs_for_coding_agent"

    required = [
        "README.md",
        "DOCS_INDEX.md",
        "00-quickstart-offline.md",
        "01-recipes.md",
        "02-ops-and-qa.md",
        "cheatsheet.zh-CN.md",
        "cheatsheet.en.md",
        "capability-inventory.md",
        "capability-coverage-map.md",
        "task-contract.md",
        "testing-strategy.md",
        "common-pitfalls.md",
    ]
    for rel in required:
        assert (base / rel).exists(), f"missing docs_for_coding_agent asset: {rel}"


def test_examples_smoke(tmp_path: Path) -> None:
    """
    smoke：跑通一组“全能力示例库”的代表性脚本。

    覆盖意图：
    - step_by_step：基础离线 run + tool_calls
    - tools：标准库工具
    - skills：preflight/scan 的最小链路
    - state：WAL replay / fork 的最小演示
    """

    repo_root = Path(__file__).resolve().parents[3]

    examples = [
        ("examples/step_by_step/01_offline_minimal_run", "run.py"),
        ("examples/step_by_step/02_offline_tool_call_read_file", "run.py"),
        ("examples/step_by_step/03_approvals_and_safety", "run.py"),
        ("examples/step_by_step/04_sandbox_evidence_and_verification", "run.py"),
        ("examples/step_by_step/05_exec_sessions_across_processes", "run.py"),
        ("examples/step_by_step/06_collab_across_processes", "run.py"),
        ("examples/step_by_step/07_skills_references_and_actions", "run.py"),
        ("examples/step_by_step/08_plan_and_user_input", "run.py"),
        ("examples/tools/01_standard_library_read_file", "run.py"),
        ("examples/tools/02_web_search_disabled_and_fake_provider", "run.py"),
        ("examples/skills/01_skills_preflight_and_scan", "run.py"),
        ("examples/state/01_wal_replay_and_fork", "run.py"),
        ("examples/workflows/01_multi_agent_repo_change_pipeline", "run.py"),
        ("examples/workflows/02_single_agent_form_interview", "run.py"),
        ("examples/workflows/03_multi_agent_reference_driven_pipeline", "run.py"),
        ("examples/workflows/04_map_reduce_parallel_subagents", "run.py"),
        ("examples/workflows/05_multi_agent_code_review_fix_qa_report", "run.py"),
        ("examples/workflows/06_wal_fork_and_resume_pipeline", "run.py"),
        ("examples/workflows/07_skill_exec_actions_module", "run.py"),
        ("examples/workflows/09_branching_router_workflow", "run.py"),
        ("examples/workflows/10_retry_degrade_workflow", "run.py"),
        ("examples/workflows/11_collab_parallel_subagents_workflow", "run.py"),
        ("examples/workflows/12_exec_sessions_engineering_workflow", "run.py"),
        ("examples/workflows/16_rules_based_parser", "run.py"),
        ("examples/workflows/17_minimal_rag_stub", "run.py"),
        ("examples/workflows/19_view_image_offline", "run.py"),
        ("examples/workflows/21_data_import_validate_and_fix", "run.py"),
    ]

    for example_dir, entry in examples:
        readme = repo_root / example_dir / "README.md"
        assert readme.exists(), f"missing example README: {example_dir}/README.md"

        rel = f"{example_dir}/{entry}"
        workspace_name = example_dir.replace("/", "_")
        out = _run_example(repo_root=repo_root, script_relpath=rel, tmp_path=tmp_path, workspace_name=workspace_name)
        assert "EXAMPLE_OK:" in out, (rel, out)


def test_example_workflows_19_view_image_offline(tmp_path: Path) -> None:
    """
    smoke（narrow）：只跑 workflows/19_view_image_offline，便于本地快速验证。
    """

    repo_root = Path(__file__).resolve().parents[3]
    rel = "examples/workflows/19_view_image_offline/run.py"
    out = _run_example(
        repo_root=repo_root,
        script_relpath=rel,
        tmp_path=tmp_path,
        workspace_name="examples_workflows_19_view_image_offline",
    )
    assert "EXAMPLE_OK:" in out, (rel, out)


def test_example_workflows_16_rules_based_parser(tmp_path: Path) -> None:
    """
    smoke（narrow）：只跑 workflows/16_rules_based_parser，便于本地快速验证。
    """

    repo_root = Path(__file__).resolve().parents[3]
    rel = "examples/workflows/16_rules_based_parser/run.py"
    out = _run_example(
        repo_root=repo_root,
        script_relpath=rel,
        tmp_path=tmp_path,
        workspace_name="examples_workflows_16_rules_based_parser",
    )
    assert "EXAMPLE_OK:" in out, (rel, out)


def test_example_workflows_17_minimal_rag_stub(tmp_path: Path) -> None:
    """
    smoke（narrow）：只跑 workflows/17_minimal_rag_stub，便于本地快速验证。
    """

    repo_root = Path(__file__).resolve().parents[3]
    rel = "examples/workflows/17_minimal_rag_stub/run.py"
    out = _run_example(
        repo_root=repo_root,
        script_relpath=rel,
        tmp_path=tmp_path,
        workspace_name="examples_workflows_17_minimal_rag_stub",
    )
    assert "EXAMPLE_OK:" in out, (rel, out)


def test_example_workflows_21_data_import_validate_and_fix(tmp_path: Path) -> None:
    """
    smoke（narrow）：只跑 workflows/21_data_import_validate_and_fix，便于本地快速验证。
    """

    repo_root = Path(__file__).resolve().parents[3]
    rel = "examples/workflows/21_data_import_validate_and_fix/run.py"
    out = _run_example(
        repo_root=repo_root,
        script_relpath=rel,
        tmp_path=tmp_path,
        workspace_name="examples_workflows_21_data_import_validate_and_fix",
    )
    assert "EXAMPLE_OK:" in out, (rel, out)
