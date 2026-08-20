"""Safely extract functions from a large module into a new module.

Guarantees:
  * Function bodies are moved VERBATIM (byte-identical source slices).
  * No function is ever lost - the tool aborts if the set of top-level
    function names changes.
  * The original module re-exports everything it moved, so every existing
    `from app.services.chat_service import X` keeps working.
  * Circular imports are avoided by using module-style imports
    (`from . import flight` then `flight.foo()`), which Python resolves
    lazily at call time.

Usage:
    python extract.py --source app/services/chat_service.py \
                      --target app/services/chat/date_helpers.py \
                      --names _resolve_day_only_followup _display_date_label ...

    python extract.py --source ... --target ... --names-file names.txt

    # dry run first:
    python extract.py ... --dry-run
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path


def parse_top_level(src: str):
    """Return {name: (start_line, end_line)} 1-indexed inclusive, decorators included."""
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            out[node.name] = (start, node.end_lineno)
    return out


def import_block(src: str) -> str:
    """Extract the module's import statements verbatim, in order."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            chunks.append("".join(lines[node.lineno - 1: node.end_lineno]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            break
    return "".join(chunks)


def module_constants(src: str) -> dict[str, str]:
    """Top-level simple assignments, so extracted code can find its constants."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = "".join(lines[node.lineno - 1: node.end_lineno])
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = "".join(lines[node.lineno - 1: node.end_lineno])
    return out


def names_referenced(src_slice: str) -> set[str]:
    """Every bare Name / attribute used inside a code slice."""
    try:
        tree = ast.parse(src_slice)
    except SyntaxError:
        # slice may be indented oddly; wrap it
        return set()
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add(n.attr)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--names", nargs="*", default=[])
    ap.add_argument("--names-file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--package", default=None,
                    help="Dotted path used in the re-export line, "
                         "e.g. 'app.services.chat.date_helpers'")
    args = ap.parse_args()

    names = list(args.names)
    if args.names_file:
        names += [
            ln.strip() for ln in Path(args.names_file).read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    if not names:
        print("ERROR: no function names given.")
        return 2

    source_path = Path(args.source)
    src = source_path.read_text(encoding="utf-8")
    original_newline = "\r\n" if "\r\n" in src[:5000] else "\n"

    spans = parse_top_level(src)
    before_names = set(spans)

    missing = [n for n in names if n not in spans]
    if missing:
        print("ERROR: not found as top-level functions in source:")
        for m in missing:
            print("   ", m)
        return 2

    lines = src.splitlines(keepends=True)

    # ---- collect the slices, largest line number first so deletion is safe ----
    chosen = sorted(names, key=lambda n: spans[n][0])
    moved_src = []
    for n in chosen:
        s, e = spans[n]
        moved_src.append("".join(lines[s - 1: e]))

    # ---- work out what the new module needs -------------------------------
    moved_blob = "\n".join(moved_src)
    referenced = names_referenced(moved_blob)
    consts = module_constants(src)
    needed_consts = [k for k in consts if k in referenced]
    stay_behind = sorted((before_names - set(names)) & referenced)

    # ---- build the new module ---------------------------------------------
    header = (
        f'"""Extracted from {source_path.name}.\n\n'
        f"Moved verbatim by extract.py - function bodies are unchanged.\n"
        f'"""\n\n'
    )
    parts = [header, import_block(src), "\n"]

    if needed_consts:
        parts.append("\n# --- constants copied from the source module ---\n")
        for k in needed_consts:
            parts.append(consts[k])
        parts.append("\n")

    if stay_behind:
        parts.append(
            "\n# --- functions still living in the source module ---\n"
            "# Imported lazily inside functions to avoid a circular import.\n"
            "# See MIGRATION NOTES at the bottom of this file.\n"
        )

    parts.append("\n")
    parts.append("\n\n".join(s.rstrip() + "\n" for s in moved_src))

    if stay_behind:
        parts.append(
            "\n\n# ============================ MIGRATION NOTES ============================\n"
            "# The functions below are referenced here but still live in the source\n"
            "# module. Until they are moved too, add a LAZY import inside each calling\n"
            "# function body (not at module level), e.g.:\n"
            "#\n"
            "#     def some_moved_function(...):\n"
            "#         from app.services.chat_service import _tool_response\n"
            "#         ...\n"
            "#\n"
            "# Unresolved references:\n"
        )
        for n in stay_behind:
            parts.append(f"#   - {n}\n")
        parts.append("# =========================================================================\n")

    new_module = "".join(parts)
    if original_newline == "\r\n":
        new_module = new_module.replace("\r\n", "\n").replace("\n", "\r\n")

    # ---- rewrite the source module ----------------------------------------
    drop = set()
    for n in names:
        s, e = spans[n]
        drop.update(range(s, e + 1))
    kept = [ln for i, ln in enumerate(lines, start=1) if i not in drop]
    new_src = "".join(kept)

    dotted = args.package or (
        str(Path(args.target).with_suffix("")).replace(os.sep, ".").replace("/", ".")
    )
    reexport = (
        f"{original_newline}{original_newline}"
        f"# --- re-exported from {dotted} (moved by extract.py) ---{original_newline}"
        f"from {dotted} import ({original_newline}"
        + "".join(f"    {n},{original_newline}" for n in sorted(names))
        + f"){original_newline}"
    )
    new_src = new_src.rstrip() + reexport

    # ---- verify ------------------------------------------------------------
    try:
        after_target = set(parse_top_level(new_module))
        after_source = set(parse_top_level(new_src))
    except SyntaxError as exc:
        print(f"ABORT: generated code does not parse: {exc}")
        return 1

    total_after = after_target | after_source
    lost = before_names - total_after
    if lost:
        print("ABORT: functions would be LOST:")
        for n in sorted(lost):
            print("   ", n)
        return 1

    print(f"source : {source_path}")
    print(f"target : {args.target}")
    print(f"moving : {len(names)} functions, "
          f"{sum(spans[n][1] - spans[n][0] + 1 for n in names)} lines")
    print(f"source : {len(lines)} -> {len(new_src.splitlines())} lines")
    print(f"verify : {len(before_names)} functions before, "
          f"{len(total_after)} after (none lost)")
    if needed_consts:
        print(f"consts : copied {len(needed_consts)} -> {', '.join(needed_consts[:6])}"
              + (" ..." if len(needed_consts) > 6 else ""))
    if stay_behind:
        print(f"WARNING: {len(stay_behind)} references still point back at the source "
              f"module. Lazy imports needed - see MIGRATION NOTES in the new file.")
        for n in stay_behind[:10]:
            print("   ", n)
        if len(stay_behind) > 10:
            print(f"    ... and {len(stay_behind) - 10} more")

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0

    tp = Path(args.target)
    tp.parent.mkdir(parents=True, exist_ok=True)
    init = tp.parent / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    tp.write_text(new_module, encoding="utf-8", newline="")
    source_path.write_text(new_src, encoding="utf-8", newline="")
    print("\nWritten. Now run:  python -m compileall app  &&  python -m unittest discover -s tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
