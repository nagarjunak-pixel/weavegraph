"""FastAPI service endpoints for WeaveGraph."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from weavegraph import __version__
from weavegraph.demo import run_demo_bundle
from weavegraph.indexer import build_file_graph, compute_blast_radius, summarize_graph
from weavegraph.kernels import backend_name
from weavegraph.merge import verify_three_way_merge
from weavegraph.scheduler import plan_worktrees
from weavegraph.types import (
    ImpactReport,
    MergeVerificationReport,
    RepoGraphSummary,
    TaskItem,
    WorktreePlan,
)


class IndexRequest(BaseModel):
    files: dict[str, str]


class BlastRadiusRequest(BaseModel):
    files: dict[str, str]
    seed_files: list[str]


class PlanRequest(BaseModel):
    files: dict[str, str]
    num_agents: int = Field(default=3, ge=1, le=32)
    tasks: list[TaskItem] = Field(default_factory=list)


class VerifyMergeRequest(BaseModel):
    base_tree: dict[str, str]
    agent_a_tree: dict[str, str]
    agent_b_tree: dict[str, str]
    agent_a_name: str = "agent-a"
    agent_b_name: str = "agent-b"


def create_app() -> FastAPI:
    """Create configured FastAPI application."""
    application = FastAPI(
        title="WeaveGraph API",
        version=__version__,
        description="Semantic Code-Graph Worktree Scheduler & 3-Way AST Merge Verifier",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "backend": backend_name()}

    @application.get("/api/v1/demo")
    def demo() -> dict[str, Any]:
        return run_demo_bundle()

    @application.post("/api/v1/index", response_model=RepoGraphSummary)
    def index_files(req: IndexRequest) -> RepoGraphSummary:
        graph = build_file_graph(req.files)
        return summarize_graph(graph)

    @application.post("/api/v1/blast-radius", response_model=ImpactReport)
    def blast_radius_endpoint(req: BlastRadiusRequest) -> ImpactReport:
        graph = build_file_graph(req.files)
        return compute_blast_radius(graph, req.seed_files)

    @application.post("/api/v1/plan", response_model=WorktreePlan)
    def plan_endpoint(req: PlanRequest) -> WorktreePlan:
        graph = build_file_graph(req.files)
        return plan_worktrees(graph, num_agents=req.num_agents, tasks=req.tasks)

    @application.post("/api/v1/verify-merge", response_model=MergeVerificationReport)
    def verify_merge_endpoint(req: VerifyMergeRequest) -> MergeVerificationReport:
        return verify_three_way_merge(
            req.base_tree,
            req.agent_a_tree,
            req.agent_b_tree,
            agent_a_name=req.agent_a_name,
            agent_b_name=req.agent_b_name,
        )

    return application


app = create_app()
