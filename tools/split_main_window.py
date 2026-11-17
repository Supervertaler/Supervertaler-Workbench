"""Move some of SupervertalerQt's methods into a mixin, unchanged.

Usage: python tools/split_main_window.py <repo> <file> <MixinClass> "<docstring>" <regex> [exclude,names]

Writes modules/main_window/<file>.py with `class <MixinClass>:` holding the
selected methods (text unchanged), adds the mixin to SupervertalerQt's bases
and imports it just above the class. Refuses (changes nothing) when a method:
uses super() or names SupervertalerQt, overrides a Qt event handler, is
defined twice, uses __file__, or uses a name defined only in Supervertaler.py.
"""
import ast
import re
import sys
from pathlib import Path

REPO, FILE, MIXIN, DOC, RX = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4].replace(chr(92) + "n", chr(10)), sys.argv[5]
EXCLUDE = set(sys.argv[6].split(",")) if len(sys.argv) > 6 and sys.argv[6] else set()
MAIN = REPO / "Supervertaler.py"
PKG = REPO / "modules" / "main_window"

src = MAIN.read_text(encoding="utf-8")
lines = src.split("\n")
tree = ast.parse(src)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SupervertalerQt")
fns = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
counts = {}
for f in fns:
    counts[f.name] = counts.get(f.name, 0) + 1

sel = [f for f in fns if re.search(RX, f.name, re.I) and f.name not in EXCLUDE]

# Module-level import bindings of Supervertaler.py (incl. inside top-level try/if).
import_of = {}
stack = list(tree.body)
while stack:
    x = stack.pop()
    if isinstance(x, ast.Import):
        for a in x.names:
            import_of[a.asname or a.name.split(".")[0]] = ("import", a.name, a.asname)
    elif isinstance(x, ast.ImportFrom) and x.level == 0:
        for a in x.names:
            import_of[a.asname or a.name] = ("from", x.module, a.name, a.asname)
    elif isinstance(x, (ast.Try, ast.If)):
        stack += x.body + x.orelse + [h for hh in getattr(x, "handlers", []) for h in hh.body]

top_defined = set()
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
        top_defined.add(n.name)
    elif isinstance(n, ast.Assign):
        top_defined |= {x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name)}

problems = []
for f in sel:
    text = ast.unparse(f)
    if "super(" in text or "SupervertalerQt" in text:
        problems.append(f"{f.name}: super()/SupervertalerQt")
    if f.name.endswith("Event") or f.name in ("eventFilter", "event", "__init__"):
        problems.append(f"{f.name}: Qt override / __init__")
    if counts[f.name] > 1:
        problems.append(f"{f.name}: defined twice")
    if "__file__" in text:
        problems.append(f"{f.name}: uses __file__")


def start_line(f):
    first = min([f.lineno] + [d.lineno for d in f.decorator_list])
    j = first - 1
    while j - 1 >= 0 and lines[j - 1].strip().startswith("#"):
        j -= 1
    return j + 1  # 1-based


chunks = []
spans = []
prev_end = {}
ordered = sorted(fns, key=lambda f: f.lineno)
for k, f in enumerate(ordered):
    prev_end[f.name, f.lineno] = ordered[k - 1].end_lineno if k else cls.lineno
for f in sorted(sel, key=lambda f: f.lineno):
    s = max(start_line(f), prev_end[f.name, f.lineno] + 1)
    spans.append((s, f.end_lineno))
    chunks.append("\n".join(lines[s - 1:f.end_lineno]))

code = "\n\n".join(chunks)
sub = ast.parse("class _X:\n" + code)
loaded = {x.id for x in ast.walk(sub) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
bound = {x.id for x in ast.walk(sub) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
bound |= {a.arg for x in ast.walk(sub) if isinstance(x, ast.arguments)
          for a in x.args + x.kwonlyargs + x.posonlyargs + [v for v in (x.vararg, x.kwarg) if v]}
bound |= {(a.asname or a.name).split(".")[0] for x in ast.walk(sub)
          if isinstance(x, (ast.Import, ast.ImportFrom)) for a in x.names}
needed = {}
for nm in sorted(loaded):
    if nm in import_of:
        needed[nm] = import_of[nm]
    elif nm in top_defined and nm not in bound:
        problems.append(f"uses {nm}, defined only in Supervertaler.py")

if problems:
    print("REFUSED:\n  " + "\n  ".join(sorted(set(problems))))
    sys.exit(1)

head = [f'"""{DOC}\n\nPart of the main window (SupervertalerQt), moved here unchanged. The\nmethods run as the main window\'s own: `self` is the SupervertalerQt.\n"""', ""]
for mod, asname in sorted({(v[1], v[2]) for v in needed.values() if v[0] == "import"}, key=lambda m: (m[0], m[1] or "")):
    head.append(f"import {mod}" + (f" as {asname}" if asname else ""))
froms = {}
for v in needed.values():
    if v[0] == "from":
        froms.setdefault(v[1], set()).add(v[2] + (f" as {v[3]}" if v[3] else ""))
for mod in sorted(froms):
    names = sorted(froms[mod])
    head.append(f"from {mod} import " + (", ".join(names) if len(names) <= 3 else
                "(\n    " + ",\n    ".join(names) + ",\n)"))

PKG.mkdir(exist_ok=True)
init = PKG / "__init__.py"
if not init.exists():
    init.write_text('"""The main window (SupervertalerQt), one file per job.\n\nEach file holds a mixin with some of the main window\'s methods, moved\nunchanged out of Supervertaler.py. SupervertalerQt inherits them all.\n"""\n', encoding="utf-8", newline="\n")
out_file = PKG / f"{FILE}.py"
assert not out_file.exists(), out_file
out_file.write_text("\n".join(head) + "\n\n\nclass " + MIXIN + ":\n" + code + "\n", encoding="utf-8", newline="\n")

# Rewrite Supervertaler.py: drop the spans, add the base and the import.
drop = set()
for (s, e), f in zip(spans, sorted(sel, key=lambda f: f.lineno)):
    # Also drop the blank lines just above the method (between methods,
    # so never inside a string).
    b = s - 1
    while b > prev_end[f.name, f.lineno] and not lines[b - 1].strip():
        b -= 1
    drop |= set(range(b + 1, e + 1))
new = [l for i, l in enumerate(lines, 1) if i not in drop]
text = "\n".join(new)
cls_line = lines[cls.lineno - 1]
bases = cls_line[cls_line.index("(") + 1:cls_line.index(")")]
new_cls_line = f"class SupervertalerQt({MIXIN}, {bases}):"
assert text.count(cls_line) == 1
text = text.replace(cls_line, f"from modules.main_window.{FILE} import {MIXIN}  # moved out of this class, unchanged\n\n\n" + new_cls_line)
MAIN.write_text(text, encoding="utf-8", newline="\n")
print(f"moved {len(sel)} methods, {sum(e - s + 1 for s, e in spans)} lines -> {out_file.name}")
print("Supervertaler.py lines:", text.count("\n") + 1)
