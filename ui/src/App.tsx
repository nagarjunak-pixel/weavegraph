import { useState } from "react";

interface Lane {
  agent_id: string;
  branch_name: string;
  worktree_command: string;
  files: string[];
  task_ids: string[];
  execution_wave: number;
  internal_coupling: number;
}

interface SemanticConflict {
  kind: string;
  severity: number;
  symbol_name: string;
  producer_agent: string;
  producer_file: string;
  consumer_agent: string;
  consumer_file: string;
  consumer_line: number;
  message: string;
  suggested_fix: string;
}

interface ImpactNode {
  file_path: string;
  hop_distance: number;
  wave: number;
}

interface DemoBundle {
  summary: {
    total_files: number;
    total_symbols: number;
    total_edges: number;
    wave_count: number;
    backend: string;
  };
  impact: {
    seeds: string[];
    affected: ImpactNode[];
    affected_ratio: number;
  };
  worktree_plan: {
    agent_count: number;
    mincut_cross_coupling: number;
    naive_cross_coupling: number;
    coupling_reduction_pct: number;
    shared_contract_locks: string[];
    lanes: Lane[];
  };
  merge_verification: {
    verdict: string;
    max_severity: number;
    summary: string;
    conflicts: SemanticConflict[];
  };
  kernel_benchmark: {
    native_ms: number;
    python_ms: number;
    speedup: number;
    num_nodes: number;
  };
}

const DEFAULT_DEMO: DemoBundle = {
  summary: {
    total_files: 8,
    total_symbols: 11,
    total_edges: 15,
    wave_count: 3,
    backend: "c++20-native (clang++ -O3)",
  },
  impact: {
    seeds: ["src/core/models.py"],
    affected_ratio: 0.5,
    affected: [
      { file_path: "src/core/models.py", hop_distance: 0, wave: 0 },
      { file_path: "src/billing/pricing.py", hop_distance: 1, wave: 1 },
      { file_path: "src/billing/checkout.py", hop_distance: 1, wave: 2 },
      { file_path: "src/analytics/ledger.py", hop_distance: 1, wave: 3 },
    ],
  },
  worktree_plan: {
    agent_count: 3,
    mincut_cross_coupling: 10,
    naive_cross_coupling: 98,
    coupling_reduction_pct: 89.8,
    shared_contract_locks: ["src/core/models.py"],
    lanes: [
      {
        agent_id: "agent-1",
        branch_name: "agent/lane-1",
        worktree_command: "git worktree add ../wt-agent-1 -b agent/lane-1",
        files: ["src/auth/middleware.py", "src/auth/tokens.py"],
        task_ids: ["T2"],
        execution_wave: 0,
        internal_coupling: 40,
      },
      {
        agent_id: "agent-2",
        branch_name: "agent/lane-2",
        worktree_command: "git worktree add ../wt-agent-2 -b agent/lane-2",
        files: ["ui/InvoiceCard.ts", "ui/format.ts"],
        task_ids: ["T3"],
        execution_wave: 0,
        internal_coupling: 40,
      },
      {
        agent_id: "agent-3",
        branch_name: "agent/lane-3",
        worktree_command: "git worktree add ../wt-agent-3 -b agent/lane-3",
        files: [
          "src/analytics/ledger.py",
          "src/billing/checkout.py",
          "src/billing/pricing.py",
          "src/core/models.py",
        ],
        task_ids: ["T1"],
        execution_wave: 1,
        internal_coupling: 87,
      },
    ],
  },
  merge_verification: {
    verdict: "SEMANTIC_CONFLICT_BLOCK",
    max_severity: 9,
    summary: "BLOCKED: Detected 4 silent semantic AST conflict(s) (max severity 9/10).",
    conflicts: [
      {
        kind: "SIGNATURE_ARITY_BREAK",
        severity: 9,
        symbol_name: "calculate_tax",
        producer_agent: "wt-agent-pricing",
        producer_file: "src/billing/pricing.py",
        consumer_agent: "wt-agent-checkout",
        consumer_file: "src/billing/checkout.py",
        consumer_line: 6,
        message: "Call `calculate_tax` supplies 1 positional arg, incompatible with required 3..3.",
        suggested_fix: "Align `calculate_tax` signature or restore defaults.",
      },
      {
        kind: "DANGLING_IMPORT_OR_SYMBOL",
        severity: 9,
        symbol_name: "legacy_discount",
        producer_agent: "wt-agent-pricing",
        producer_file: "src/billing/pricing.py",
        consumer_agent: "wt-agent-checkout",
        consumer_file: "src/billing/checkout.py",
        consumer_line: 2,
        message: "`wt-agent-pricing` removed `legacy_discount`, breaking import in `checkout.py:2`.",
        suggested_fix: "Restore alias or migrate caller.",
      },
      {
        kind: "CONTRACT_FIELD_REMOVED",
        severity: 8,
        symbol_name: "Invoice.subtotal_cents",
        producer_agent: "wt-agent-pricing",
        producer_file: "src/core/models.py",
        consumer_agent: "wt-agent-checkout",
        consumer_file: "src/billing/checkout.py",
        consumer_line: 5,
        message: "`wt-agent-pricing` removed `Invoice.subtotal_cents` read by `checkout.py:5`.",
        suggested_fix: "Add compatibility `@property subtotal_cents`.",
      },
    ],
  },
  kernel_benchmark: {
    native_ms: 0.42,
    python_ms: 8.6,
    speedup: 20.5,
    num_nodes: 400,
  },
};

