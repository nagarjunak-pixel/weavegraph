"""K-agent min-cut task partitioning & topological git worktree scheduler."""

from __future__ import annotations

from weavegraph.kernels.native import backend_name, mincut_partition
from weavegraph.types import AgentLane, FileGraphData, TaskItem, WorktreePlan


def _naive_round_robin_coupling(
    files: list[str],
    weighted_edges: list[tuple[int, int, int]],
    eff_k: int,
) -> int:
    if eff_k <= 1:
        return 0
    naive_part = [i % eff_k for i in range(len(files))]
    return sum(w for u, v, w in weighted_edges if naive_part[u] != naive_part[v])


def plan_worktrees(
    graph: FileGraphData,
    num_agents: int = 3,
    tasks: list[TaskItem] | None = None,
    *,
    use_native: bool = True,
) -> WorktreePlan:
    """Partition repository files & tasks into `num_agents` isolated worktree lanes."""
    if num_agents < 1:
        raise ValueError("num_agents must be >= 1")

    files = graph.files
    n = len(files)
    file_to_idx = {f: i for i, f in enumerate(files)}

    agg_edges: dict[tuple[int, int], int] = {}
    for e in graph.edges:
        u = file_to_idx[e.source]
        v = file_to_idx[e.target]
        if u != v:
            key = (min(u, v), max(u, v))
            agg_edges[key] = agg_edges.get(key, 0) + e.weight

    weighted_edges = [(u, v, w) for (u, v), w in sorted(agg_edges.items())]
    node_weights = [graph.file_weights.get(f, 1) for f in files]

    # Incorporate task co-editing affinity weights so files in the same task stick together
    task_list = tasks or []
    task_weight_map = dict(zip(files, node_weights, strict=False))
    for t in task_list:
        for idx_a, fa in enumerate(t.target_files):
            if fa in file_to_idx:
                task_weight_map[fa] = task_weight_map.get(fa, 1) + t.estimated_effort
                for fb in t.target_files[idx_a + 1 :]:
                    if fb in file_to_idx and fa != fb:
                        ua, ub = file_to_idx[fa], file_to_idx[fb]
                        weighted_edges.append((ua, ub, 25))
    node_weights = [task_weight_map.get(f, 1) for f in files]

    eff_k = max(1, min(num_agents, n)) if n > 0 else 1
    parts, mincut_cost = mincut_partition(
        n, weighted_edges, node_weights, eff_k, use_native=use_native
    )
    naive_cost = _naive_round_robin_coupling(files, weighted_edges, eff_k)

    if naive_cost > 0:
        reduction = round(max(0.0, (naive_cost - mincut_cost) * 100.0 / naive_cost), 2)
    else:
        reduction = 0.0

    # Shared boundary files touched by cross-partition edges -> contract lock recommended
    boundary_files: set[str] = set()
    for u, v, _ in weighted_edges:
        if parts[u] != parts[v]:
            # Lock the target / more depended-on file in the pair
            w_u = graph.waves.get(files[u], 0)
            w_v = graph.waves.get(files[v], 0)
            boundary_files.add(files[v] if w_v <= w_u else files[u])

    # Map tasks to their dominant lane
    lane_files: dict[int, list[str]] = {p: [] for p in range(eff_k)}
    for idx, p in enumerate(parts):
        lane_files[p].append(files[idx])

    lane_tasks: dict[int, list[str]] = {p: [] for p in range(eff_k)}
    for t in task_list:
        counts = dict.fromkeys(range(eff_k), 0)
        for tf in t.target_files:
            if tf in file_to_idx:
                counts[parts[file_to_idx[tf]]] += 1
        best_lane = max(counts, key=lambda p: (counts[p], -len(lane_tasks[p])))
        lane_tasks[best_lane].append(t.task_id)

    lanes: list[AgentLane] = []
    for p in range(eff_k):
        l_files = sorted(lane_files[p])
        l_idx = {file_to_idx[f] for f in l_files}
        internal = sum(w for u, v, w in weighted_edges if u in l_idx and v in l_idx)
        avg_wave = round(sum(graph.waves.get(f, 0) for f in l_files) / max(1, len(l_files)))
        branch = f"agent/lane-{p + 1}"
        lanes.append(
            AgentLane(
                agent_id=f"agent-{p + 1}",
                branch_name=branch,
                worktree_command=f"git worktree add ../wt-agent-{p + 1} -b {branch}",
                files=l_files,
                task_ids=lane_tasks[p],
                execution_wave=avg_wave,
                internal_coupling=internal,
            )
        )

    lanes.sort(key=lambda ln: (ln.execution_wave, ln.agent_id))
    return WorktreePlan(
        agent_count=eff_k,
        mincut_cross_coupling=mincut_cost,
        naive_cross_coupling=naive_cost,
        coupling_reduction_pct=reduction,
        shared_contract_locks=sorted(boundary_files),
        lanes=lanes,
        backend=backend_name() if use_native else "python-fallback",
    )
