"""X4_STAGING_DIRS: an inert download collection inside the game install takes FILE WRITES.

The setting names folders the user keeps as DIRECT children of the game folder (outside
extensions/), where downloaded mod packages wait. Unset (the default), every verdict is the
one before the setting existed. Set, only file writes strictly beneath an accepted folder
change from deny to allow; deletes, moves out, the folder itself, archives, links and every
earlier protected root keep their verdicts, and an entry that fails the placement rules is
ignored with a warning in the refusal and in the session-start banner, never silently.
Every write is judged single AND batch, and the two must agree.
"""
import os
import subprocess
import time

import pytest

from _layout import claude_hooks_source
from test_file_guard_batch import guard  # noqa: F401  (the shared real-backend fixture)

STAGE = "Mod staging"

CASES = [
    (f"{STAGE}/Deferred/Example/package.zip", "allow"),
    (f"{STAGE}/Deferred/Example/README.txt", "allow"),
    (f"{STAGE}/Deferred/Example/content.xml", "advise"),
    (f"{STAGE}/Deferred/Example/libraries/jobs.xml", "allow"),
    (f"{STAGE}/top-level-note.txt", "allow"),
    (f"{STAGE}-other/package.zip", "deny"),          # lookalike sibling: no directory boundary
    (f"{STAGE}x/package.zip", "deny"),
    (f"Other/{STAGE}/package.zip", "deny"),           # nested, not a direct child
    (f"{STAGE}/../libraries/wares.xml", "deny"),      # traversal out of the folder
    (f"{STAGE}/../extensions/mod/content.xml", "deny"),
    (f"{STAGE}/../extensions/ego_dlc_test/content.xml", "deny"),
    (f"{STAGE}/Deferred/Example/../../../01.cat", "deny"),
    (f"{STAGE}/Deferred/Example/ext_01.dat", "deny"),  # archives stay forbidden
    (f"{STAGE}/Deferred/Example/ext_01.CAT", "deny"),
    ("01.cat", "deny"),
    ("libraries/wares.xml", "deny"),
    ("extensions/ego_dlc_test/content.xml", "deny"),
    ("extensions/mod/content.xml", "deny"),          # a deployed copy (mods root is separate)
]


def _both(g, path):
    """The single and batch verdicts for one write; they must agree."""
    single = g.verdict_for("write", None, None, str(path), save_qualified=False)
    batch = g.write_verdicts([str(path)], deadline=time.monotonic() + 35, save_qualified=False)[0]
    assert single == batch, (path, single, batch)
    assert not single["inert"], single
    return single


def _cases(g, game):
    paths = [str(game / p) for p, _ in CASES]
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    assert len(bulk) == len(CASES)
    out = []
    for path, batch in zip(paths, bulk):
        single = g.verdict_for("write", None, None, path, save_qualified=False)
        assert single == batch, (path, single, batch)
        assert not single["inert"], single
        out.append(single["decision"])
    return out


@pytest.mark.parametrize("form", ["name", "absolute"])
def test_configured_staging_single_and_batch(guard, monkeypatch, form):
    g, r = guard
    value = STAGE if form == "name" else str(r["X4_GAME"] / STAGE)
    monkeypatch.setenv("X4_STAGING_DIRS", value)
    assert _cases(g, r["X4_GAME"]) == [d for _, d in CASES]


def test_unset_setting_changes_nothing(guard, monkeypatch):
    """The twin: without the setting every staging write keeps the game-install refusal."""
    g, r = guard
    monkeypatch.delenv("X4_STAGING_DIRS", raising=False)
    expected = ["deny" if p.startswith(STAGE) else d for p, d in CASES]
    assert _cases(g, r["X4_GAME"]) == expected
    monkeypatch.setenv("X4_STAGING_DIRS", "")
    assert _cases(g, r["X4_GAME"]) == expected


