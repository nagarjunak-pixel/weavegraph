"""Tests for AST/TS indexer, relative imports, blast radius, and min-cut worktree planner."""

from __future__ import annotations

from pathlib import Path

import pytest

from weavegraph.indexer import (
    build_file_graph,
    compute_blast_radius,
    load_repo_files,
    summarize_graph,
)
from weavegraph.scheduler import plan_worktrees
from weavegraph.types import TaskItem


def test_build_graph_python_ts_and_relative_imports() -> None:
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "class Model:\n    val: int = 1\ndef helper(x: int) -> int:\n    return x\n",
        "src/pkg/worker.py": (
            "from .core import Model, helper\n"
            "import src.pkg.core\n"
            "def run(m: Model) -> int:\n"
            "    return helper(m.val)\n"
        ),
        "src/pkg/broken.py": "def broken(:\n    pass\n",
        "ui/utils.ts": "export function fmt(x: number): string { return String(x); }\n",
        "ui/Card.ts": "import { fmt } from './utils';\nexport interface CardProps { title: string; }\nexport function render() { return fmt(10); }\n",
    }
    graph = build_file_graph(files)
    summary = summarize_graph(graph)
    assert summary.total_files == 6
    assert summary.total_edges >= 3
    assert graph.waves["src/pkg/core.py"] < graph.waves["src/pkg/worker.py"]
    assert graph.waves["ui/utils.ts"] < graph.waves["ui/Card.ts"]

    impact = compute_blast_radius(graph, ["src/pkg/core.py"])
    affected_paths = {n.file_path for n in impact.affected}
    assert "src/pkg/core.py" in affected_paths
    assert "src/pkg/worker.py" in affected_paths
    assert "ui/Card.ts" not in affected_paths


def test_plan_worktrees_reduces_coupling_and_validates_k() -> None:
    files = {
        "a1.py": "def f1():\n    return 1\n",
        "a2.py": "from a1 import f1\ndef f2():\n    return f1()\n",
        "a3.py": "from a2 import f2\ndef f3():\n    return f2()\n",
        "b1.py": "def g1():\n    return 2\n",
        "b2.py": "from b1 import g1\ndef g2():\n    return g1()\n",
        "b3.py": "from b2 import g2\ndef g3():\n    return g2()\n",
    }
    graph = build_file_graph(files)
    tasks = [
        TaskItem(task_id="T1", title="Cluster A", target_files=["a1.py", "a2.py", "a3.py"]),
        TaskItem(task_id="T2", title="Cluster B", target_files=["b1.py", "b2.py", "b3.py"]),
    ]
    plan = plan_worktrees(graph, num_agents=2, tasks=tasks)
    assert plan.agent_count == 2
    assert plan.mincut_cross_coupling <= plan.naive_cross_coupling
    assert plan.coupling_reduction_pct >= 50.0
    assigned = [f for lane in plan.lanes for f in lane.files]
    assert sorted(assigned) == sorted(files.keys())

    with pytest.raises(ValueError, match="num_agents"):
        plan_worktrees(graph, num_agents=0)


def test_load_repo_files_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (tmp_path / "extra.py").write_text("def ok2():\n    return 2\n", encoding="utf-8")
    ignored = tmp_path / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    (ignored / "index.js").write_text("export function x() {}", encoding="utf-8")

    loaded = load_repo_files(tmp_path, max_files=1)
    assert list(loaded.keys()) == ["app.py"]


def test_relative_parent_imports_ts_and_planner_edge_cases() -> None:
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/sub/__init__.py": "",
        "src/pkg/sub/leaf.py": "class Obj:\n    tag = 'v1'\n    def ping(self) -> int:\n        return 1\n",
        "src/pkg/sub/caller.py": "from ..sub import leaf\nimport os\ndef f(x):\n    return x.ping(), leaf.Obj.tag\n",
        "src/pkg/top.py": "from pkg.sub import leaf\ndef g():\n    return leaf.Obj().ping()\n",
        "ui/base.ts": "export function fmt(x: number) { return String(x); }\n",
        "ui/sub/view.ts": "import { fmt } from '../base';\nimport React from 'react';\nexport function show() { return fmt(1); }\n",
        "ui/sub/missing.ts": "import { x } from '../does_not_exist';\n",
    }
    graph = build_file_graph(files)
    assert any(
        e.source == "src/pkg/top.py" and e.target == "src/pkg/sub/leaf.py" for e in graph.edges
    )
    assert any(e.source == "ui/sub/view.ts" and e.target == "ui/base.ts" for e in graph.edges)

    single_plan = plan_worktrees(graph, num_agents=1)
    assert single_plan.agent_count == 1
    assert single_plan.naive_cross_coupling == 0

    zero_edge_graph = build_file_graph({"a.py": "x = 1\n", "b.py": "y = 2\n"})
    z_plan = plan_worktrees(
        zero_edge_graph,
        num_agents=2,
        tasks=[TaskItem(task_id="T0", title="Missing", target_files=["missing.py"])],
    )
    assert z_plan.coupling_reduction_pct == 0.0

    empty_graph = build_file_graph({})
    assert empty_graph.files == []
    assert summarize_graph(empty_graph).wave_count == 0
    assert compute_blast_radius(empty_graph, ["none.py"]).affected == []
