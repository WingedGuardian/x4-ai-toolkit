"""One rule for where x4live journals may live (review r1-5, r1-6).

A journal must not overlap the PERSONAL TREE (the parent of the profile folder, which
holds every profile, its saves and uidata), the game install or the reference folder.
Before this, the persistence modules compared against the one profile folder only, and
`x4live automation --journal` compared against nothing.
"""
import copy
from pathlib import Path

import pytest

from x4validate import _livetrees as T
from x4validate import _paths
from x4validate._liveactions import ActionRefused
from x4validate._liveautomation import AutomationClient
from x4validate._livepersistence import PersistenceUnavailable, SlotVerifier


def test_overlap_rule():
    assert T.overlaps(Path("C:/a/b"), Path("C:/a"))
    assert T.overlaps(Path("C:/a"), Path("C:/a/b"))
    assert T.overlaps(Path("C:/a"), Path("C:/a"))
    assert not T.overlaps(Path("C:/a/b"), Path("C:/a/c"))
    assert T.personal_tree(Path("C:/Users/tester/Documents/Egosoft/X4/12345678")) == \
        Path("C:/Users/tester/Documents/Egosoft/X4").resolve()


def _verifier(profile, journal):
    return SlotVerifier(profile, journal, identity=lambda: {}, catalogue=lambda: {},
                        profile_peer=lambda: {}, clock=lambda: 1.0)


def test_persistence_journal_beside_the_profile_in_the_personal_tree_refuses(tmp_path):
    personal = tmp_path / "X4"
    profile = personal / "12345678"
    (profile / "save").mkdir(parents=True)
    journal = personal / "journal"
    journal.mkdir()
    with pytest.raises(PersistenceUnavailable, match="overlap"):
        _verifier(profile, journal)


def test_persistence_journal_outside_the_personal_tree_is_accepted(tmp_path):
    # Twin: a journal next to, not inside, the personal tree is fine.
    profile = tmp_path / "X4" / "12345678"
    (profile / "save").mkdir(parents=True)
    journal = tmp_path / "journal"
    journal.mkdir()
    _verifier(profile, journal)


class _NoPipe:
    @property
    def _h(self):
        raise AssertionError("the pipe was used before the journal was checked")


@pytest.mark.parametrize("which", ["reference", "game", "personal", "image"])
def test_automation_journal_overlapping_a_protected_tree_refuses_before_the_pipe(tmp_path, monkeypatch, which):
    ref, game, prof = tmp_path / "ref", tmp_path / "game", tmp_path / "X4" / "12345678"
    for folder in (ref, game, prof):
        folder.mkdir(parents=True)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    monkeypatch.setattr(_paths, "game_root", lambda: game)
    monkeypatch.setattr(_paths, "profile", lambda: prof)
    image = tmp_path / "other" / "X4.exe"
    journal = {"reference": ref / "j", "game": game / "j", "personal": prof.parent / "j",
               "image": image.parent / "j"}[which]
    journal.mkdir(parents=True)
    with pytest.raises(ActionRefused, match="overlap"):
        AutomationClient.connect(_NoPipe(), journal, image=str(image), profile_id="12345678",
                                 build="f" * 64, revision="a" * 64, mode="fresh")


def test_automation_journal_elsewhere_reaches_the_pipe(tmp_path, monkeypatch):
    # Twin: a journal outside every protected tree passes the check and reaches the pipe.
    ref, game, prof = tmp_path / "ref", tmp_path / "game", tmp_path / "X4" / "12345678"
    for folder in (ref, game, prof):
        folder.mkdir(parents=True)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    monkeypatch.setattr(_paths, "game_root", lambda: game)
    monkeypatch.setattr(_paths, "profile", lambda: prof)
    journal = tmp_path / "journals"
    journal.mkdir()
    with pytest.raises(AssertionError, match="pipe was used"):
        AutomationClient.connect(_NoPipe(), journal, image=str(tmp_path / "other" / "X4.exe"),
                                 profile_id="12345678", build="f" * 64, revision="a" * 64, mode="fresh")


