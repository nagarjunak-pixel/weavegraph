"""Tests for 3-Way Semantic AST Merge Verifier across all 5 conflict kinds."""

from __future__ import annotations

from weavegraph.merge import verify_three_way_merge
from weavegraph.types import ConflictKind, MergeVerdict


def test_clean_non_conflicting_merge_passes() -> None:
    base = {
        "pricing.py": "def tax(amount: int, rate: float = 0.05) -> int:\n    return int(amount * rate)\n",
        "checkout.py": "from pricing import tax\ndef pay(x: int) -> int:\n    return tax(x, rate=0.05)\n",
    }
    agent_a = {
        "pricing.py": "def tax(amount: int, rate: float = 0.07) -> int:\n    return int(round(amount * rate))\n",
        "checkout.py": base["checkout.py"],
    }
    agent_b = {
        "pricing.py": base["pricing.py"],
        "checkout.py": "from pricing import tax\ndef pay(x: int) -> int:\n    return tax(x + 1, rate=0.05)\n",
    }
    report = verify_three_way_merge(base, agent_a, agent_b)
    assert report.verdict == MergeVerdict.SAFE_TO_MERGE
    assert report.conflicts == []
    assert report.max_severity == 0


def test_detects_arity_kwarg_dangling_and_field_breaks() -> None:
    base = {
        "models.py": "class Invoice:\n    subtotal_cents: int\n    currency: str\n",
        "pricing.py": (
            "def calc_tax(amount: int, tax_rate: float = 0.05) -> int:\n    return int(amount * tax_rate)\n"
            "def old_helper() -> int:\n    return 1\n"
        ),
        "checkout.py": (
            "from models import Invoice\n"
            "from pricing import calc_tax, old_helper\n\n"
            "def run(inv: Invoice) -> int:\n"
            "    return inv.subtotal_cents + calc_tax(100, tax_rate=0.05) + old_helper()\n"
        ),
    }
    agent_a = dict(base)
    agent_a["models.py"] = "class Invoice:\n    net_cents: int\n    currency: str\n"
    agent_a["pricing.py"] = (
        "def calc_tax(amount: int, rate: float, region: str) -> int:\n    return int(amount * rate)\n"
    )

    agent_b = dict(base)
    agent_b["checkout.py"] = (
        "from models import Invoice\n"
        "from pricing import calc_tax, old_helper\n\n"
        "def run(inv: Invoice) -> int:\n"
        "    return inv.subtotal_cents + calc_tax(200, tax_rate=0.08) + old_helper()\n"
    )

    report = verify_three_way_merge(base, agent_a, agent_b)
    assert report.verdict == MergeVerdict.SEMANTIC_CONFLICT_BLOCK
    kinds = {c.kind for c in report.conflicts}
    assert ConflictKind.SIGNATURE_ARITY_BREAK in kinds
    assert ConflictKind.REMOVED_KEYWORD_PARAM in kinds
    assert ConflictKind.DANGLING_IMPORT_OR_SYMBOL in kinds
    assert ConflictKind.CONTRACT_FIELD_REMOVED in kinds


def test_detects_duplicate_definition_clash_and_ts_dangling_import() -> None:
    base = {
        "shared.py": "BASE_CONST = 1\n",
        "ui/api.ts": "export function fetchUser(id: string) { return id; }\n",
        "ui/page.ts": "import { fetchUser } from './api';\nexport function load() { return fetchUser('1'); }\n",
    }
    agent_a = dict(base)
    agent_a["shared.py"] = "BASE_CONST = 1\ndef compute_fee(x: int) -> int:\n    return x\n"
    agent_a["ui/api.ts"] = "export function getUser(id: string) { return id; }\n"

    agent_b = dict(base)
    agent_b["shared.py"] = (
        "BASE_CONST = 1\ndef compute_fee(x: int, y: int) -> int:\n    return x + y\n"
    )
    agent_b["ui/page.ts"] = (
        "import { fetchUser } from './api';\nexport function load() { return fetchUser('2'); }\n"
    )

    report = verify_three_way_merge(base, agent_a, agent_b)
    kinds = {c.kind for c in report.conflicts}
    assert ConflictKind.DUPLICATE_DEFINITION_CLASH in kinds
    assert ConflictKind.DANGLING_IMPORT_OR_SYMBOL in kinds


def test_merge_verifier_edge_cases_and_too_many_args() -> None:
    base = {
        "cfg.py": "class Settings:\n    timeout = 30\n    retries: int\n",
        "lib.py": "def compute(a: int, b: int) -> int:\n    return a + b\ndef same_owner() -> int:\n    return compute(1, 2)\n",
        "consumer.py": "from cfg import Settings\nfrom lib import compute\ndef use() -> int:\n    s: Settings = Settings()\n    return s.timeout + compute(1, 2)\n",
        "broken.py": "def bad(:\n    pass\n",
        "deleted.py": "def gone(): pass\n",
    }
    agent_a = dict(base)
    del agent_a["deleted.py"]
    agent_a["cfg.py"] = "class Settings:\n    retries: int\n"
    agent_a["lib.py"] = (
        "def compute(a: int) -> int:\n    return a\ndef same_owner() -> int:\n    return compute(1)\n"
    )
    agent_a["added_a.py"] = "def brand_new():\n    return 42\n"

    agent_b = dict(base)
    agent_b["consumer.py"] = (
        "from cfg import Settings\nfrom lib import compute\n"
        "def helper():\n    return 1\n"
        "def use() -> int:\n    s: Settings = Settings()\n"
        "    return s.timeout + compute(1, 2, 3) + helper() + unknown_fn(*[])\n"
    )
    agent_b["added_b.py"] = "def b_new():\n    return 7\n"

    report = verify_three_way_merge(base, agent_a, agent_b)
    assert report.verdict == MergeVerdict.SEMANTIC_CONFLICT_BLOCK
    kinds = {c.kind for c in report.conflicts}
    assert ConflictKind.SIGNATURE_ARITY_BREAK in kinds
    assert ConflictKind.CONTRACT_FIELD_REMOVED in kinds
