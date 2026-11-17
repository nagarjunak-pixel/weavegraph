"""Strict Pydantic v2 domain models for WeaveGraph."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SymbolKind(StrEnum):
    """Kind of extracted code artifact or symbol."""

    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    INTERFACE = "interface"
    CONSTANT = "constant"


class EdgeKind(StrEnum):
    """Typed dependency edge between code nodes."""

    CALL = "call"
    INHERIT = "inherit"
    FIELD_ACCESS = "field_access"
    IMPORT = "import"


class SymbolNode(BaseModel):
    """Symbol metadata extracted from Python or TypeScript/JS files."""

    node_id: str
    file_path: str
    name: str
    kind: SymbolKind
    line: int = Field(ge=1)
    min_args: int = Field(default=0, ge=0)
    max_args: int | None = None
    kwarg_names: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)


class DependencyEdge(BaseModel):
    """Weighted directed dependency edge (source depends on target)."""

    source: str
    target: str
    kind: EdgeKind
    weight: int = Field(ge=1)


class FileGraphData(BaseModel):
    """File-level dependency graph extracted from a codebase."""

    files: list[str]
    edges: list[DependencyEdge]
    symbols: list[SymbolNode]
    file_weights: dict[str, int]
    scc_groups: list[list[str]]
    waves: dict[str, int]
    backend: str


class RepoGraphSummary(BaseModel):
    """Compact summary of an indexed repository dependency graph."""

    total_files: int
    total_symbols: int
    total_edges: int
    scc_count: int
    cycle_groups: list[list[str]]
    wave_count: int
    max_wave_files: list[str]
    backend: str
    file_waves: dict[str, int]


class ImpactNode(BaseModel):
    """Affected file/node with shortest reverse-dependency hop distance."""

    file_path: str
    hop_distance: int = Field(ge=0)
    wave: int = Field(ge=0)


class ImpactReport(BaseModel):
    """Blast-radius impact report from one or more changed seed files."""

    seeds: list[str]
    affected: list[ImpactNode]
    affected_ratio: float = Field(ge=0.0, le=1.0)
    backend: str


class TaskItem(BaseModel):
    """Coding task specification mapped onto repository files."""

    task_id: str
    title: str
    target_files: list[str] = Field(default_factory=list)
    estimated_effort: int = Field(default=1, ge=1)


class AgentLane(BaseModel):
    """Isolated agent lane with assigned files, tasks, wave, and git worktree command."""

    agent_id: str
    branch_name: str
    worktree_command: str
    files: list[str]
    task_ids: list[str]
    execution_wave: int = Field(ge=0)
    internal_coupling: int = Field(ge=0)


class WorktreePlan(BaseModel):
    """Min-cut worktree partitioning & wave schedule for K coding agents."""

    agent_count: int = Field(ge=1)
    mincut_cross_coupling: int = Field(ge=0)
    naive_cross_coupling: int = Field(ge=0)
    coupling_reduction_pct: float
    shared_contract_locks: list[str]
    lanes: list[AgentLane]
    backend: str


class ConflictKind(StrEnum):
    """Semantic conflict categories invisible to text-based git merge."""

    SIGNATURE_ARITY_BREAK = "SIGNATURE_ARITY_BREAK"
    REMOVED_KEYWORD_PARAM = "REMOVED_KEYWORD_PARAM"
    DANGLING_IMPORT_OR_SYMBOL = "DANGLING_IMPORT_OR_SYMBOL"
    CONTRACT_FIELD_REMOVED = "CONTRACT_FIELD_REMOVED"
    DUPLICATE_DEFINITION_CLASH = "DUPLICATE_DEFINITION_CLASH"


class SemanticConflict(BaseModel):
    """Detailed cross-agent AST contract violation found in 3-way merge verification."""

    kind: ConflictKind
    severity: int = Field(ge=1, le=10)
    symbol_name: str
    producer_agent: str
    producer_file: str
    consumer_agent: str
    consumer_file: str
    consumer_line: int = Field(ge=1)
    message: str
    suggested_fix: str


class MergeVerdict(StrEnum):
    """Overall semantic merge gate verdict."""

    SAFE_TO_MERGE = "SAFE_TO_MERGE"
    SEMANTIC_CONFLICT_BLOCK = "SEMANTIC_CONFLICT_BLOCK"


class MergeVerificationReport(BaseModel):
    """3-way semantic AST verification report across Base, Agent A, and Agent B trees."""

    verdict: MergeVerdict
    files_compared: int = Field(ge=0)
    max_severity: int = Field(ge=0, le=10)
    conflicts: list[SemanticConflict]
    summary: str
