"""3-Way Semantic AST Merge Verifier detecting silent cross-file contract breaks."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from weavegraph.types import (
    ConflictKind,
    MergeVerdict,
    MergeVerificationReport,
    SemanticConflict,
)

TS_EXPORT_FUNC_RE = re.compile(
    r"""export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"""
)
TS_EXPORT_TYPE_RE = re.compile(
    r"""export\s+(?:interface|class|type)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*)?\{([^}]*)\}""",
    re.DOTALL,
)
TS_FIELD_LINE_RE = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:""", re.MULTILINE)
TS_IMPORT_NAMES_RE = re.compile(
    r"""import\s+\{([^}]+)\}\s+from\s+['"](\.[^'"]+)['"]""", re.MULTILINE
)


@dataclass
class FuncContract:
    name: str
    file_path: str
    line: int
    min_pos: int
    max_pos: int | None
    param_names: list[str]
    all_pos_names: list[str]
    has_kwargs: bool


@dataclass
class TypeContract:
    name: str
    file_path: str
    line: int
    fields: set[str]


@dataclass
class CallRef:
    name: str
    file_path: str
    line: int
    pos_args: int
    has_star_args: bool
    kw_names: list[str]
    has_star_kwargs: bool


@dataclass
class AttrRef:
    target_var: str
    attr: str
    file_path: str
    line: int


@dataclass
class ImportRef:
    symbol: str
    source_hint: str
    file_path: str
    line: int


@dataclass
class TreeContracts:
    funcs: dict[tuple[str, str], FuncContract] = field(default_factory=dict)
    types: dict[tuple[str, str], TypeContract] = field(default_factory=dict)
    exported_names_by_file: dict[str, set[str]] = field(default_factory=dict)
    calls_by_file: dict[str, list[CallRef]] = field(default_factory=dict)
    attrs_by_file: dict[str, list[AttrRef]] = field(default_factory=dict)
    imports_by_file: dict[str, list[ImportRef]] = field(default_factory=dict)
    var_type_hints: dict[tuple[str, str], str] = field(default_factory=dict)


def _stem_match(file_path: str, hint: str) -> bool:
    clean_hint = hint.lstrip(".").replace("/", ".").strip(".")
    stem = file_path.rsplit(".", 1)[0].replace("/", ".")
    return stem == clean_hint or stem.endswith(f".{clean_hint}") or clean_hint.endswith(stem)


