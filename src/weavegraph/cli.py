"""Typer CLI entrypoint for WeaveGraph."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from weavegraph.demo import run_demo_bundle
from weavegraph.indexer import (
    build_file_graph,
    compute_blast_radius,
    load_repo_files,
    summarize_graph,
)
from weavegraph.mcp import serve_stdio
from weavegraph.merge import verify_three_way_merge
from weavegraph.scheduler import plan_worktrees
from weavegraph.types import MergeVerdict

app = typer.Typer(
    name="weavegraph",
    help="Semantic Code-Graph Worktree Scheduler & 3-Way AST Merge Verifier",
    no_args_is_help=True,
)
console = Console()


@app.command()
def scan(
    repo_path: Path = typer.Argument(Path("."), help="Path to repository"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary"),
) -> None:
    """Index repository dependency graph, Tarjan SCCs, and execution waves."""
    files = load_repo_files(repo_path)
    summary = summarize_graph(build_file_graph(files))
    if json_output:
        typer.echo(summary.model_dump_json(indent=2))
        return
    console.print(
        f"[bold cyan]WeaveGraph Scan[/bold cyan] ({summary.backend}) · "
        f"files={summary.total_files} symbols={summary.total_symbols} "
        f"edges={summary.total_edges} sccs={summary.scc_count} waves={summary.wave_count}"
    )


@app.command()
def impact(
    repo_path: Path = typer.Argument(..., help="Path to repository"),
    seed: list[str] = typer.Option(..., "--seed", help="Changed file(s) relative to repo_path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON impact report"),
) -> None:
    """Compute shortest-hop reverse-dependency blast radius for modified files."""
    files = load_repo_files(repo_path)
    graph = build_file_graph(files)
    report = compute_blast_radius(graph, seed)
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    table = Table(title=f"Blast Radius ({len(report.affected)} impacted)")
    table.add_column("Hop")
    table.add_column("Wave")
    table.add_column("File")
    for item in report.affected:
        table.add_row(str(item.hop_distance), str(item.wave), item.file_path)
    console.print(table)


@app.command()
def plan(
    repo_path: Path = typer.Argument(..., help="Path to repository"),
    agents: int = typer.Option(3, "--agents", "-k", min=1, help="Number of parallel coding agents"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON worktree plan"),
) -> None:
    """Partition repository files across K coding agents via min-cut optimization."""
    files = load_repo_files(repo_path)
    graph = build_file_graph(files)
    wt_plan = plan_worktrees(graph, num_agents=agents)
    if json_output:
        typer.echo(wt_plan.model_dump_json(indent=2))
        return
    console.print(
        f"[bold green]Min-Cut Partition ({wt_plan.backend})[/bold green]: "
        f"cut={wt_plan.mincut_cross_coupling} vs naive={wt_plan.naive_cross_coupling} "
        f"(-{wt_plan.coupling_reduction_pct}% coupling)"
    )
    for lane in wt_plan.lanes:
        console.print(
            f"  {lane.agent_id} [wave {lane.execution_wave}] ({len(lane.files)} files): {lane.worktree_command}"
        )


@app.command("verify-merge")
def verify_merge_cmd(
    base_dir: Path = typer.Argument(..., help="Base branch directory"),
    agent_a_dir: Path = typer.Argument(..., help="Agent A worktree directory"),
    agent_b_dir: Path = typer.Argument(..., help="Agent B worktree directory"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON verification report"),
) -> None:
    """Verify 3-way semantic AST contract safety across Base, Agent A, and Agent B directories."""
    report = verify_three_way_merge(
        load_repo_files(base_dir),
        load_repo_files(agent_a_dir),
        load_repo_files(agent_b_dir),
    )
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        console.print(report.summary)
        for c in report.conflicts:
            console.print(f"  [{c.kind.value} sev={c.severity}] {c.message}")
    if report.verdict == MergeVerdict.SEMANTIC_CONFLICT_BLOCK:
        raise typer.Exit(code=2)


@app.command()
def demo(json_output: bool = typer.Option(False, "--json", help="Emit JSON demo bundle")) -> None:
    """Run built-in multi-agent worktree scheduling & semantic merge verification demo."""
    bundle = run_demo_bundle()
    if json_output:
        typer.echo(json.dumps(bundle, indent=2))
        return
    wp = bundle["worktree_plan"]
    mv = bundle["merge_verification"]
    kb = bundle["kernel_benchmark"]
    console.print(
        f"[bold cyan]WeaveGraph Demo[/bold cyan] · "
        f"cut={wp['mincut_cross_coupling']} vs naive={wp['naive_cross_coupling']} (-{wp['coupling_reduction_pct']}%)"
    )
    console.print(f"[bold yellow]3-Way Merge Gate:[/bold yellow] {mv['summary']}")
    console.print(
        f"[bold green]C++20 Kernel Bench (400 nodes):[/bold green] "
        f"{kb['native_ms']}ms native vs {kb['python_ms']}ms python ({kb['speedup']}x)"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8150, "--port"),
) -> None:
    """Start WeaveGraph FastAPI REST server."""
    uvicorn.run("weavegraph.api.app:app", host=host, port=port, reload=False)


@app.command()
def mcp() -> None:
    """Start WeaveGraph stdio JSON-RPC 2.0 MCP server."""
    serve_stdio()
