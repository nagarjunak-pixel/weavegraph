"""Tests for FastAPI REST endpoints, Typer CLI commands, and stdio MCP JSON-RPC server."""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from weavegraph.api import app
from weavegraph.cli import app as cli_app
from weavegraph.demo import DEMO_BASE_TREE
from weavegraph.mcp import handle_jsonrpc_message, serve_stdio

client = TestClient(app)
runner = CliRunner()


def test_fastapi_endpoints() -> None:
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_demo = client.get("/api/v1/demo")
    assert r_demo.status_code == 200
    assert r_demo.json()["merge_verification"]["verdict"] == "SEMANTIC_CONFLICT_BLOCK"

    r_idx = client.post("/api/v1/index", json={"files": DEMO_BASE_TREE})
    assert r_idx.status_code == 200
    assert r_idx.json()["total_files"] == len(DEMO_BASE_TREE)

    r_blast = client.post(
        "/api/v1/blast-radius",
        json={"files": DEMO_BASE_TREE, "seed_files": ["src/core/models.py"]},
    )
    assert r_blast.status_code == 200
    assert len(r_blast.json()["affected"]) >= 3

    r_plan = client.post(
        "/api/v1/plan",
        json={"files": DEMO_BASE_TREE, "num_agents": 3, "tasks": []},
    )
    assert r_plan.status_code == 200
    assert r_plan.json()["agent_count"] == 3

    r_merge = client.post(
        "/api/v1/verify-merge",
        json={
            "base_tree": DEMO_BASE_TREE,
            "agent_a_tree": DEMO_BASE_TREE,
            "agent_b_tree": DEMO_BASE_TREE,
        },
    )
    assert r_merge.status_code == 200
    assert r_merge.json()["verdict"] == "SAFE_TO_MERGE"


def test_cli_commands(tmp_path: Path) -> None:
    res_demo = runner.invoke(cli_app, ["demo", "--json"])
    assert res_demo.exit_code == 0
    assert "worktree_plan" in res_demo.stdout

    res_demo_text = runner.invoke(cli_app, ["demo"])
    assert res_demo_text.exit_code == 0

    base_d = tmp_path / "base"
    a_d = tmp_path / "a"
    b_d = tmp_path / "b"
    for d in (base_d, a_d, b_d):
        d.mkdir()
        (d / "mod.py").write_text(
            "def greet(name: str) -> str:\n    return name\n", encoding="utf-8"
        )
        (d / "app.py").write_text("from mod import greet\n", encoding="utf-8")

    assert runner.invoke(cli_app, ["scan", str(base_d)]).exit_code == 0
    assert runner.invoke(cli_app, ["scan", str(base_d), "--json"]).exit_code == 0
    assert runner.invoke(cli_app, ["impact", str(base_d), "--seed", "mod.py"]).exit_code == 0
    assert (
        runner.invoke(cli_app, ["impact", str(base_d), "--seed", "mod.py", "--json"]).exit_code == 0
    )
    assert runner.invoke(cli_app, ["plan", str(base_d), "--agents", "2"]).exit_code == 0
    assert runner.invoke(cli_app, ["plan", str(base_d), "--agents", "2", "--json"]).exit_code == 0
    assert runner.invoke(cli_app, ["verify-merge", str(base_d), str(a_d), str(b_d)]).exit_code == 0

    # Introduce semantic break -> CLI exit code 2
    (a_d / "mod.py").write_text(
        "def greet_v2(name: str) -> str:\n    return name\n", encoding="utf-8"
    )
    (b_d / "app.py").write_text("from mod import greet\nx = greet('hi')\n", encoding="utf-8")
    res_fail = runner.invoke(cli_app, ["verify-merge", str(base_d), str(a_d), str(b_d), "--json"])
    assert res_fail.exit_code == 2


def test_mcp_protocol_and_stdio(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import f\nx = f()\n", encoding="utf-8")

    assert handle_jsonrpc_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    init_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "weavegraph"
    ping_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp is not None
    assert ping_resp["result"] == {}
    tools_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert tools_resp is not None
    assert len(tools_resp["result"]["tools"]) == 4

    for tid, tool_name, args in [
        (4, "weavegraph_index_repo", {"repo_path": str(tmp_path)}),
        (5, "weavegraph_blast_radius", {"repo_path": str(tmp_path), "seed_files": ["a.py"]}),
        (
            6,
            "weavegraph_plan_worktrees",
            {"repo_path": str(tmp_path), "num_agents": 2, "tasks": []},
        ),
        (
            7,
            "weavegraph_verify_merge",
            {
                "base_tree": {"a.py": "def f(): pass"},
                "agent_a_tree": {"a.py": "def f(): pass"},
                "agent_b_tree": {"a.py": "def f(): pass"},
            },
        ),
    ]:
        r = handle_jsonrpc_message(
            {
                "jsonrpc": "2.0",
                "id": tid,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": args},
            }
        )
        assert r is not None and not r["result"]["isError"]

    err_tool = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "bad_tool", "arguments": {}},
        }
    )
    assert err_tool is not None and err_tool["result"]["isError"] is True
    err_meth = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 9, "method": "unknown/method"})
    assert err_meth is not None and err_meth["error"]["code"] == -32601

    stdin = io.StringIO('\n{bad json}\n{"jsonrpc":"2.0","id":10,"method":"ping"}\n')
    stdout = io.StringIO()
    serve_stdio(stdin, stdout)
    lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["id"] == 10
