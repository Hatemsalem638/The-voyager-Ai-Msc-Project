"""Analyse a large Python module and propose split boundaries.

Builds an internal call graph, then clusters functions by connectivity so that
proposed modules are cohesive (functions that call each other stay together).
"""
import ast
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "app/services/chat_service.py"

src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

funcs = {}
order = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        end = getattr(node, "end_lineno", node.lineno)
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        funcs[node.name] = (start, end, end - start + 1)
        order.append(node.name)

names = set(funcs)

# --- call graph -------------------------------------------------------------
calls = defaultdict(set)      # caller -> callees (internal only)
called_by = defaultdict(set)  # callee -> callers

for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            target = None
            if isinstance(fn, ast.Name):
                target = fn.id
            elif isinstance(fn, ast.Attribute):
                target = fn.attr
            if target in names and target != node.name:
                calls[node.name].add(target)
                called_by[target].add(node.name)

# --- seed clusters by name prefix (domain hints) ----------------------------
DOMAINS = {
    "hotel":       ("hotel", "stay", "room", "board", "checkin", "check_in", "checkout_date"),
    "flight":      ("flight", "offer", "cabin", "slice", "airline", "passenger"),
    "package":     ("package",),
    "destination": ("destination", "discovery", "recommend", "activity", "region", "broad"),
    "cart":        ("cart", "checkout", "booking", "confirm", "document", "ticket", "voucher"),
    "date":        ("date", "day", "month", "night", "duration", "relative", "temporal"),
    "state":       ("active_trip", "pending", "state", "memory", "session", "trip_frame"),
    "routing":     ("tool", "route", "intent", "llama", "structured", "canonical", "coerce"),
    "response":    ("response", "reply", "message_for", "format", "render", "label", "summary"),
    "location":    ("airport", "origin", "city", "location", "iata", "geo", "haversine"),
}


def domain_of(name: str) -> str:
    low = name.lower()
    best, best_pos = "misc", 10**6
    for dom, keys in DOMAINS.items():
        for k in keys:
            pos = low.find(k)
            if pos != -1 and pos < best_pos:
                best, best_pos = dom, pos
    return best


assigned = {n: domain_of(n) for n in names}

# --- refine: pull a function toward the domain most of its neighbours share --
for _ in range(6):
    changed = 0
    for n in names:
        if not calls[n] and not called_by[n]:
            continue
        votes = defaultdict(int)
        for nb in calls[n]:
            votes[assigned[nb]] += 1
        for nb in called_by[n]:
            votes[assigned[nb]] += 2   # callers weigh more: keeps helpers with users
        if not votes:
            continue
        top = max(votes.items(), key=lambda kv: kv[1])
        # only move 'misc' functions, or ones with overwhelming pull
        if assigned[n] == "misc" and top[1] >= 1:
            if assigned[n] != top[0]:
                assigned[n] = top[0]
                changed += 1
    if not changed:
        break

groups = defaultdict(list)
for n in names:
    groups[assigned[n]].append(n)

print(f"{'MODULE':<14}{'FUNCS':>7}{'LINES':>9}   COUPLING (calls out to other modules)")
print("-" * 78)
total_lines = 0
for dom in sorted(groups, key=lambda d: -sum(funcs[n][2] for n in groups[d])):
    fns = groups[dom]
    lines = sum(funcs[n][2] for n in fns)
    total_lines += lines
    out = defaultdict(int)
    for n in fns:
        for c in calls[n]:
            if assigned[c] != dom:
                out[assigned[c]] += 1
    top = ", ".join(f"{d}:{c}" for d, c in sorted(out.items(), key=lambda kv: -kv[1])[:4])
    print(f"{dom:<14}{len(fns):>7}{lines:>9}   {top}")
print("-" * 78)
print(f"{'TOTAL':<14}{len(names):>7}{total_lines:>9}")

# --- functions that would be hardest to move --------------------------------
print("\nMOST-DEPENDED-ON (move these to a shared module first):")
hot = sorted(names, key=lambda n: -len(called_by[n]))[:15]
for n in hot:
    print(f"  {len(called_by[n]):>3} callers  {assigned[n]:<12} {n}  ({funcs[n][2]} lines)")

print("\nLARGEST SINGLE FUNCTIONS:")
for n in sorted(names, key=lambda n: -funcs[n][2])[:10]:
    print(f"  {funcs[n][2]:>5} lines  {assigned[n]:<12} {n}  (line {funcs[n][0]})")
