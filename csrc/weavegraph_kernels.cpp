#include <algorithm>
#include <cmath>
#include <cstdint>
#include <queue>
#include <vector>

extern "C" {

int32_t weavegraph_tarjan_scc(
    int32_t num_nodes,
    const int32_t* row_ptr,
    const int32_t* col_idx,
    int32_t* out_comp_ids
) {
    if (num_nodes < 0 || !out_comp_ids || (num_nodes > 0 && (!row_ptr || !col_idx))) {
        return -1;
    }
    if (num_nodes == 0) {
        return 0;
    }

    std::vector<int32_t> index(num_nodes, -1);
    std::vector<int32_t> lowlink(num_nodes, 0);
    std::vector<uint8_t> on_stack(num_nodes, 0);
    std::vector<int32_t> tarjan_stack;
    tarjan_stack.reserve(num_nodes);

    struct Frame {
        int32_t u;
        int32_t edge_pos;
    };
    std::vector<Frame> call_stack;
    call_stack.reserve(num_nodes);

    int32_t current_index = 0;
    int32_t comp_count = 0;

    for (int32_t start = 0; start < num_nodes; ++start) {
        if (index[start] != -1) {
            continue;
        }
        index[start] = current_index;
        lowlink[start] = current_index;
        ++current_index;
        on_stack[start] = 1;
        tarjan_stack.push_back(start);
        call_stack.push_back({start, row_ptr[start]});

        while (!call_stack.empty()) {
            Frame& top = call_stack.back();
            int32_t u = top.u;
            int32_t end_pos = row_ptr[u + 1];

            if (top.edge_pos < end_pos) {
                int32_t v = col_idx[top.edge_pos];
                ++top.edge_pos;
                if (v < 0 || v >= num_nodes) {
                    continue;
                }
                if (index[v] == -1) {
                    index[v] = current_index;
                    lowlink[v] = current_index;
                    ++current_index;
                    on_stack[v] = 1;
                    tarjan_stack.push_back(v);
                    call_stack.push_back({v, row_ptr[v]});
                } else if (on_stack[v]) {
                    if (index[v] < lowlink[u]) {
                        lowlink[u] = index[v];
                    }
                }
            } else {
                if (lowlink[u] == index[u]) {
                    while (true) {
                        int32_t w = tarjan_stack.back();
                        tarjan_stack.pop_back();
                        on_stack[w] = 0;
                        out_comp_ids[w] = comp_count;
                        if (w == u) {
                            break;
                        }
                    }
                    ++comp_count;
                }
                call_stack.pop_back();
                if (!call_stack.empty()) {
                    int32_t parent = call_stack.back().u;
                    if (lowlink[u] < lowlink[parent]) {
                        lowlink[parent] = lowlink[u];
                    }
                }
            }
        }
    }

    return comp_count;
}

int32_t weavegraph_blast_radius(
    int32_t num_nodes,
    const int32_t* rev_row_ptr,
    const int32_t* rev_col_idx,
    const int32_t* seed_nodes,
    int32_t num_seeds,
    int32_t* out_distances
) {
    if (num_nodes < 0 || num_seeds < 0 || !out_distances) {
        return -1;
    }
    if (num_nodes == 0) {
        return 0;
    }
    if (!rev_row_ptr || !rev_col_idx) {
        return -1;
    }

    std::fill(out_distances, out_distances + num_nodes, -1);
    std::queue<int32_t> q;

    for (int32_t i = 0; i < num_seeds; ++i) {
        if (!seed_nodes) {
            return -1;
        }
        int32_t s = seed_nodes[i];
        if (s >= 0 && s < num_nodes && out_distances[s] == -1) {
            out_distances[s] = 0;
            q.push(s);
        }
    }

    int32_t affected = 0;
    while (!q.empty()) {
        int32_t u = q.front();
        q.pop();
        ++affected;
        int32_t d_next = out_distances[u] + 1;
        int32_t begin = rev_row_ptr[u];
        int32_t end = rev_row_ptr[u + 1];
        for (int32_t e = begin; e < end; ++e) {
            int32_t v = rev_col_idx[e];
            if (v >= 0 && v < num_nodes && out_distances[v] == -1) {
                out_distances[v] = d_next;
                q.push(v);
            }
        }
    }

    return affected;
}

int32_t weavegraph_mincut_partition(
    int32_t num_nodes,
    const int32_t* u_idx,
    const int32_t* v_idx,
    const int32_t* edge_weights,
    int32_t num_edges,
    const int32_t* node_weights,
    int32_t k_parts,
    int32_t* out_part_ids,
    int64_t* out_cut_weight
) {
    if (num_nodes < 0 || num_edges < 0 || k_parts <= 0 || !out_part_ids || !out_cut_weight) {
        return -1;
    }
    if (num_nodes == 0) {
        *out_cut_weight = 0;
        return 0;
    }
    if (k_parts == 1 || num_nodes == 1) {
        std::fill(out_part_ids, out_part_ids + num_nodes, 0);
        *out_cut_weight = 0;
        return 0;
    }

    int32_t eff_k = std::min(k_parts, num_nodes);

    std::vector<int64_t> n_weight(num_nodes, 1);
    int64_t total_weight = 0;
    int64_t max_single_w = 1;
    for (int32_t i = 0; i < num_nodes; ++i) {
        int64_t w = (node_weights && node_weights[i] > 0) ? node_weights[i] : 1;
        n_weight[i] = w;
        total_weight += w;
        if (w > max_single_w) {
            max_single_w = w;
        }
    }

    struct Adj {
        int32_t to;
        int64_t w;
    };
    std::vector<std::vector<Adj>> adj(num_nodes);
    std::vector<int64_t> deg(num_nodes, 0);
    for (int32_t e = 0; e < num_edges; ++e) {
        if (!u_idx || !v_idx || !edge_weights) {
            return -1;
        }
        int32_t u = u_idx[e];
        int32_t v = v_idx[e];
        int64_t w = edge_weights[e] > 0 ? edge_weights[e] : 0;
        if (u < 0 || u >= num_nodes || v < 0 || v >= num_nodes || u == v || w == 0) {
            continue;
        }
        adj[u].push_back({v, w});
        adj[v].push_back({u, w});
        deg[u] += w;
        deg[v] += w;
    }

    // Order nodes by weighted degree descending, tie-broken by node weight descending, then index
    std::vector<int32_t> order(num_nodes);
    for (int32_t i = 0; i < num_nodes; ++i) {
        order[i] = i;
    }
    std::sort(order.begin(), order.end(), [&](int32_t a, int32_t b) {
        if (deg[a] != deg[b]) return deg[a] > deg[b];
        if (n_weight[a] != n_weight[b]) return n_weight[a] > n_weight[b];
        return a < b;
    });

    std::vector<int32_t> part(num_nodes, -1);
    std::vector<int64_t> part_weight(eff_k, 0);
    std::vector<int32_t> part_count(eff_k, 0);

    // Seed eff_k anchor nodes spaced apart in sorted order
    for (int32_t p = 0; p < eff_k; ++p) {
        int32_t seed = order[p];
        part[seed] = p;
        part_weight[p] += n_weight[seed];
        part_count[p] += 1;
    }

    int64_t ideal_w = (total_weight + eff_k - 1) / eff_k;
    int64_t max_cap = std::max(ideal_w + max_single_w, static_cast<int64_t>(std::ceil(ideal_w * 1.25)));
    int32_t max_cnt = std::max(1, static_cast<int32_t>(std::ceil((static_cast<double>(num_nodes) / eff_k) * 1.35)));

    // Greedy affinity assignment for remaining nodes
    std::vector<int64_t> aff(eff_k, 0);
    for (int32_t idx = eff_k; idx < num_nodes; ++idx) {
        int32_t u = order[idx];
        std::fill(aff.begin(), aff.end(), 0);
        for (const Adj& edge : adj[u]) {
            int32_t p = part[edge.to];
            if (p >= 0) {
                aff[p] += edge.w;
            }
        }
        int32_t best_p = -1;
        double best_score = -1e18;
        for (int32_t p = 0; p < eff_k; ++p) {
            if (part_weight[p] + n_weight[u] > max_cap && part_count[p] > 0) {
                continue;
            }
            if (part_count[p] + 1 > max_cnt && part_count[p] > 0) {
                continue;
            }
            // Prefer high internal affinity and lighter existing partition
            double sc = static_cast<double>(aff[p]) - 0.01 * static_cast<double>(part_weight[p]);
            if (best_p == -1 || sc > best_score) {
                best_score = sc;
                best_p = p;
            }
        }
        if (best_p == -1) {
            // Fallback: pick lightest partition
            best_p = 0;
            for (int32_t p = 1; p < eff_k; ++p) {
                if (part_weight[p] < part_weight[best_p]) {
                    best_p = p;
                }
            }
        }
        part[u] = best_p;
        part_weight[best_p] += n_weight[u];
        part_count[best_p] += 1;
    }

    // Fiduccia-Mattheyses / Kernighan-Lin single-node move + pairwise swap refinement
    for (int iter = 0; iter < 16; ++iter) {
        bool improved = false;

        // Pass 1: single-node moves
        for (int32_t u = 0; u < num_nodes; ++u) {
            int32_t cur_p = part[u];
            if (part_count[cur_p] <= 1) {
                continue;
            }
            std::fill(aff.begin(), aff.end(), 0);
            for (const Adj& edge : adj[u]) {
                aff[part[edge.to]] += edge.w;
            }
            int64_t cur_aff = aff[cur_p];
            int32_t best_p = cur_p;
            int64_t best_gain = 0;

            for (int32_t p = 0; p < eff_k; ++p) {
                if (p == cur_p) continue;
                if (part_weight[p] + n_weight[u] > max_cap) continue;
                if (part_count[p] + 1 > max_cnt) continue;
                int64_t gain = aff[p] - cur_aff;
                if (gain > best_gain) {
                    best_gain = gain;
                    best_p = p;
                }
            }
            if (best_p != cur_p) {
                part[u] = best_p;
                part_weight[cur_p] -= n_weight[u];
                part_count[cur_p] -= 1;
                part_weight[best_p] += n_weight[u];
                part_count[best_p] += 1;
                improved = true;
            }
        }

        // Pass 2: bounded pairwise swaps when num_nodes <= 512
        if (num_nodes <= 512) {
            for (int32_t u = 0; u < num_nodes; ++u) {
                int32_t pu = part[u];
                for (int32_t v = u + 1; v < num_nodes; ++v) {
                    int32_t pv = part[v];
                    if (pu == pv) continue;
                    if (part_weight[pu] - n_weight[u] + n_weight[v] > max_cap) continue;
                    if (part_weight[pv] - n_weight[v] + n_weight[u] > max_cap) continue;

                    int64_t aff_u_pu = 0, aff_u_pv = 0, uv_w = 0;
                    for (const Adj& e : adj[u]) {
                        if (e.to == v) {
                            uv_w += e.w;
                        } else if (part[e.to] == pu) {
                            aff_u_pu += e.w;
                        } else if (part[e.to] == pv) {
                            aff_u_pv += e.w;
                        }
                    }
                    int64_t aff_v_pv = 0, aff_v_pu = 0;
                    for (const Adj& e : adj[v]) {
                        if (e.to == u) continue;
                        if (part[e.to] == pv) {
                            aff_v_pv += e.w;
                        } else if (part[e.to] == pu) {
                            aff_v_pu += e.w;
                        }
                    }
                    // Gain of swapping u (pu->pv) and v (pv->pu); internal uv remains cut
                    int64_t gain = (aff_u_pv - aff_u_pu) + (aff_v_pu - aff_v_pv);
                    if (gain > 0) {
                        part[u] = pv;
                        part[v] = pu;
                        part_weight[pu] += (n_weight[v] - n_weight[u]);
                        part_weight[pv] += (n_weight[u] - n_weight[v]);
                        pu = pv;
                        improved = true;
                    }
                }
            }
        }

        if (!improved) {
            break;
        }
    }

    int64_t cut_w = 0;
    for (int32_t e = 0; e < num_edges; ++e) {
        int32_t u = u_idx[e];
        int32_t v = v_idx[e];
        int64_t w = edge_weights[e] > 0 ? edge_weights[e] : 0;
        if (u >= 0 && u < num_nodes && v >= 0 && v < num_nodes && part[u] != part[v]) {
            cut_w += w;
        }
    }

    for (int32_t i = 0; i < num_nodes; ++i) {
        out_part_ids[i] = part[i];
    }
    *out_cut_weight = cut_w;
    return 0;
}

int32_t weavegraph_schedule_waves(
    int32_t num_nodes,
    const int32_t* row_ptr,
    const int32_t* col_idx,
    int32_t* out_wave_ids
) {
    if (num_nodes < 0 || !out_wave_ids || (num_nodes > 0 && (!row_ptr || !col_idx))) {
        return -1;
    }
    if (num_nodes == 0) {
        return 0;
    }

    std::vector<int32_t> comp(num_nodes, -1);
    int32_t num_scc = weavegraph_tarjan_scc(num_nodes, row_ptr, col_idx, comp.data());
    if (num_scc <= 0) {
        return -1;
    }

    std::vector<std::vector<int32_t>> scc_nodes(num_scc);
    for (int32_t u = 0; u < num_nodes; ++u) {
        scc_nodes[comp[u]].push_back(u);
    }

    // Tarjan assigns sink SCCs lower IDs, so iterating c=0..num_scc-1 evaluates dependencies before dependents
    std::vector<int32_t> scc_wave(num_scc, 0);
    int32_t max_wave = 0;
    for (int32_t c = 0; c < num_scc; ++c) {
        int32_t w = 0;
        for (int32_t u : scc_nodes[c]) {
            int32_t begin = row_ptr[u];
            int32_t end = row_ptr[u + 1];
            for (int32_t e = begin; e < end; ++e) {
                int32_t v = col_idx[e];
                if (v >= 0 && v < num_nodes) {
                    int32_t cv = comp[v];
                    if (cv != c && scc_wave[cv] + 1 > w) {
                        w = scc_wave[cv] + 1;
                    }
                }
            }
        }
        scc_wave[c] = w;
        if (w > max_wave) {
            max_wave = w;
        }
    }

    for (int32_t u = 0; u < num_nodes; ++u) {
        out_wave_ids[u] = scc_wave[comp[u]];
    }
    return max_wave + 1;
}

}  // extern "C"
