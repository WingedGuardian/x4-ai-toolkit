r"""The guard that makes the bug class extinct instead of merely fixed.

Every false pass this package has shipped had one shape::

    except SomeError:
        continue          # or: pass / return set() / return [] / return None

The caller then cannot tell *found nothing* from *could not look*, and silence
renders as OK. It has now been fixed at four layers (`Report.skipped`,
`coverage.json`, `_input.require_mod_dir`, `_scan.iter_mod_xml`) — and it kept
coming back because six modules each hand-rolled the same loop. Fixing instances
without a guard just schedules the seventh.

So: walk the package AST and fail on any handler whose body only swallows.

**Allowlisting is by inline marker comment, never by file+line.** Line numbers
churn on every edit, so a line-based allowlist rots into a rubber stamp within a
week — and it puts the justification somewhere nobody reads. A marker forces the
reason to live at the code it excuses::

    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim, failure means default codec

Prototyped before adoption: 28 sites flagged, ~13 of them legitimate. That ratio
is workable only because the reason travels with the code.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "x4validate"
MARKER = "silent-ok:"


def _is_swallow(body: list[ast.stmt]) -> bool:
    """True if the handler body does nothing but discard the failure."""
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return True
        # `return None` / `return set()` / `return []` / `return {}` / `return ""`
        if isinstance(stmt.value, ast.Constant) and not stmt.value.value:
            return True
        if isinstance(stmt.value, (ast.List, ast.Dict)) and not getattr(stmt.value, "elts", []) \
                and not getattr(stmt.value, "keys", []):
            return True
        if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name) \
                and stmt.value.func.id in {"set", "list", "dict", "tuple"} \
                and not stmt.value.args:
            return True
    return False


def _marked(source_lines: list[str], handler: ast.ExceptHandler) -> bool:
    """True if the handler carries a `# silent-ok:` marker on any of its lines."""
    start = handler.lineno - 1
    end = getattr(handler, "end_lineno", handler.lineno)
    return any(MARKER in line for line in source_lines[start:end])


def _offenders(package: Path = PACKAGE) -> list[str]:
    out = []
    for path in ([package] if package.is_file() else sorted(package.glob("*.py"))):
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")  # AST lines count LF, not Unicode paragraph/line separators.
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if _is_swallow(node.body) and not _marked(lines, node):
                out.append(f"{path.name}:{node.lineno}")
    return out


GATES = PACKAGE.parent / "gates"


def test_housekeep_has_no_unmarked_silent_swallow():
    assert _offenders(PACKAGE.parents[2] / "scripts" / "x4housekeep.py") == []


def test_no_unmarked_silent_swallow_in_the_gates():
    """The gates ship publicly and are subject to the same rule.

    Added after `consistency_audit.py` crashed on a prop shape it did not
    understand and its own failure surfaced as "the tools DISAGREE" — a checker
    accusing the thing it checks. A gate that swallows is worse than a module
    that does: it converts its own blind spot into a verdict about someone
    else's code.
    """
    offenders = _offenders(GATES)
    assert not offenders, (
        "these gate handlers swallow a failure with no channel and no reason:\n  "
        + "\n  ".join(offenders) +
        "\n\nA gate must distinguish 'I could not check this' from 'this is wrong'. "
        "Record the skip, or add an inline `# silent-ok: <reason>` marker."
    )


def test_no_unmarked_silent_swallow():
    """A handler that discards a failure must say why, at the code that does it."""
    offenders = _offenders()
    assert not offenders, (
        "these except-handlers swallow a failure with no channel for the work not "
        "done, and no justification:\n  " + "\n  ".join(offenders) +
        "\n\nEither record the failure (Report.skipped / an `unreadable` list / a "
        "printed exclusion) or add an inline `# silent-ok: <reason>` marker."
    )


def test_the_guard_actually_detects_a_swallow(tmp_path):
    """Mutation test for the guard itself.

    A guard that cannot fail is worse than no guard: it reports green forever and
    everyone stops thinking about the thing it was meant to watch.
    """
    fake = tmp_path / "pkg"
    fake.mkdir()
    (fake / "_bad.py").write_text(
        "def f():\n    try:\n        g()\n    except ValueError:\n        continue\n",
        encoding="utf-8")
    assert _offenders(fake) == ["_bad.py:4"]
    assert _offenders(fake / "_bad.py") == ["_bad.py:4"]


def test_the_marker_silences_it(tmp_path):
    fake = tmp_path / "pkg"
    fake.mkdir()
    (fake / "_ok.py").write_text(
        "def f():\n    try:\n        g()\n    except ValueError:\n"
        "        continue  # silent-ok: tested reason\n",
        encoding="utf-8")
    assert _offenders(fake) == []


def test_unicode_separators_do_not_shift_ast_marker_lines(tmp_path):
    fake = tmp_path / "pkg"
    fake.mkdir()
    p = fake / "_ok.py"
    source = ('"""separators: \u2028\u2029"""\n'
              'def f():\n    try:\n        g()\n'
              '    except OSError:  # silent-ok: failure reported by caller\n        return None\n')
    p.write_text(source, encoding="utf-8")
    assert _offenders(fake) == []
    p.write_text(source.replace("silent-ok:", "explanation:"), encoding="utf-8")
    assert _offenders(fake) == ["_ok.py:5"]


# --- second shape of the same class: control-flow swallow --------------------
# The guard above only inspects `except` handlers. The defect that dropped 858
# installed-mod ops was not in a handler at all::
#
#     parent = t.getparent()
#     if parent is None:
#         continue          # op discarded, yet reported applied=True
#
# Same failure — a branch that does nothing and tells no one — different syntax.
# Scope this to the diff-application helpers, where "did nothing" must always be
# reportable, so the rule stays sharp instead of flagging ordinary guard clauses.

MUTATORS = {"_do_replace", "_do_remove", "_do_add"}


def _control_flow_swallows(package: Path = PACKAGE) -> list[str]:
    out = []
    path = package / "_merge.py"
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")  # Match Python AST line numbers, as in _offenders.
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name not in MUTATORS:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.If):
                continue
            if not _is_swallow(inner.body):
                continue
            start, end = inner.lineno - 1, getattr(inner, "end_lineno", inner.lineno)
            if not any(MARKER in ln for ln in lines[start:end]):
                out.append(f"{path.name}:{inner.lineno} in {node.name}()")
    return out


def test_diff_helpers_never_swallow_an_op_silently():
    """A mutation helper that cannot apply an op must SAY so (return a reason),
    never just `continue` — otherwise apply_diff reports it as applied."""
    offenders = _control_flow_swallows()
    assert not offenders, (
        "these branches abandon a diff op without reporting why:\n  "
        + "\n  ".join(offenders) +
        "\n\nReturn a reason string so apply_diff can set AppliedOp.ok=False, or "
        "add an inline `# silent-ok: <reason>` marker."
    )


def test_the_control_flow_guard_actually_detects_one(tmp_path):
    """Mutation test for the new guard, mirroring the one above it."""
    fake = tmp_path / "pkg"
    fake.mkdir()
    (fake / "_merge.py").write_text(
        "def _do_replace(t):\n    for x in t:\n        if x is None:\n            continue\n",
        encoding="utf-8")
    assert _control_flow_swallows(fake) == ["_merge.py:3 in _do_replace()"]


def test_unicode_separators_do_not_shift_control_flow_markers(tmp_path):
    p=tmp_path/"_merge.py"
    source=('"""separators: \u2028\u2029"""\n'
            'def _do_replace(t):\n    for x in t:\n'
            '        if x is None:  # silent-ok: test marker\n            continue\n')
    p.write_text(source,encoding="utf-8")
    assert _control_flow_swallows(tmp_path)==[]
    p.write_text(source.replace("silent-ok:","explanation:"),encoding="utf-8")
    assert _control_flow_swallows(tmp_path)==["_merge.py:4 in _do_replace()"]