def test_several_entries_and_a_rejected_one(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", f" extensions ; {STAGE} ;; Second ")
    assert _both(g, r["X4_GAME"] / STAGE / "a.zip")["decision"] == "allow"
    assert _both(g, r["X4_GAME"] / "Second/a.zip")["decision"] == "allow"
    v = _both(g, r["X4_GAME"] / "libraries/wares.xml")
    assert v["decision"] == "deny"
    assert "X4_STAGING_DIRS" in v["reason"] and "'extensions'" in v["reason"], v


@pytest.mark.parametrize("entry", [
    "extensions", "Extensions", "EXTENSIONS.", "extensions/", "extensions/mod",
    "ego_dlc_test", "libraries", "md", "aiscripts", "t", "voice-l044", ".claude", "reference",
    "videos", "MouseCursors", "support",
    "Other/Nested", "..", ".", "", ])
def test_rejected_entries_grant_nothing_and_say_so(guard, monkeypatch, entry):
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", f"{entry};" if entry == "" else entry)
    target = r["X4_GAME"] / (entry if entry not in ("", ".", "..") else "x") / "file.txt"
    v = _both(g, target)
    assert v["decision"] in ("deny", "ask"), v          # an earlier rule may decide (extensions/)
    probe = _both(g, r["X4_GAME"] / "libraries/wares.xml")   # a plain game-install refusal
    assert probe["decision"] == "deny", probe
    if entry.strip():
        assert "X4_STAGING_DIRS" in probe["reason"] and "IGNORED" in probe["reason"], probe
    else:
        assert "X4_STAGING_DIRS" not in probe["reason"], probe


def test_entry_outside_the_game_is_ignored_with_a_warning(guard, monkeypatch, tmp_path):
    g, r = guard
    outside = tmp_path / "elsewhere"
    monkeypatch.setenv("X4_STAGING_DIRS", str(outside))
    v = _both(g, r["X4_GAME"] / "libraries/wares.xml")
    assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"], v


def test_folder_holding_archives_is_rejected(guard, monkeypatch):
    g, r = guard
    stage = r["X4_GAME"] / STAGE
    stage.mkdir(parents=True)
    (stage / "08.CAT").write_bytes(b"")
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    v = _both(g, stage / "note.txt")
    assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"], v


@pytest.mark.parametrize("rel, rejected", [
    ("intro.mkv.sig", True),                          # game-signed content at the top level
    ("a/b/c/utf8_64.dll.sig", True),                  # 4 components down: the deepest the game ships
    ("a/b/c/d/utf8_64.dll.sig", False),               # 5 down: past the bound (a deeper glob goes red)
    ("Deferred/Mod/ext/x/y/content.sig", False),      # 6 components: a staged mod's own signature
    ("Deferred/notes.SIG.txt", False),                # not a .sig file
])
def test_folder_holding_signed_game_content_is_rejected(guard, monkeypatch, rel, rejected):
    """Folders the game ships (videos/, mousecursors/, ui/...) hold Egosoft .sig files. A bounded
    scan (4 path components below the entry) rejects them structurally, whatever their name."""
    g, r = guard
    stage = r["X4_GAME"] / "Signed"
    f = stage / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"")
    monkeypatch.setenv("X4_STAGING_DIRS", "Signed")
    v = _both(g, stage / "probe.txt")
    if rejected:
        assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"] and ".sig" in v["reason"], v
    else:
        assert v["decision"] == "allow", v


