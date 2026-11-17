"""Unit and Hypothesis property tests verifying C++20 kernels vs Python reference."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from weavegraph.kernels.native import (
    backend_name,
    benchmark_kernels,
    blast_radius,
    mincut_partition,
    schedule_waves,
    tarjan_scc,
)


def test_backend_is_native_and_benchmark_runs() -> None:
    assert "c++20-native" in backend_name()
    bench = benchmark_kernels(num_nodes=120, k_parts=3)
    assert bench["num_nodes"] == 120
    assert float(bench["native_ms"]) >= 0.0
    assert float(bench["python_ms"]) >= 0.0


def test_empty_and_edge_cases() -> None:
    assert tarjan_scc(0, []) == []
    assert blast_radius(0, [], [0]) == []
    assert mincut_partition(0, [], [], 3) == ([], 0)
    assert mincut_partition(4, [(0, 1, 5)], [1, 1, 1, 1], 1) == ([0, 0, 0, 0], 0)
    assert schedule_waves(0, []) == []
    with pytest.raises(ValueError, match="k_parts"):
        mincut_partition(3, [], [1, 1, 1], 0)


def test_tarjan_scc_and_waves_on_cycles() -> None:
    # 0 -> 1 -> 0 (cycle), 2 -> 0, 3 -> 2
    edges = [(0, 1), (1, 0), (2, 0), (3, 2)]
    c_nat = tarjan_scc(4, edges, use_native=True)
    c_py = tarjan_scc(4, edges, use_native=False)
    assert c_nat[0] == c_nat[1]
    assert c_py[0] == c_py[1]
    assert len(set(c_nat)) == 3
    assert len(set(c_py)) == 3

    w_nat = schedule_waves(4, edges, use_native=True)
    w_py = schedule_waves(4, edges, use_native=False)
    assert w_nat == w_py == [0, 0, 1, 2]


def test_blast_radius_native_python_parity() -> None:
    # 3 -> 2 -> 0, 1 -> 0, 4 isolated
    edges = [(3, 2), (2, 0), (1, 0)]
    d_nat = blast_radius(5, edges, [0], use_native=True)
    d_py = blast_radius(5, edges, [0], use_native=False)
    assert d_nat == d_py == [0, 1, 1, 2, -1]


@settings(max_examples=40, deadline=None)
@given(
    n=st.integers(min_value=2, max_value=18),
    k=st.integers(min_value=1, max_value=4),
    data=st.data(),
)
def test_hypothesis_graph_kernels_parity_and_invariants(
    n: int, k: int, data: st.DataObject
) -> None:
    raw_edges = data.draw(
        st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=n - 1),
                st.integers(min_value=0, max_value=n - 1),
                st.integers(min_value=1, max_value=15),
            ),
            max_size=30,
        )
    )
    dir_edges = [(u, v) for u, v, _ in raw_edges]

    # 1. Tarjan SCC parity: same equivalence classes
    c_nat = tarjan_scc(n, dir_edges, use_native=True)
    c_py = tarjan_scc(n, dir_edges, use_native=False)
    for i in range(n):
        for j in range(i + 1, n):
            assert (c_nat[i] == c_nat[j]) == (c_py[i] == c_py[j])

    # 2. Blast radius bit-exact shortest-hop parity
    seeds = data.draw(st.lists(st.integers(min_value=0, max_value=n - 1), max_size=3))
    d_nat = blast_radius(n, dir_edges, seeds, use_native=True)
    d_py = blast_radius(n, dir_edges, seeds, use_native=False)
    assert d_nat == d_py

    # 3. Topological wave invariance: cross-SCC dependency implies strictly smaller dependency wave
    w_nat = schedule_waves(n, dir_edges, use_native=True)
    w_py = schedule_waves(n, dir_edges, use_native=False)
    assert w_nat == w_py
    for u, v in dir_edges:
        if c_nat[u] != c_nat[v]:
            assert w_nat[u] > w_nat[v]

    # 4. Min-cut exact cut-weight accounting & partition bounds
    nw = [1] * n
    p_nat, cut_nat = mincut_partition(n, raw_edges, nw, k, use_native=True)
    p_py, cut_py = mincut_partition(n, raw_edges, nw, k, use_native=False)
    eff_k = min(k, n)
    assert all(0 <= p < eff_k for p in p_nat)
    assert all(0 <= p < eff_k for p in p_py)
    expected_cut_nat = sum(w for u, v, w in raw_edges if u != v and p_nat[u] != p_nat[v])
    expected_cut_py = sum(w for u, v, w in raw_edges if u != v and p_py[u] != p_py[v])
    assert cut_nat == expected_cut_nat
    assert cut_py == expected_cut_py
