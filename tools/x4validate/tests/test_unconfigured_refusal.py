r"""An unconfigured toolkit must REFUSE, never guess — and say so with rc=2.

**The defect this pins (2.5.0).** `_merge.REFERENCE` was::

    REFERENCE = _paths.reference() or Path("reference")

On a machine with nothing configured that resolved to the *relative* path
``reference``, i.e. whatever happens to sit under the current directory. The
consequences ran all the way to the exit code:

  * `_cli.py` passes ``--reference`` with default ``str(_merge.REFERENCE)``, so a
    cold run validated a mod against a tree that does not exist, produced a wall
    of findings about missing base-game files, and returned **rc=1**.
  * rc=1 is also "your mod has real errors". A caller — a script, a CI job, the
    user — could not tell *"your mod is broken"* from *"your toolkit isn't set
    up"*, which are opposite actions.

Commit `ae79fcd` had already named CWD-relative fallback as the defect it was
fixing ("invisible here only because this machine's hardcoded defaults happened
to be right"); this was the last survivor of that family.

**The boundary is deliberate and measured.** The refusal fires when the reference
is *unresolved* (None), NOT when a caller names a tree that happens not to exist:
**12 tests** pass `Config(reference=tmp_path / "does_not_exist")` on purpose —
`test_exprlint.py:80` says so in as many words, "exprlint needs no reference
tree". Making absence-on-disk an error would break honest callers and is a
different question from "you never told me where it is".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from x4validate import _merge, _paths


@pytest.fixture
def cold(monkeypatch):
    """Simulate a machine where no reference tree can be resolved."""
    monkeypatch.setattr(_merge, "REFERENCE", None)
    return monkeypatch


# --- the refusal itself -----------------------------------------------------

def test_config_refuses_when_no_reference_is_configured(cold):
    with pytest.raises(_paths.Unconfigured) as exc:
        _merge.Config()
    msg = str(exc.value)
    assert "X4_REFERENCE" in msg, "the refusal must name the variable to set"
    assert "x4-paths.env" in msg, "and the config file that also supplies it"


def test_the_refusal_never_names_a_guessed_path(cold):
    """A message mentioning a relative `reference` would re-teach the guess."""
    with pytest.raises(_paths.Unconfigured) as exc:
        _merge.Config()
    assert "reference'" not in str(exc.value).replace('"', "'")


def test_config_accepts_an_explicit_reference_that_does_not_exist(tmp_path):
    """The 12-callers boundary: naming a tree is not the same as resolving one.

    `check_exprlint`, the debug-log correlation and several merge tests all pass
    a reference that was never created, because they exercise code paths that do
    not read it. That must keep working.
    """
    cfg = _merge.Config(reference=tmp_path / "does_not_exist")
    assert cfg.reference == tmp_path / "does_not_exist"


def test_an_explicit_reference_survives_a_cold_default(cold, tmp_path):
    """Passing one explicitly is exactly how a cold machine is meant to work."""
    cfg = _merge.Config(reference=tmp_path)
    assert cfg.reference == tmp_path


# --- the exit code the boundary reports -------------------------------------

def test_cli_exits_2_not_1_when_unconfigured(cold, tmp_path, capsys):
    """rc=1 means "your mod has findings"; rc=2 means "your toolkit isn't set up"."""
    from x4validate import _cli
    mod = tmp_path / "mymod"
    mod.mkdir()
    rc = _cli.main([str(mod)])
    assert rc == 2, "an unconfigured toolkit must not report mod findings"
    err = capsys.readouterr().err
    assert "X4_REFERENCE" in err, "and must say what to set"


def test_every_console_script_refuses_unconfigured():
    """Mechanized, not remembered: every entry point in pyproject is wrapped.

    A behavioural sweep would have to invent valid arguments for nine different
    CLIs and would conflate "argparse usage error" (also rc=2) with the refusal.
    The structural assertion is the honest one: the decorator is what converts
    `Unconfigured` into rc=2, so the property to hold is that every shipped entry
    point has it.
    """
    import importlib
    import tomllib

    root = Path(__file__).resolve().parent.parent
    scripts = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    entries = scripts["project"]["scripts"]
    assert len(entries) == 12, (
        "expected exactly the 12 shipped CLIs (x4fresh made 12 on 2026-11-01). This was `>= 9` until 2026-08-26, "
        "which caught a VANISHING entry point but silently accepted a new one — so "
        "adding a CLI passed a guard meant to notice. If you added or removed one, "
        "change this number deliberately.")

    unwrapped = []
    for name, target in entries.items():
        modname, _, func = target.partition(":")
        fn = getattr(importlib.import_module(modname), func)
        if not getattr(fn, "_refuses_unconfigured", False):
            unwrapped.append(f"{name} -> {target}")
    assert not unwrapped, (
        "these entry points would raise a traceback instead of returning rc=2 "
        f"on an unconfigured machine: {unwrapped}")


# --- the one setting that is deliberately NOT a hard refusal ----------------

def test_a_missing_nexus_key_is_recoverable_not_fatal(monkeypatch):
    """The Nexus key is OPTIONAL, and must not join the rc=2 refusal path.

    Every caller catches `NexusError` and degrades to local facts
    (`_modlist.py:203, 377, 521`); `steam_title()` needs no key at all. Turning
    absence into a hard refusal would break offline triage, which is the common
    case. Pinned because 2.5.0 routes every OTHER setting into exactly that
    refusal, and the next person will reasonably wonder why this one differs.
    """
    from x4validate import _nexus
    monkeypatch.setattr(_paths, "value", lambda *names: None)
    with pytest.raises(_nexus.NexusError):
        _nexus.nexus_key()
    assert not issubclass(_nexus.NexusError, _paths.Unconfigured)
    assert not issubclass(_nexus.NexusError, SystemExit)
