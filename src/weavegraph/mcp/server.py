"""Standards-compliant JSON-RPC 2.0 stdio MCP server for coding agents."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from weavegraph import __version__
from weavegraph.indexer import (
    build_file_graph,
    compute_blast_radius,
    load_repo_files,
    summarize_graph,
)
from weavegraph.merge import verify_three_way_merge
from weavegraph.scheduler import plan_worktrees
from weavegraph.types import TaskItem

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "weavegraph_index_repo",
        "description": "Index a repository AST dependency graph, Tarjan SCC cycles, and topological build waves.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Absolute or relative repository directory",
                },
                "max_files": {"type": "integer", "default": 500},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "weavegraph_blast_radius",
        "description": "Compute shortest-hop transitive blast radius from changed seed files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "seed_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo_path", "seed_files"],
        },
    },
    {
        "name": "weavegraph_plan_worktrees",
        "description": "Partition files and tasks across K parallel coding agent git worktrees using min-cut graph optimization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "num_agents": {"type": "integer", "default": 3},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                            "title": {"type": "string"},
                            "target_files": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["task_id", "title"],
                    },
                },
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "weavegraph_verify_merge",
        "description": "Verify 3-way merge safety across Base, Agent A, and Agent B file maps for silent semantic AST breaks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_tree": {"type": "object"},
                "agent_a_tree": {"type": "object"},
                "agent_b_tree": {"type": "object"},
            },
            "required": ["base_tree", "agent_a_tree", "agent_b_tree"],
        },
    },
]


def _dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "weavegraph_index_repo":
        files = load_repo_files(str(args["repo_path"]), max_files=int(args.get("max_files", 500)))
        graph = build_file_graph(files)
        return summarize_graph(graph).model_dump(mode="json")

    if name == "weavegraph_blast_radius":
        files = load_repo_files(str(args["repo_path"]))
        graph = build_file_graph(files)
        seeds = [str(s) for s in args.get("seed_files", [])]
        return compute_blast_radius(graph, seeds).model_dump(mode="json")

    if name == "weavegraph_plan_worktrees":
        files = load_repo_files(str(args["repo_path"]))
        graph = build_file_graph(files)
        tasks = [TaskItem(**t) for t in args.get("tasks", [])]
        return plan_worktrees(
            graph, num_agents=int(args.get("num_agents", 3)), tasks=tasks
        ).model_dump(mode="json")

    if name == "weavegraph_verify_merge":
        return verify_three_way_merge(
            base_tree={str(k): str(v) for k, v in dict(args["base_tree"]).items()},
            agent_a_tree={str(k): str(v) for k, v in dict(args["agent_a_tree"]).items()},
            agent_b_tree={str(k): str(v) for k, v in dict(args["agent_b_tree"]).items()},
        ).model_dump(mode="json")

    raise KeyError(f"Unknown tool: {name}")


def handle_jsonrpc_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single MCP JSON-RPC 2.0 request or notification."""
    method = str(msg.get("method", ""))
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method.startswith("notifications/") or req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "weavegraph", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}

    if method == "tools/call":
        tool_name = str(params.get("name", ""))
        tool_args = dict(params.get("arguments") or {})
        try:
            payload = _dispatch_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                    "structuredContent": payload,
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio(in_stream: TextIO = sys.stdin, out_stream: TextIO = sys.stdout) -> None:
    """Run line-delimited JSON-RPC 2.0 MCP loop on stdio."""
    for raw_line in in_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            out_stream.write(json.dumps(err) + "\n")
            out_stream.flush()
            continue

        resp = handle_jsonrpc_message(msg)
        if resp is not None:
            out_stream.write(json.dumps(resp) + "\n")
            out_stream.flush()
