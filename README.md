# WeaveGraph

**Semantic Code-Graph Worktree Scheduler, Min-Cut Multi-Agent Task Partitioner & 3-Way AST Semantic Merge Verifier**

WeaveGraph solves the #1 bottleneck identified across the Global GitHub AI-500 & Complexity-100 Atlas (`AI Coding Agents & Dev Harnesses`, 121 repos): parallel coding agents (`Claude Code`, `Codex`, `OpenCode`, `Orca`) running in `git worktree` lanes frequently edit coupled files and pass text-based `git merge` cleanly while silently breaking function signatures, keyword parameters, imports, or schema fields.

## Key Capabilities

1. **Native C++20 CSR Graph Engine (`csrc/weavegraph_kernels.cpp`):**
   - Iterative **Tarjan Strongly Connected Components** ($O(V+E)$)
   - Shortest-hop **Reverse-Dependency Blast Radius BFS**
   - **Fiduccia-Mattheyses / Kernighan-Lin Min-Cut Partitioning** minimizing cross-agent coupling across $K$ parallel git worktrees
   - **Topological Dependency Wave Scheduling** over condensed SCC DAGs
2. **3-Way Semantic AST Merge Verifier (`verify-merge`):**
   - Catches 5 classes of silent cross-agent contract breaks invisible to `git merge`: `SIGNATURE_ARITY_BREAK`, `REMOVED_KEYWORD_PARAM`, `DANGLING_IMPORT_OR_SYMBOL`, `CONTRACT_FIELD_REMOVED`, and `DUPLICATE_DEFINITION_CLASH`.
3. **Drop-In CLI, FastAPI REST Server & Stdio MCP Server (`.mcp.json`):**
   - Exposes `weavegraph_index_repo`, `weavegraph_blast_radius`, `weavegraph_plan_worktrees`, and `weavegraph_verify_merge` to Claude Code, Cursor, and Codex.
4. **Interactive React 18 + TypeScript Workbench (`ui/`):**
   - Visualizes worktree lanes, cross-agent coupling reduction, semantic merge violations, and blast-radius hops.

## Quickstart

```bash
uv sync --python 3.13 --all-extras
uv run weavegraph demo
uv run weavegraph scan .
uv run weavegraph plan . --agents 3
uv run weavegraph serve --port 8150
```

## Verification & Quality Gates

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest --cov=weavegraph --cov-report=term-missing --cov-fail-under=95
cd ui && npm ci && npm run build
```
