"""Static AST (Python) & structural (TS/JS) repository dependency extractor."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from weavegraph.kernels.native import backend_name, blast_radius, schedule_waves, tarjan_scc
from weavegraph.types import (
    DependencyEdge,
    EdgeKind,
    FileGraphData,
    ImpactNode,
    ImpactReport,
    RepoGraphSummary,
    SymbolKind,
    SymbolNode,
)

SUPPORTED_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}
IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}

TS_IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?(?:[^'"]*?from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
TS_FUNC_RE = re.compile(
    r"""(?:export\s+(?:async\s+)?)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"""
)
TS_CLASS_IFACE_RE = re.compile(
    r"""(?:export\s+)?(class|interface)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:extends\s+[^{]+)?\{([^}]*)\}""",
    re.DOTALL,
)
TS_FIELD_RE = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:""", re.MULTILINE)
TS_CALL_RE = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*)\s*\(""")


def load_repo_files(root_dir: Path | str, max_files: int = 1000) -> dict[str, str]:
    """Load supported source files from `root_dir` into a `{rel_path: source}` dictionary."""
    root = Path(root_dir).resolve()
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if len(files) >= max_files:
            break
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            rel = p.relative_to(root).as_posix()
            files[rel] = p.read_text(encoding="utf-8", errors="ignore")
    return files


def _module_candidates(file_path: str) -> list[str]:
    p = Path(file_path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    candidates = [".".join(parts)]
    if parts[0] in {"src", "lib", "app"} and len(parts) > 1:
        candidates.append(".".join(parts[1:]))
    return candidates


def _resolve_python_import(
    importer: str, module_str: str | None, level: int, mod_index: dict[str, str]
) -> str | None:
    if level > 0:
        cur_parts = list(Path(importer).parent.parts)
        up = max(0, level - 1)
        if up <= len(cur_parts):
            base_parts = cur_parts[: len(cur_parts) - up] if up > 0 else cur_parts
            if module_str:
                base_parts.extend(module_str.split("."))
            rel_mod = ".".join(base_parts)
            if rel_mod in mod_index:
                return mod_index[rel_mod]
    if module_str:
        if module_str in mod_index:
            return mod_index[module_str]
        head = module_str.rsplit(".", 1)[0]
        if head in mod_index:
            return mod_index[head]
    return None


def _resolve_ts_import(importer: str, spec: str, file_set: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    base = (Path(importer).parent / spec).as_posix()
    # Normalize '..' segments manually on posix paths
    norm_parts: list[str] = []
    for part in base.split("/"):
        if part in ("", "."):
            continue
        if part == ".." and norm_parts:
            norm_parts.pop()
        elif part != "..":
            norm_parts.append(part)
    norm = "/".join(norm_parts)
    for ext in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"):
        cand = f"{norm}{ext}"
        if cand in file_set:
            return cand
    return None


def _extract_python_file(
    rel_path: str, source: str, mod_index: dict[str, str]
) -> tuple[list[SymbolNode], list[str], list[str], list[str]]:
    symbols: list[SymbolNode] = []
    imported_files: list[str] = []
    called_names: list[str] = []
    attr_names: list[str] = []

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return symbols, imported_files, called_names, attr_names

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pos_args = node.args.posonlyargs + node.args.args
            num_defaults = len(node.args.defaults)
            min_args = max(0, len(pos_args) - num_defaults)
            max_args = None if node.args.vararg is not None else len(pos_args)
            kw_names = [a.arg for a in pos_args] + [a.arg for a in node.args.kwonlyargs]
            symbols.append(
                SymbolNode(
                    node_id=f"{rel_path}::{node.name}",
                    file_path=rel_path,
                    name=node.name,
                    kind=SymbolKind.FUNCTION,
                    line=node.lineno,
                    min_args=min_args,
                    max_args=max_args,
                    kwarg_names=kw_names,
                )
            )
        elif isinstance(node, ast.ClassDef):
            fields: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            fields.append(tgt.id)
            symbols.append(
                SymbolNode(
                    node_id=f"{rel_path}::{node.name}",
                    file_path=rel_path,
                    name=node.name,
                    kind=SymbolKind.CLASS,
                    line=node.lineno,
                    fields=fields,
                )
            )

    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.Import):
            for alias in ast_node.names:
                tgt_file = _resolve_python_import(rel_path, alias.name, 0, mod_index)
                if tgt_file and tgt_file != rel_path:
                    imported_files.append(tgt_file)
        elif isinstance(ast_node, ast.ImportFrom):
            tgt_file = _resolve_python_import(rel_path, ast_node.module, ast_node.level, mod_index)
            if tgt_file and tgt_file != rel_path:
                imported_files.append(tgt_file)
            elif ast_node.module:
                for alias in ast_node.names:
                    sub = f"{ast_node.module}.{alias.name}"
                    sub_tgt = _resolve_python_import(rel_path, sub, ast_node.level, mod_index)
                    if sub_tgt and sub_tgt != rel_path:
                        imported_files.append(sub_tgt)
        elif isinstance(ast_node, ast.Call):
            if isinstance(ast_node.func, ast.Name):
                called_names.append(ast_node.func.id)
            elif isinstance(ast_node.func, ast.Attribute):
                called_names.append(ast_node.func.attr)
        elif isinstance(ast_node, ast.Attribute):
            attr_names.append(ast_node.attr)

    return symbols, imported_files, called_names, attr_names


def _extract_ts_file(
    rel_path: str, source: str, file_set: set[str]
) -> tuple[list[SymbolNode], list[str], list[str], list[str]]:
    symbols: list[SymbolNode] = []
    imported_files: list[str] = []
    called_names: list[str] = []

    for m in TS_IMPORT_RE.finditer(source):
        tgt = _resolve_ts_import(rel_path, m.group(1), file_set)
        if tgt and tgt != rel_path:
            imported_files.append(tgt)

    for m in TS_FUNC_RE.finditer(source):
        name = m.group(1)
        params_raw = [p.strip() for p in m.group(2).split(",") if p.strip()]
        min_args = sum(
            1 for p in params_raw if "?" not in p and "=" not in p and not p.startswith("...")
        )
        has_rest = any(p.startswith("...") for p in params_raw)
        line = source.count("\n", 0, m.start()) + 1
        kw_names = [p.split(":")[0].split("=")[0].rstrip("?").lstrip(".") for p in params_raw]
        symbols.append(
            SymbolNode(
                node_id=f"{rel_path}::{name}",
                file_path=rel_path,
                name=name,
                kind=SymbolKind.FUNCTION,
                line=line,
                min_args=min_args,
                max_args=None if has_rest else len(params_raw),
                kwarg_names=kw_names,
            )
        )

    for m in TS_CLASS_IFACE_RE.finditer(source):
        kind = SymbolKind.CLASS if m.group(1) == "class" else SymbolKind.INTERFACE
        name = m.group(2)
        fields = [fm.group(1) for fm in TS_FIELD_RE.finditer(m.group(3))]
        line = source.count("\n", 0, m.start()) + 1
        symbols.append(
            SymbolNode(
                node_id=f"{rel_path}::{name}",
                file_path=rel_path,
                name=name,
                kind=kind,
                line=line,
                fields=fields,
            )
        )

    for m in TS_CALL_RE.finditer(source):
        called_names.append(m.group(1))

    return symbols, imported_files, called_names, []


def build_file_graph(
    files: dict[str, str],
    *,
    use_native: bool = True,
) -> FileGraphData:
    """Build weighted file dependency graph, SCCs, and topological waves from `{path: source}`."""
    sorted_files = sorted(files.keys())
    file_set = set(sorted_files)
    file_to_idx = {f: i for i, f in enumerate(sorted_files)}

    mod_index: dict[str, str] = {}
    for f in sorted_files:
        if f.endswith(".py"):
            for cand in _module_candidates(f):
                mod_index[cand] = f

    all_symbols: list[SymbolNode] = []
    symbol_owners: dict[str, list[str]] = {}
    field_owners: dict[str, list[str]] = {}
    per_file_imports: dict[str, list[str]] = {}
    per_file_calls: dict[str, list[str]] = {}
    per_file_attrs: dict[str, list[str]] = {}

    for f in sorted_files:
        src = files[f]
        if f.endswith(".py"):
            syms, imps, calls, attrs = _extract_python_file(f, src, mod_index)
        else:
            syms, imps, calls, attrs = _extract_ts_file(f, src, file_set)
        all_symbols.extend(syms)
        per_file_imports[f] = imps
        per_file_calls[f] = calls
        per_file_attrs[f] = attrs
        for s in syms:
            symbol_owners.setdefault(s.name, []).append(f)
            for fld in s.fields:
                field_owners.setdefault(fld, []).append(f)

    edge_weights: dict[tuple[str, str, EdgeKind], int] = {}
    for src_file in sorted_files:
        for tgt in per_file_imports[src_file]:
            key = (src_file, tgt, EdgeKind.IMPORT)
            edge_weights[key] = edge_weights.get(key, 0) + 5

        imported_set = set(per_file_imports[src_file])
        for cname in per_file_calls[src_file]:
            owners = [o for o in symbol_owners.get(cname, []) if o != src_file]
            matched = [o for o in owners if o in imported_set] or (
                owners if len(owners) == 1 else []
            )
            for tgt in matched:
                key = (src_file, tgt, EdgeKind.CALL)
                edge_weights[key] = edge_weights.get(key, 0) + 10

        for attr in per_file_attrs[src_file]:
            owners = [o for o in field_owners.get(attr, []) if o != src_file and o in imported_set]
            for tgt in owners:
                key = (src_file, tgt, EdgeKind.FIELD_ACCESS)
                edge_weights[key] = edge_weights.get(key, 0) + 7

    # Combine multi-kind parallel file edges cleanly into DependencyEdge list
    edges = [
        DependencyEdge(source=s, target=t, kind=k, weight=w)
        for (s, t, k), w in sorted(edge_weights.items())
    ]

    dir_edges = sorted({(file_to_idx[e.source], file_to_idx[e.target]) for e in edges})
    n = len(sorted_files)
    comp_ids = tarjan_scc(n, dir_edges, use_native=use_native)
    wave_ids = schedule_waves(n, dir_edges, use_native=use_native)

    comp_map: dict[int, list[str]] = {}
    for idx, cid in enumerate(comp_ids):
        comp_map.setdefault(cid, []).append(sorted_files[idx])
    scc_groups = sorted(comp_map.values(), key=lambda g: (-len(g), g[0]))
    waves = {sorted_files[idx]: wave_ids[idx] for idx in range(n)}
    file_weights = {f: max(1, len(files[f].splitlines()) // 20 + 1) for f in sorted_files}

    return FileGraphData(
        files=sorted_files,
        edges=edges,
        symbols=all_symbols,
        file_weights=file_weights,
        scc_groups=scc_groups,
        waves=waves,
        backend=backend_name() if use_native else "python-fallback",
    )


def summarize_graph(graph: FileGraphData) -> RepoGraphSummary:
    """Summarize dependency cycles, wave depth, and file counts."""
    cycles = [g for g in graph.scc_groups if len(g) > 1]
    wave_vals = list(graph.waves.values())
    max_w = max(wave_vals) if wave_vals else -1
    wave_count = max_w + 1 if wave_vals else 0
    max_wave_files = [f for f, w in graph.waves.items() if w == max_w]
    return RepoGraphSummary(
        total_files=len(graph.files),
        total_symbols=len(graph.symbols),
        total_edges=len(graph.edges),
        scc_count=len(graph.scc_groups),
        cycle_groups=cycles,
        wave_count=wave_count,
        max_wave_files=max_wave_files,
        backend=graph.backend,
        file_waves=graph.waves,
    )


def compute_blast_radius(
    graph: FileGraphData,
    seed_files: list[str],
    *,
    use_native: bool = True,
) -> ImpactReport:
    """Compute transitive dependents impacted when `seed_files` are modified."""
    file_to_idx = {f: i for i, f in enumerate(graph.files)}
    valid_seeds = [f for f in seed_files if f in file_to_idx]
    seed_indices = [file_to_idx[f] for f in valid_seeds]
    dir_edges = [(file_to_idx[e.source], file_to_idx[e.target]) for e in graph.edges]
    dists = blast_radius(len(graph.files), dir_edges, seed_indices, use_native=use_native)

    affected: list[ImpactNode] = []
    for idx, d in enumerate(dists):
        if d >= 0:
            fp = graph.files[idx]
            affected.append(ImpactNode(file_path=fp, hop_distance=d, wave=graph.waves.get(fp, 0)))

    affected.sort(key=lambda n: (n.hop_distance, n.file_path))
    ratio = round(len(affected) / max(1, len(graph.files)), 4)
    return ImpactReport(
        seeds=valid_seeds,
        affected=affected,
        affected_ratio=ratio,
        backend=backend_name() if use_native else "python-fallback",
    )