type TabKey = "lanes" | "merge" | "impact";

export default function App() {
  const [data, setData] = useState<DemoBundle>(DEFAULT_DEMO);
  const [tab, setTab] = useState<TabKey>("lanes");
  const [status, setStatus] = useState<string>("Loaded built-in multi-agent verification snapshot");

  async function refreshFromApi() {
    try {
      const resp = await fetch("http://127.0.0.1:8150/api/v1/demo");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = (await resp.json()) as DemoBundle;
      setData(json);
      setStatus(`Synced live from FastAPI (${json.summary.backend})`);
    } catch {
      setStatus("Offline snapshot active (start `weavegraph serve --port 8150` to sync live)");
    }
  }

  return (
    <div className="container">
      <header>
        <div>
          <h1>WeaveGraph Workbench</h1>
          <div className="sub">
            Semantic Code-Graph Worktree Scheduler &amp; 3-Way AST Merge Verifier · {status}
          </div>
        </div>
        <button className="btn" onClick={() => void refreshFromApi()}>
          Sync Local API (:8150)
        </button>
      </header>

      <section className="kpis">
        <div className="kpi">
          <div className="lbl">Min-Cut Coupling Reduction</div>
          <div className="val">{data.worktree_plan.coupling_reduction_pct}%</div>
          <div className="sub">
            cut {data.worktree_plan.mincut_cross_coupling} vs naive {data.worktree_plan.naive_cross_coupling}
          </div>
        </div>
        <div className="kpi">
          <div className="lbl">3-Way Semantic Gate</div>
          <div className="val" style={{ color: data.merge_verification.conflicts.length ? "var(--red)" : "var(--green)" }}>
            {data.merge_verification.conflicts.length} Conflicts
          </div>
          <div className="sub">{data.merge_verification.verdict}</div>
        </div>
        <div className="kpi">
          <div className="lbl">Graph Topology</div>
          <div className="val">{data.summary.total_files} Files / {data.summary.wave_count} Waves</div>
          <div className="sub">{data.summary.total_edges} typed AST dependency edges</div>
        </div>
        <div className="kpi">
          <div className="lbl">C++20 Kernel Speedup</div>
          <div className="val">{data.kernel_benchmark.speedup}x</div>
          <div className="sub">{data.kernel_benchmark.native_ms}ms C++20 vs {data.kernel_benchmark.python_ms}ms Py</div>
        </div>
      </section>

      <div className="tabs">
        <button className={`tab ${tab === "lanes" ? "active" : ""}`} onClick={() => setTab("lanes")}>
          Min-Cut Worktree Lanes ({data.worktree_plan.lanes.length})
        </button>
        <button className={`tab ${tab === "merge" ? "active" : ""}`} onClick={() => setTab("merge")}>
          3-Way Semantic Merge Gate ({data.merge_verification.conflicts.length})
        </button>
        <button className={`tab ${tab === "impact" ? "active" : ""}`} onClick={() => setTab("impact")}>
          Tarjan Blast Radius ({data.impact.affected.length})
        </button>
      </div>

      {tab === "lanes" && (
        <div className="grid">
          {data.worktree_plan.lanes.map((lane) => (
            <div className="card" key={lane.agent_id}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{lane.agent_id}</strong>
                <span className="badge">Wave {lane.execution_wave} · Tasks: {lane.task_ids.join(", ") || "auto"}</span>
              </div>
              <p className="sub"><code>{lane.worktree_command}</code></p>
              <ul>
                {lane.files.map((f) => (
                  <li key={f}><code>{f}</code></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {tab === "merge" && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Severity</th>
                <th>Symbol</th>
                <th>Producer &rarr; Consumer</th>
                <th>Diagnostic</th>
              </tr>
            </thead>
            <tbody>
              {data.merge_verification.conflicts.map((c, idx) => (
                <tr key={idx}>
                  <td><span className="badge danger">{c.kind}</span></td>
                  <td>{c.severity}/10</td>
                  <td><code>{c.symbol_name}</code></td>
                  <td><code>{c.producer_file}</code> &rarr; <code>{c.consumer_file}:{c.consumer_line}</code></td>
                  <td>{c.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "impact" && (
        <div className="card">
          <div className="sub" style={{ marginBottom: 10 }}>
            Seed files: {data.impact.seeds.map((s) => <code key={s}>{s} </code>)}
          </div>
          <table>
            <thead>
              <tr><th>File</th><th>Hop Distance</th><th>Execution Wave</th></tr>
            </thead>
            <tbody>
              {data.impact.affected.map((node) => (
                <tr key={node.file_path}>
                  <td><code>{node.file_path}</code></td>
                  <td>{node.hop_distance}</td>
                  <td>Wave {node.wave}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