@pytest.mark.parametrize("unlistable", ["", "Deferred/Mod"])
def test_a_folder_the_scan_cannot_list_rejects_the_entry(guard, monkeypatch, unlistable):
    """Fail closed: a folder within the scanned levels that cannot be listed could hide a .sig, and a
    glob over it returns nothing -- which read as "no game content" (4.2.0 review M6)."""
    if os.name == "nt":
        pytest.skip("an unlistable folder on Windows needs an ACL deny, which this suite never applies; "
                    "the rule is a builtin -r/-x test, covered on POSIX CI")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: every folder is listable, so no fixture can express the case")
    g, r = guard
    stage = r["X4_GAME"] / "Locked"
    target = stage / unlistable if unlistable else stage
    (stage / "Deferred/Mod/inner").mkdir(parents=True)
    (stage / "readme.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("X4_STAGING_DIRS", "Locked")
    probe = r["X4_GAME"] / "libraries/wares.xml"
    target.chmod(0)
    try:
        assert not os.access(target, os.R_OK | os.X_OK), "fixture is still listable"
        v = _both(g, probe)
        assert v["decision"] == "deny" and "cannot list" in v["reason"], v
    finally:
        target.chmod(0o755)
    v = _both(g, stage / "note.txt")                     # TWIN: listable again -> accepted
    assert v["decision"] == "allow", v


def test_folder_overlapping_a_configured_root_is_rejected(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("X4_REFERENCE", str(r["X4_GAME"] / STAGE / "reference"))
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    v = _both(g, r["X4_GAME"] / STAGE / "note.txt")
    assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"], v


def test_folder_inside_a_configured_root_is_rejected(guard, monkeypatch):
    """The other overlap direction: the entry IS (or is inside) a configured root that lives in
    the game folder -- here the backups store, which no name rule covers."""
    g, r = guard
    monkeypatch.setenv("X4_BACKUPS", str(r["X4_GAME"] / "Agent backups"))
    monkeypatch.setenv("X4_STAGING_DIRS", "Agent backups")
    v = _both(g, r["X4_GAME"] / "Agent backups" / "note.txt")
    assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"], v


def test_nested_reference_and_profile_still_protected(guard, monkeypatch):
    """Even were they accepted, the earlier rules decide first."""
    g, r = guard
    stage = r["X4_GAME"] / STAGE
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    monkeypatch.setenv("X4_PROFILE", str(stage / "profile"))
    v = _both(g, stage / "profile/config.xml")
    assert v["decision"] in ("ask", "deny"), v


def test_deletes_and_moves_gain_no_exemption(guard, monkeypatch):
    g, r = guard
    game = r["X4_GAME"]
    stage = game / STAGE / "Deferred/Example"
    stage.mkdir(parents=True)
    (stage / "README.txt").write_text("staged", encoding="utf-8")
    paths = [game, game / "extensions", game / "extensions/ego_dlc_test", game / STAGE,
             stage, stage / "README.txt"]
    def verdicts():
        return [(k, g.verdict_for(k, None, None, str(p), save_qualified=False)) for k in ("delete", "move")
                for p in paths]
    monkeypatch.delenv("X4_STAGING_DIRS", raising=False)
    before = verdicts()
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    after = verdicts()
    for (kind, b), (_, a) in zip(before, after):
        assert not a["inert"] and a["decision"] == b["decision"] == "deny", (kind, a, b)


def test_existing_file_hardlink_and_directory(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    stage = r["X4_GAME"] / STAGE / "Deferred/Example"
    stage.mkdir(parents=True)
    ordinary = stage / "README.txt"
    ordinary.write_text("staged", encoding="utf-8")
    protected = r["X4_GAME"] / "base.xml"
    protected.write_text("protected", encoding="utf-8")
    linked = stage / "alias.xml"
    try:
        os.link(protected, linked)
    except OSError as e:
        pytest.skip(f"this filesystem cannot make a hard link here: {e}")
    assert _both(g, ordinary)["decision"] == "allow"
    assert _both(g, linked)["decision"] == "deny"
    assert _both(g, stage)["decision"] == "deny"           # an existing directory is no file


def _directory_link(link, target):
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip(f"could not create a junction: {result.stderr.strip()}")
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("whole_root", [False, True])
def test_link_cannot_whitelist_game_data(guard, monkeypatch, whole_root):
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    stage = r["X4_GAME"] / STAGE
    link = stage if whole_root else stage / "redirect"
    _directory_link(link, r["X4_GAME"] / "libraries")
    v = _both(g, link / "wares.xml")
    assert v["decision"] == "deny", v
    if whole_root:
        assert "X4_STAGING_DIRS" in v["reason"], v


def test_file_symlink_to_game_data_is_not_exempt(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    stage = r["X4_GAME"] / STAGE
    stage.mkdir(parents=True)
    real = r["X4_GAME"] / "libraries/wares.xml"
    real.parent.mkdir(parents=True)
    real.write_text("x", encoding="utf-8")
    try:
        (stage / "wares.xml").symlink_to(real)
    except OSError as e:
        pytest.skip(f"no symlink privilege on this machine: {e}")
    assert _both(g, stage / "wares.xml")["decision"] == "deny"


def test_legacy_layout_without_game_root_is_unchanged(guard, monkeypatch, tmp_path):
    """No X4_GAME: the name backstop still denies, and the entry is reported as unplaceable."""
    g, r = guard
    monkeypatch.delenv("X4_GAME", raising=False)
    monkeypatch.delenv("X4_EXTENSIONS", raising=False)
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    v = _both(g, tmp_path / "X4 Foundations" / STAGE / "a.zip")
    assert v["decision"] == "deny" and "X4_STAGING_DIRS" in v["reason"], v


def _banner(g, env):
    hooks = claude_hooks_source(why="the session-start config banner")
    script = '. "$HOOK_DIR/_x4-env.sh"; x4_config_banner'
    env = dict(env, HOOK_DIR=str(hooks).replace("\\", "/"))
    bash, why = g.resolve_bash()
    if not bash:
        pytest.skip(f"no bash for the banner check: {why}")
    p = subprocess.run([bash, "-c", script], env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_session_banner_names_an_ignored_entry(guard, monkeypatch):
    g, r = guard
    (r["X4_GAME"]).mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "X4_STAGING_DIRS"}
    assert "X4_STAGING_DIRS" not in _banner(g, env)
    assert "X4_STAGING_DIRS" not in _banner(g, dict(env, X4_STAGING_DIRS=STAGE))
    out = _banner(g, dict(env, X4_STAGING_DIRS=f"{STAGE};libraries"))
    assert "X4_STAGING_DIRS" in out and "'libraries'" in out and STAGE not in out.split("ignored", 1)[-1], out


def test_native_hook_entry_agrees(guard, monkeypatch):
    """Claude Code runs protect-files.sh WITHOUT the backend flag: the save dispatcher reaches the
    same policy. A staging write is allowed there too, and a typed delete still refused."""
    g, r = guard
    monkeypatch.setenv("X4_STAGING_DIRS", STAGE)
    target = str(r["X4_GAME"] / STAGE / "Deferred/Example/package.zip")
    w = g.verdict_for("write", None, None, target)
    d = g.verdict_for("delete", None, None, target)
    assert not w["inert"] and w["decision"] == "allow", w
    assert not d["inert"] and d["decision"] == "deny", d
    monkeypatch.delenv("X4_STAGING_DIRS")
    w0 = g.verdict_for("write", None, None, target)
    assert not w0["inert"] and w0["decision"] == "deny", w0