def _extract_python_contracts(path: str, src: str, out: TreeContracts) -> None:
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return

    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pos_all = node.args.posonlyargs + node.args.args
            min_pos = max(0, len(pos_all) - len(node.args.defaults))
            max_pos = None if node.args.vararg is not None else len(pos_all)
            all_pos_names = [a.arg for a in pos_all]
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            param_names = all_pos_names + kwonly_names
            out.funcs[(path, node.name)] = FuncContract(
                name=node.name,
                file_path=path,
                line=node.lineno,
                min_pos=min_pos,
                max_pos=max_pos,
                param_names=param_names,
                all_pos_names=all_pos_names,
                has_kwargs=node.args.kwarg is not None,
            )
            exported.add(node.name)
        elif isinstance(node, ast.ClassDef):
            flds: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    flds.add(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            flds.add(tgt.id)
            out.types[(path, node.name)] = TypeContract(
                name=node.name,
                file_path=path,
                line=node.lineno,
                fields=flds,
            )
            exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    exported.add(tgt.id)

    out.exported_names_by_file[path] = exported
    calls: list[CallRef] = []
    attrs: list[AttrRef] = []
    imports: list[ImportRef] = []

    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.ImportFrom) and ast_node.module:
            for alias in ast_node.names:
                imports.append(
                    ImportRef(
                        symbol=alias.name,
                        source_hint=ast_node.module,
                        file_path=path,
                        line=ast_node.lineno,
                    )
                )
        elif isinstance(ast_node, ast.AnnAssign) and isinstance(ast_node.target, ast.Name):
            if isinstance(ast_node.annotation, ast.Name):
                out.var_type_hints[(path, ast_node.target.id)] = ast_node.annotation.id
        elif isinstance(ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in ast_node.args.posonlyargs + ast_node.args.args + ast_node.args.kwonlyargs:
                if isinstance(arg.annotation, ast.Name):
                    out.var_type_hints[(path, arg.arg)] = arg.annotation.id
        elif isinstance(ast_node, ast.Call):
            if isinstance(ast_node.func, ast.Name):
                pos_cnt = sum(1 for a in ast_node.args if not isinstance(a, ast.Starred))
                has_star = any(isinstance(a, ast.Starred) for a in ast_node.args)
                kw_names = [kw.arg for kw in ast_node.keywords if kw.arg is not None]
                has_dkw = any(kw.arg is None for kw in ast_node.keywords)
                calls.append(
                    CallRef(
                        name=ast_node.func.id,
                        file_path=path,
                        line=ast_node.lineno,
                        pos_args=pos_cnt,
                        has_star_args=has_star,
                        kw_names=kw_names,
                        has_star_kwargs=has_dkw,
                    )
                )
        elif (
            isinstance(ast_node, ast.Assign)
            and len(ast_node.targets) == 1
            and isinstance(ast_node.targets[0], ast.Name)
        ):
            if isinstance(ast_node.value, ast.Call) and isinstance(ast_node.value.func, ast.Name):
                out.var_type_hints[(path, ast_node.targets[0].id)] = ast_node.value.func.id
        elif isinstance(ast_node, ast.Attribute) and isinstance(ast_node.value, ast.Name):
            attrs.append(
                AttrRef(
                    target_var=ast_node.value.id,
                    attr=ast_node.attr,
                    file_path=path,
                    line=ast_node.lineno,
                )
            )

    out.calls_by_file[path] = calls
    out.attrs_by_file[path] = attrs
    out.imports_by_file[path] = imports


def _extract_ts_contracts(path: str, src: str, out: TreeContracts) -> None:
    exported: set[str] = set()
    for m in TS_EXPORT_FUNC_RE.finditer(src):
        name = m.group(1)
        params_raw = [p.strip() for p in m.group(2).split(",") if p.strip()]
        min_pos = sum(
            1 for p in params_raw if "?" not in p and "=" not in p and not p.startswith("...")
        )
        has_rest = any(p.startswith("...") for p in params_raw)
        p_names = [p.split(":")[0].split("=")[0].rstrip("?").lstrip(".") for p in params_raw]
        line = src.count("\n", 0, m.start()) + 1
        out.funcs[(path, name)] = FuncContract(
            name=name,
            file_path=path,
            line=line,
            min_pos=min_pos,
            max_pos=None if has_rest else len(params_raw),
            param_names=p_names,
            all_pos_names=p_names,
            has_kwargs=False,
        )
        exported.add(name)

    for m in TS_EXPORT_TYPE_RE.finditer(src):
        name = m.group(1)
        flds = {fm.group(1) for fm in TS_FIELD_LINE_RE.finditer(m.group(2))}
        line = src.count("\n", 0, m.start()) + 1
        out.types[(path, name)] = TypeContract(
            name=name,
            file_path=path,
            line=line,
            fields=flds,
        )
        exported.add(name)

    out.exported_names_by_file[path] = exported
    imports: list[ImportRef] = []
    for m in TS_IMPORT_NAMES_RE.finditer(src):
        line = src.count("\n", 0, m.start()) + 1
        for raw_sym in m.group(1).split(","):
            sym = raw_sym.strip().split(" as ")[0].strip()
            if sym:
                imports.append(
                    ImportRef(symbol=sym, source_hint=m.group(2), file_path=path, line=line)
                )
    out.imports_by_file[path] = imports


def _extract_tree_contracts(tree_files: dict[str, str]) -> TreeContracts:
    contracts = TreeContracts()
    for path, src in sorted(tree_files.items()):
        if path.endswith(".py"):
            _extract_python_contracts(path, src, contracts)
        elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
            _extract_ts_contracts(path, src, contracts)
    return contracts


def verify_three_way_merge(
    base_tree: dict[str, str],
    agent_a_tree: dict[str, str],
    agent_b_tree: dict[str, str],
    *,
    agent_a_name: str = "agent-a",
    agent_b_name: str = "agent-b",
) -> MergeVerificationReport:
    """Detect semantic AST contract violations when merging parallel agent worktrees."""
    all_paths = sorted(set(base_tree) | set(agent_a_tree) | set(agent_b_tree))
    a_changed = {p for p in all_paths if agent_a_tree.get(p) != base_tree.get(p)}
    b_changed = {p for p in all_paths if agent_b_tree.get(p) != base_tree.get(p)}

    base_c = _extract_tree_contracts(base_tree)
    a_c = _extract_tree_contracts(agent_a_tree)
    b_c = _extract_tree_contracts(agent_b_tree)

    # Construct unified merged tree view preserving edits from A and B
    merged_tree: dict[str, str] = {}
    owner_of_file: dict[str, str] = {}
    for p in all_paths:
        if p in a_changed and p not in b_changed:
            if p in agent_a_tree:
                merged_tree[p] = agent_a_tree[p]
                owner_of_file[p] = agent_a_name
        elif p in b_changed and p not in a_changed:
            if p in agent_b_tree:
                merged_tree[p] = agent_b_tree[p]
                owner_of_file[p] = agent_b_name
        elif p in a_changed and p in b_changed:
            if p in agent_a_tree:
                merged_tree[p] = agent_a_tree[p]
                owner_of_file[p] = f"{agent_a_name}+{agent_b_name}"
        elif p in base_tree:
            merged_tree[p] = base_tree[p]
            owner_of_file[p] = "base"

    merged_c = _extract_tree_contracts(merged_tree)
    conflicts: list[SemanticConflict] = []
    seen_keys: set[tuple[str, str, str, int]] = set()

    def _add_conflict(c: SemanticConflict) -> None:
        key = (c.kind.value, c.symbol_name, c.consumer_file, c.consumer_line)
        if key not in seen_keys:
            seen_keys.add(key)
            conflicts.append(c)

    # 1. Duplicate new symbol definitions introduced in the same file by both agents
    for p in sorted(a_changed & b_changed):
        base_syms = base_c.exported_names_by_file.get(p, set())
        a_new = a_c.exported_names_by_file.get(p, set()) - base_syms
        b_new = b_c.exported_names_by_file.get(p, set()) - base_syms
        for sym in sorted(a_new & b_new):
            fa = a_c.funcs.get((p, sym))
            fb = b_c.funcs.get((p, sym))
            _add_conflict(
                SemanticConflict(
                    kind=ConflictKind.DUPLICATE_DEFINITION_CLASH,
                    severity=8 if (fa and fb and fa != fb) else 7,
                    symbol_name=sym,
                    producer_agent=agent_a_name,
                    producer_file=p,
                    consumer_agent=agent_b_name,
                    consumer_file=p,
                    consumer_line=fb.line if fb else 1,
                    message=f"Both {agent_a_name} and {agent_b_name} independently defined `{sym}` in `{p}`.",
                    suggested_fix=f"Consolidate or namespace `{sym}` in `{p}` before merging.",
                )
            )

    # Build quick lookup maps in merged tree
    merged_funcs_by_name: dict[str, list[FuncContract]] = {}
    for fc in merged_c.funcs.values():
        merged_funcs_by_name.setdefault(fc.name, []).append(fc)

    merged_types_by_name: dict[str, list[TypeContract]] = {}
    for tc in merged_c.types.values():
        merged_types_by_name.setdefault(tc.name, []).append(tc)

    # 2. Check dangling imports (symbol existed in Base, removed in merged producer, still imported by consumer)
    for c_file, imp_list in merged_c.imports_by_file.items():
        cons_agent = owner_of_file.get(c_file, "base")
        for imp in imp_list:
            for m_file, base_exports in base_c.exported_names_by_file.items():
                if _stem_match(m_file, imp.source_hint) and imp.symbol in base_exports:
                    cur_exports = merged_c.exported_names_by_file.get(m_file, set())
                    if imp.symbol not in cur_exports:
                        prod_agent = owner_of_file.get(m_file, "unknown")
                        _add_conflict(
                            SemanticConflict(
                                kind=ConflictKind.DANGLING_IMPORT_OR_SYMBOL,
                                severity=9,
                                symbol_name=imp.symbol,
                                producer_agent=prod_agent,
                                producer_file=m_file,
                                consumer_agent=cons_agent,
                                consumer_file=c_file,
                                consumer_line=imp.line,
                                message=(
                                    f"`{prod_agent}` removed/renamed `{imp.symbol}` in `{m_file}`, "
                                    f"breaking import in `{c_file}:{imp.line}` ({cons_agent})."
                                ),
                                suggested_fix=f"Restore alias or update `{c_file}:{imp.line}` to import the replacement symbol.",
                            )
                        )

    # 3. Check call signature arity & keyword argument breaks across merged tree
    for c_file, call_list in merged_c.calls_by_file.items():
        cons_agent = owner_of_file.get(c_file, "base")
        imported_hints = [imp.source_hint for imp in merged_c.imports_by_file.get(c_file, [])]
        imported_syms = {imp.symbol for imp in merged_c.imports_by_file.get(c_file, [])}

        for call in call_list:
            candidates = merged_funcs_by_name.get(call.name, [])
            if not candidates:
                continue
            # Prefer functions in files imported by c_file, else unique non-local match
            matched_fc: FuncContract | None = None
            for fc in candidates:
                if fc.file_path != c_file and (
                    call.name in imported_syms
                    or any(_stem_match(fc.file_path, h) for h in imported_hints)
                ):
                    matched_fc = fc
                    break
            if matched_fc is None and len(candidates) == 1 and candidates[0].file_path != c_file:
                matched_fc = candidates[0]
            if matched_fc is None:
                continue

            prod_agent = owner_of_file.get(matched_fc.file_path, "base")
            if prod_agent == cons_agent:
                # Only report cross-agent or consumer-vs-producer regressions
                continue

            # Check removed/unknown keyword arguments
            if not matched_fc.has_kwargs and not call.has_star_kwargs:
                for kw in call.kw_names:
                    if kw not in matched_fc.param_names:
                        _add_conflict(
                            SemanticConflict(
                                kind=ConflictKind.REMOVED_KEYWORD_PARAM,
                                severity=8,
                                symbol_name=matched_fc.name,
                                producer_agent=prod_agent,
                                producer_file=matched_fc.file_path,
                                consumer_agent=cons_agent,
                                consumer_file=c_file,
                                consumer_line=call.line,
                                message=(
                                    f"Call `{call.name}(..., {kw}=...)` in `{c_file}:{call.line}` "
                                    f"targets removed/unknown keyword param on `{matched_fc.file_path}:{matched_fc.line}` ({prod_agent})."
                                ),
                                suggested_fix=f"Update `{c_file}:{call.line}` to pass one of {matched_fc.param_names}.",
                            )
                        )

            # Check positional/total arity break
            if not call.has_star_args and not call.has_star_kwargs:
                # Positional slots filled by valid positional args + valid named positional params
                named_pos = sum(1 for kw in call.kw_names if kw in matched_fc.all_pos_names)
                total_supplied = call.pos_args + named_pos
                too_few = total_supplied < matched_fc.min_pos
                too_many = matched_fc.max_pos is not None and call.pos_args > matched_fc.max_pos
                if too_few or too_many:
                    _add_conflict(
                        SemanticConflict(
                            kind=ConflictKind.SIGNATURE_ARITY_BREAK,
                            severity=9,
                            symbol_name=matched_fc.name,
                            producer_agent=prod_agent,
                            producer_file=matched_fc.file_path,
                            consumer_agent=cons_agent,
                            consumer_file=c_file,
                            consumer_line=call.line,
                            message=(
                                f"Call `{call.name}` at `{c_file}:{call.line}` supplies {call.pos_args} positional "
                                f"(total {total_supplied}) args, incompatible with `{matched_fc.file_path}:{matched_fc.line}` "
                                f"(requires {matched_fc.min_pos}..{matched_fc.max_pos if matched_fc.max_pos is not None else '∞'})."
                            ),
                            suggested_fix=f"Align `{call.name}` call signature in `{c_file}:{call.line}` or provide default value in `{matched_fc.file_path}`.",
                        )
                    )

    # 4. Check removed contract fields on classes/dataclasses/interfaces
    for (t_file, t_name), base_tc in base_c.types.items():
        merged_tc = merged_c.types.get((t_file, t_name))
        if merged_tc is None:
            continue
        removed_fields = base_tc.fields - merged_tc.fields
        if not removed_fields:
            continue
        prod_agent = owner_of_file.get(t_file, "base")
        for c_file, attr_list in merged_c.attrs_by_file.items():
            if c_file == t_file:
                continue
            cons_agent = owner_of_file.get(c_file, "base")
            if cons_agent == prod_agent:
                continue
            for ar in attr_list:
                if ar.attr in removed_fields:
                    hint_type = merged_c.var_type_hints.get((c_file, ar.target_var))
                    if hint_type == t_name or ar.target_var.lower() == t_name.lower():
                        _add_conflict(
                            SemanticConflict(
                                kind=ConflictKind.CONTRACT_FIELD_REMOVED,
                                severity=8,
                                symbol_name=f"{t_name}.{ar.attr}",
                                producer_agent=prod_agent,
                                producer_file=t_file,
                                consumer_agent=cons_agent,
                                consumer_file=c_file,
                                consumer_line=ar.line,
                                message=(
                                    f"`{prod_agent}` removed field `{t_name}.{ar.attr}` in `{t_file}`, "
                                    f"breaking attribute access in `{c_file}:{ar.line}` ({cons_agent})."
                                ),
                                suggested_fix=f"Add compatibility `@property {ar.attr}` on `{t_name}` or update `{c_file}:{ar.line}`.",
                            )
                        )

    conflicts.sort(key=lambda c: (-c.severity, c.consumer_file, c.consumer_line))
    verdict = MergeVerdict.SEMANTIC_CONFLICT_BLOCK if conflicts else MergeVerdict.SAFE_TO_MERGE
    max_sev = max((c.severity for c in conflicts), default=0)
    summary = (
        f"BLOCKED: Detected {len(conflicts)} silent semantic AST conflict(s) (max severity {max_sev}/10)."
        if conflicts
        else f"SAFE TO MERGE: Verified {len(all_paths)} files across {agent_a_name} & {agent_b_name} with 0 contract breaks."
    )
    return MergeVerificationReport(
        verdict=verdict,
        files_compared=len(all_paths),
        max_severity=max_sev,
        conflicts=conflicts,
        summary=summary,
    )
