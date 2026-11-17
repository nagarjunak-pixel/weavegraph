"""Native C++20 ctypes bridge (`clang++ -O3`) and pure-Python reference kernels."""

from __future__ import annotations

import ctypes
import hashlib
import math
import shutil
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path


def _compile_native_lib() -> tuple[ctypes.CDLL | None, str]:
    src_path = Path(__file__).resolve().parents[3] / "csrc" / "weavegraph_kernels.cpp"
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not src_path.exists() or compiler is None:
        return None, "python-fallback"

    digest = hashlib.sha256(src_path.read_bytes()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / "weavegraph_native_cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = ".dylib" if Path("/System/Library").exists() else ".so"
    lib_path = out_dir / f"libweavegraph_kernels_{digest}{ext}"

    if not lib_path.exists():
        cmd = [
            compiler,
            "-O3",
            "-std=c++20",
            "-shared",
            "-fPIC",
            str(src_path),
            "-o",
            str(lib_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None, "python-fallback"

    lib = ctypes.CDLL(str(lib_path))
    lib.weavegraph_tarjan_scc.argtypes = [
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.weavegraph_tarjan_scc.restype = ctypes.c_int32

    lib.weavegraph_blast_radius.argtypes = [
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.weavegraph_blast_radius.restype = ctypes.c_int32

    lib.weavegraph_mincut_partition.argtypes = [
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int64),
    ]
    lib.weavegraph_mincut_partition.restype = ctypes.c_int32

    lib.weavegraph_schedule_waves.argtypes = [
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.weavegraph_schedule_waves.restype = ctypes.c_int32
    return lib, "c++20-native (clang++ -O3)"


_NATIVE_LIB, _BACKEND_NAME = _compile_native_lib()


def backend_name() -> str:
    """Return active graph kernel backend identifier."""
    return _BACKEND_NAME


def _edges_to_csr(num_nodes: int, edges: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    adj: list[list[int]] = [[] for _ in range(num_nodes)]
    for u, v in edges:
        if 0 <= u < num_nodes and 0 <= v < num_nodes:
            adj[u].append(v)
    row_ptr: list[int] = [0]
    col_idx: list[int] = []
    for neighbors in adj:
        col_idx.extend(neighbors)
        row_ptr.append(len(col_idx))
    return row_ptr, col_idx


def _tarjan_scc_python(num_nodes: int, row_ptr: list[int], col_idx: list[int]) -> list[int]:
    index = [-1] * num_nodes
    lowlink = [0] * num_nodes
    on_stack = [False] * num_nodes
    t_stack: list[int] = []
    comp_ids = [-1] * num_nodes
    cur_idx = 0
    comp_count = 0

    for start in range(num_nodes):
        if index[start] != -1:
            continue
        index[start] = cur_idx
        lowlink[start] = cur_idx
        cur_idx += 1
        on_stack[start] = True
        t_stack.append(start)
        call_stack: list[list[int]] = [[start, row_ptr[start]]]

        while call_stack:
            u, pos = call_stack[-1]
            if pos < row_ptr[u + 1]:
                v = col_idx[pos]
                call_stack[-1][1] = pos + 1
                if index[v] == -1:
                    index[v] = cur_idx
                    lowlink[v] = cur_idx
                    cur_idx += 1
                    on_stack[v] = True
                    t_stack.append(v)
                    call_stack.append([v, row_ptr[v]])
                elif on_stack[v]:
                    if index[v] < lowlink[u]:
                        lowlink[u] = index[v]
            else:
                if lowlink[u] == index[u]:
                    while True:
                        w = t_stack.pop()
                        on_stack[w] = False
                        comp_ids[w] = comp_count
                        if w == u:
                            break
                    comp_count += 1
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    if lowlink[u] < lowlink[parent]:
                        lowlink[parent] = lowlink[u]
    return comp_ids


def tarjan_scc(
    num_nodes: int,
    edges: list[tuple[int, int]],
    *,
    use_native: bool = True,
) -> list[int]:
    """Compute strongly connected component IDs for each node in `0..num_nodes-1`."""
    if num_nodes <= 0:
        return []
    row_ptr, col_idx = _edges_to_csr(num_nodes, edges)
    if use_native and _NATIVE_LIB is not None:
        rp_arr = (ctypes.c_int32 * len(row_ptr))(*row_ptr)
        ci_arr = (ctypes.c_int32 * max(1, len(col_idx)))(*(col_idx or [0]))
        out_arr = (ctypes.c_int32 * num_nodes)()
        rc = _NATIVE_LIB.weavegraph_tarjan_scc(num_nodes, rp_arr, ci_arr, out_arr)
        if rc >= 0:
            return [int(out_arr[i]) for i in range(num_nodes)]
    return _tarjan_scc_python(num_nodes, row_ptr, col_idx)


def _blast_radius_python(
    num_nodes: int, rev_row_ptr: list[int], rev_col_idx: list[int], seeds: list[int]
) -> list[int]:
    dists = [-1] * num_nodes
    q: deque[int] = deque()
    for s in seeds:
        if 0 <= s < num_nodes and dists[s] == -1:
            dists[s] = 0
            q.append(s)
    while q:
        u = q.popleft()
        d_next = dists[u] + 1
        for pos in range(rev_row_ptr[u], rev_row_ptr[u + 1]):
            v = rev_col_idx[pos]
            if dists[v] == -1:
                dists[v] = d_next
                q.append(v)
    return dists


def blast_radius(
    num_nodes: int,
    edges: list[tuple[int, int]],
    seeds: list[int],
    *,
    use_native: bool = True,
) -> list[int]:
    """Compute shortest reverse-dependency hop distance from `seeds` (`u->v` means `u` depends on `v`)."""
    if num_nodes <= 0:
        return []
    rev_edges = [(v, u) for (u, v) in edges]
    rev_rp, rev_ci = _edges_to_csr(num_nodes, rev_edges)
    if use_native and _NATIVE_LIB is not None:
        rp_arr = (ctypes.c_int32 * len(rev_rp))(*rev_rp)
        ci_arr = (ctypes.c_int32 * max(1, len(rev_ci)))(*(rev_ci or [0]))
        s_arr = (ctypes.c_int32 * max(1, len(seeds)))(*(seeds or [0]))
        out_arr = (ctypes.c_int32 * num_nodes)()
        rc = _NATIVE_LIB.weavegraph_blast_radius(
            num_nodes, rp_arr, ci_arr, s_arr, len(seeds), out_arr
        )
        if rc >= 0:
            return [int(out_arr[i]) for i in range(num_nodes)]
    return _blast_radius_python(num_nodes, rev_rp, rev_ci, seeds)


def _mincut_partition_python(
    num_nodes: int,
    weighted_edges: list[tuple[int, int, int]],
    node_weights: list[int],
    k_parts: int,
) -> tuple[list[int], int]:
    if num_nodes == 0:
        return [], 0
    if k_parts <= 1 or num_nodes == 1:
        return [0] * num_nodes, 0

    eff_k = min(k_parts, num_nodes)
    n_weight = [max(1, node_weights[i]) if i < len(node_weights) else 1 for i in range(num_nodes)]
    total_w = sum(n_weight)
    max_single = max(n_weight)

    adj: list[list[tuple[int, int]]] = [[] for _ in range(num_nodes)]
    deg = [0] * num_nodes
    for u, v, w in weighted_edges:
        if 0 <= u < num_nodes and 0 <= v < num_nodes and u != v and w > 0:
            adj[u].append((v, w))
            adj[v].append((u, w))
            deg[u] += w
            deg[v] += w

    order = sorted(range(num_nodes), key=lambda i: (-deg[i], -n_weight[i], i))
    part = [-1] * num_nodes
    part_w = [0] * eff_k
    part_cnt = [0] * eff_k

    for p in range(eff_k):
        seed = order[p]
        part[seed] = p
        part_w[p] += n_weight[seed]
        part_cnt[p] += 1

    ideal_w = (total_w + eff_k - 1) // eff_k
    max_cap = max(ideal_w + max_single, math.ceil(ideal_w * 1.25))
    max_cnt = max(1, math.ceil((num_nodes / eff_k) * 1.35))

    for u in order[eff_k:]:
        aff = [0] * eff_k
        for nb, w in adj[u]:
            if part[nb] >= 0:
                aff[part[nb]] += w
        best_p = -1
        best_score = -1e18
        for p in range(eff_k):
            if part_w[p] + n_weight[u] > max_cap and part_cnt[p] > 0:
                continue
            if part_cnt[p] + 1 > max_cnt and part_cnt[p] > 0:
                continue
            sc = aff[p] - 0.01 * part_w[p]
            if best_p == -1 or sc > best_score:
                best_score = sc
                best_p = p
        if best_p == -1:
            best_p = min(range(eff_k), key=lambda p: part_w[p])
        part[u] = best_p
        part_w[best_p] += n_weight[u]
        part_cnt[best_p] += 1

    for _ in range(16):
        improved = False
        for u in range(num_nodes):
            cur_p = part[u]
            if part_cnt[cur_p] <= 1:
                continue
            aff = [0] * eff_k
            for nb, w in adj[u]:
                aff[part[nb]] += w
            cur_aff = aff[cur_p]
            best_p = cur_p
            best_gain = 0
            for p in range(eff_k):
                if p == cur_p:
                    continue
                if part_w[p] + n_weight[u] > max_cap or part_cnt[p] + 1 > max_cnt:
                    continue
                gain = aff[p] - cur_aff
                if gain > best_gain:
                    best_gain = gain
                    best_p = p
            if best_p != cur_p:
                part[u] = best_p
                part_w[cur_p] -= n_weight[u]
                part_cnt[cur_p] -= 1
                part_w[best_p] += n_weight[u]
                part_cnt[best_p] += 1
                improved = True
        if not improved:
            break

    cut_w = sum(
        w
        for u, v, w in weighted_edges
        if 0 <= u < num_nodes and 0 <= v < num_nodes and w > 0 and part[u] != part[v]
    )
    return part, cut_w


def mincut_partition(
    num_nodes: int,
    weighted_edges: list[tuple[int, int, int]],
    node_weights: list[int],
    k_parts: int,
    *,
    use_native: bool = True,
) -> tuple[list[int], int]:
    """Partition `num_nodes` into `<= k_parts` balanced lanes minimizing cross-lane cut weight."""
    if k_parts <= 0:
        raise ValueError("k_parts must be >= 1")
    if num_nodes <= 0:
        return [], 0
    if use_native and _NATIVE_LIB is not None:
        m = len(weighted_edges)
        u_arr = (ctypes.c_int32 * max(1, m))(*([e[0] for e in weighted_edges] or [0]))
        v_arr = (ctypes.c_int32 * max(1, m))(*([e[1] for e in weighted_edges] or [0]))
        w_arr = (ctypes.c_int32 * max(1, m))(*([max(0, e[2]) for e in weighted_edges] or [0]))
        nw_list = [
            max(1, node_weights[i]) if i < len(node_weights) else 1 for i in range(num_nodes)
        ]
        nw_arr = (ctypes.c_int32 * num_nodes)(*nw_list)
        out_parts = (ctypes.c_int32 * num_nodes)()
        out_cut = ctypes.c_int64(0)
        rc = _NATIVE_LIB.weavegraph_mincut_partition(
            num_nodes,
            u_arr,
            v_arr,
            w_arr,
            m,
            nw_arr,
            k_parts,
            out_parts,
            ctypes.byref(out_cut),
        )
        if rc == 0:
            return [int(out_parts[i]) for i in range(num_nodes)], int(out_cut.value)
    return _mincut_partition_python(num_nodes, weighted_edges, node_weights, k_parts)


def _schedule_waves_python(num_nodes: int, row_ptr: list[int], col_idx: list[int]) -> list[int]:
    comp = _tarjan_scc_python(num_nodes, row_ptr, col_idx)
    num_scc = (max(comp) + 1) if comp else 0
    scc_nodes: list[list[int]] = [[] for _ in range(num_scc)]
    for u, c in enumerate(comp):
        scc_nodes[c].append(u)
    scc_wave = [0] * num_scc
    for c in range(num_scc):
        w = 0
        for u in scc_nodes[c]:
            for pos in range(row_ptr[u], row_ptr[u + 1]):
                v = col_idx[pos]
                cv = comp[v]
                if cv != c and scc_wave[cv] + 1 > w:
                    w = scc_wave[cv] + 1
        scc_wave[c] = w
    return [scc_wave[comp[u]] for u in range(num_nodes)]


def schedule_waves(
    num_nodes: int,
    edges: list[tuple[int, int]],
    *,
    use_native: bool = True,
) -> list[int]:
    """Compute dependency execution waves (0 = leaf foundations, k = depends on wave k-1)."""
    if num_nodes <= 0:
        return []
    row_ptr, col_idx = _edges_to_csr(num_nodes, edges)
    if use_native and _NATIVE_LIB is not None:
        rp_arr = (ctypes.c_int32 * len(row_ptr))(*row_ptr)
        ci_arr = (ctypes.c_int32 * max(1, len(col_idx)))(*(col_idx or [0]))
        out_waves = (ctypes.c_int32 * num_nodes)()
        rc = _NATIVE_LIB.weavegraph_schedule_waves(num_nodes, rp_arr, ci_arr, out_waves)
        if rc >= 0:
            return [int(out_waves[i]) for i in range(num_nodes)]
    return _schedule_waves_python(num_nodes, row_ptr, col_idx)


def benchmark_kernels(num_nodes: int = 400, k_parts: int = 4) -> dict[str, float | str | int]:
    """Benchmark native C++20 kernels vs Python fallback on a synthetic dependency graph."""
    edges: list[tuple[int, int]] = []
    weighted_edges: list[tuple[int, int, int]] = []
    for i in range(num_nodes):
        for step in (1, 2, 5):
            if i + step < num_nodes:
                edges.append((i + step, i))
                weighted_edges.append((i + step, i, 10 - step))
    node_weights = [1 + (i % 4) for i in range(num_nodes)]

    t0 = time.perf_counter()
    tarjan_scc(num_nodes, edges, use_native=True)
    blast_radius(num_nodes, edges, [0, 1], use_native=True)
    mincut_partition(num_nodes, weighted_edges, node_weights, k_parts, use_native=True)
    schedule_waves(num_nodes, edges, use_native=True)
    native_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    tarjan_scc(num_nodes, edges, use_native=False)
    blast_radius(num_nodes, edges, [0, 1], use_native=False)
    mincut_partition(num_nodes, weighted_edges, node_weights, k_parts, use_native=False)
    schedule_waves(num_nodes, edges, use_native=False)
    python_ms = (time.perf_counter() - t1) * 1000.0

    return {
        "backend": _BACKEND_NAME,
        "num_nodes": num_nodes,
        "num_edges": len(edges),
        "native_ms": round(native_ms, 3),
        "python_ms": round(python_ms, 3),
        "speedup": round(python_ms / max(native_ms, 1e-6), 2),
    }