# Second review batch.
# I1: the save/load modules compared only against the personal tree.
def test_persistence_journal_under_the_configured_game_root_refuses(tmp_path, monkeypatch):
    game = tmp_path / "game"
    journal = game / "journal"
    journal.mkdir(parents=True)
    monkeypatch.setattr(_paths, "game_root", lambda: game)
    profile = tmp_path / "X4" / "12345678"
    (profile / "save").mkdir(parents=True)
    with pytest.raises(PersistenceUnavailable, match="overlap"):
        _verifier(profile, journal)


def test_persistence_journal_under_the_configured_reference_refuses(tmp_path, monkeypatch):
    ref = tmp_path / "reference"
    journal = ref / "journal"
    journal.mkdir(parents=True)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    profile = tmp_path / "X4" / "12345678"
    (profile / "save").mkdir(parents=True)
    with pytest.raises(PersistenceUnavailable, match="overlap"):
        _verifier(profile, journal)


# Minor 1: one rule. No live module hand-rolls a personal-tree or profile check.
def test_no_hand_rolled_personal_tree_check_outside_livetrees():
    root = Path(T.__file__).resolve().parent
    hits = []
    for f in sorted(root.glob("_live*.py")):
        if f.name == "_livetrees.py":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ("is_relative_to" in line and ("profile" in line or "personal" in line)) or "personal_tree(" in line:
                hits.append(f"{f.name}:{n}")
    assert hits == []


def test_journal_containing_the_personal_tree_is_refused_both_directions(tmp_path):
    # The dialog modules checked one direction: a journal ABOVE every profile was accepted.
    profile = tmp_path / "Egosoft" / "X4" / "12345678"
    profile.mkdir(parents=True)
    assert T.journal_refusal(tmp_path / "Egosoft", profile=profile)
    assert T.journal_refusal(profile / "j", profile=profile)
    assert T.journal_refusal(tmp_path / "elsewhere", profile=profile) is None


# Minor 2: a relative image is resolved, and what could not be determined is reported.
def test_relative_image_is_resolved_and_checked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "game").mkdir()
    roots = T.protected_roots(image="game/X4.exe", profile=tmp_path / "X4" / "1")
    assert (tmp_path / "game").resolve() in roots.values()


def test_unconfigured_roots_are_reported_not_silently_skipped(monkeypatch):
    monkeypatch.setattr(_paths, "game_root", lambda: None)
    monkeypatch.setattr(_paths, "reference", lambda: None)
    monkeypatch.setattr(_paths, "profile", lambda: None)
    assert set(T.unchecked_roots()) == {"game install", "configured game root", "reference folder", "personal tree"}
    assert T.unchecked_roots(image="C:/Users/tester/X4/X4.exe", profile="C:/Users/tester/X4p/12345678") == \
        ["configured game root", "reference folder"]


def test_automation_cli_names_the_journal_trees_it_could_not_check(tmp_path, monkeypatch, capsys):
    # Minor 2: the CLI says which protected trees were not determined, instead of checking nothing quietly.
    from x4validate import _liveautomationcli as AC
    monkeypatch.setattr(_paths, "game_root", lambda: None)
    monkeypatch.setattr(_paths, "reference", lambda: None)
    monkeypatch.setattr(_paths, "profile", lambda: None)

    class Stop(Exception):
        pass

    def opener(pipe, timeout):
        raise Stop()
    with pytest.raises(Stop):
        AC.main(["--journal", str(tmp_path), "--image", "C:/Users/tester/X4/X4.exe", "--profile-id", "12345678",
                 "--build", "f" * 64, "--revision", "a" * 64, "--mode", "fresh", "identity"], open_pipe=opener)
    err = capsys.readouterr().err
    assert "journal check" in err and "configured game root" in err and "personal tree" in err
