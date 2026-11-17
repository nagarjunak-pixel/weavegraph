"""Built-in realistic multi-agent repo scheduling & semantic merge demo."""

from __future__ import annotations

from typing import Any

from weavegraph.indexer import build_file_graph, compute_blast_radius, summarize_graph
from weavegraph.kernels import benchmark_kernels
from weavegraph.merge import verify_three_way_merge
from weavegraph.scheduler import plan_worktrees
from weavegraph.types import TaskItem

DEMO_BASE_TREE: dict[str, str] = {
    "src/core/models.py": (
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Invoice:\n"
        "    invoice_id: str\n"
        "    subtotal_cents: int\n"
        "    currency: str\n"
    ),
    "src/billing/pricing.py": (
        "from src.core.models import Invoice\n\n"
        "def calculate_tax(amount_cents: int, tax_rate: float = 0.05) -> int:\n"
        "    return int(round(amount_cents * tax_rate))\n\n"
        "def legacy_discount(amount_cents: int) -> int:\n"
        "    return int(amount_cents * 0.9)\n"
    ),
    "src/billing/checkout.py": (
        "from src.core.models import Invoice\n"
        "from src.billing.pricing import calculate_tax, legacy_discount\n\n"
        "def finalize_invoice(inv: Invoice) -> int:\n"
        "    base = legacy_discount(inv.subtotal_cents)\n"
        "    return base + calculate_tax(base, tax_rate=0.05)\n"
    ),
    "src/auth/tokens.py": (
        "def issue_token(user_id: str, ttl_sec: int = 3600) -> str:\n"
        "    return f'{user_id}:{ttl_sec}'\n"
    ),
    "src/auth/middleware.py": (
        "from src.auth.tokens import issue_token\n\n"
        "def refresh_session(user_id: str) -> str:\n"
        "    return issue_token(user_id, ttl_sec=1800)\n"
    ),
    "src/analytics/ledger.py": (
        "from src.core.models import Invoice\n"
        "from src.billing.checkout import finalize_invoice\n\n"
        "def build_daily_ledger(inv: Invoice) -> dict[str, int]:\n"
        "    return {'total': finalize_invoice(inv), 'subtotal': inv.subtotal_cents}\n"
    ),
    "ui/InvoiceCard.ts": (
        "import { formatMoney } from './format';\n\n"
        "export interface InvoiceView {\n"
        "  invoiceId: string;\n"
        "  totalCents: number;\n"
        "}\n\n"
        "export function renderInvoice(v: InvoiceView): string {\n"
        "  return `${v.invoiceId}: ${formatMoney(v.totalCents)}`;\n"
        "}\n"
    ),
    "ui/format.ts": (
        "export function formatMoney(cents: number): string {\n"
        "  return `$${(cents / 100).toFixed(2)}`;\n"
        "}\n"
    ),
}


def run_demo_bundle() -> dict[str, Any]:
    """Run indexing, blast radius, 3-agent min-cut planning, merge check, and C++20 benchmark."""
    graph = build_file_graph(DEMO_BASE_TREE)
    summary = summarize_graph(graph)
    impact = compute_blast_radius(graph, ["src/core/models.py"])

    tasks = [
        TaskItem(
            task_id="T1",
            title="Multi-currency VAT pricing refactor",
            target_files=["src/billing/pricing.py", "src/billing/checkout.py"],
            estimated_effort=3,
        ),
        TaskItem(
            task_id="T2",
            title="Token rotation & session hardening",
            target_files=["src/auth/tokens.py", "src/auth/middleware.py"],
            estimated_effort=2,
        ),
        TaskItem(
            task_id="T3",
            title="UI invoice card currency formatting",
            target_files=["ui/InvoiceCard.ts", "ui/format.ts"],
            estimated_effort=2,
        ),
    ]
    plan = plan_worktrees(graph, num_agents=3, tasks=tasks)

    # Disjoint files modified by Agent A and Agent B -> clean text merge, fatal runtime break
    agent_a = dict(DEMO_BASE_TREE)
    agent_a["src/core/models.py"] = (
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Invoice:\n"
        "    invoice_id: str\n"
        "    net_cents: int\n"
        "    currency: str\n"
    )
    agent_a["src/billing/pricing.py"] = (
        "def calculate_tax(amount_cents: int, rate: float, region: str) -> int:\n"
        "    return int(round(amount_cents * rate))\n"
    )

    agent_b = dict(DEMO_BASE_TREE)
    agent_b["src/billing/checkout.py"] = (
        "from src.core.models import Invoice\n"
        "from src.billing.pricing import calculate_tax, legacy_discount\n\n"
        "def finalize_invoice(inv: Invoice) -> int:\n"
        "    base = legacy_discount(inv.subtotal_cents)\n"
        "    return base + calculate_tax(base, tax_rate=0.08)\n"
    )

    merge_report = verify_three_way_merge(
        DEMO_BASE_TREE,
        agent_a,
        agent_b,
        agent_a_name="wt-agent-pricing",
        agent_b_name="wt-agent-checkout",
    )
    bench = benchmark_kernels(num_nodes=400, k_parts=4)

    return {
        "summary": summary.model_dump(mode="json"),
        "impact": impact.model_dump(mode="json"),
        "worktree_plan": plan.model_dump(mode="json"),
        "merge_verification": merge_report.model_dump(mode="json"),
        "kernel_benchmark": bench,
        "edges": [e.model_dump(mode="json") for e in graph.edges],
    }
