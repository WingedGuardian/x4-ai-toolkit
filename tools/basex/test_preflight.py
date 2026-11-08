r"""The preflight must be able to FAIL, on purpose, for each thing it checks.

A precondition check that has only ever been seen to pass is not verification --
it is a green light nobody has tested the bulb of. Every check here is exercised
in BOTH directions, and the refusal code is asserted to be **2** ("this is not
set up"), never **1**, which throughout this toolkit means "the thing you asked
about has findings". Reporting a missing JVM as 1 tells the reader their corpus
is broken when the truth is that they have no Java.

The failures these tests pin were all REPRODUCED before the module was written
(2026-08-24) -- see preflight.py's own docstring for the verbatim messages the
tool used to emit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preflight  # noqa: E402


def _jar(path: Path, version: str = preflight.BASEX_VERSION) -> Path:
    """A minimal jar: a zip whose manifest names *version* -- all `_jar()` reads."""
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF",
                   f"Manifest-Version: 1.0\r\nImplementation-Title: BaseX Core\r\n"
                   f"Implementation-Version: {version}\r\n\r\n")
    return path


# --- jar -------------------------------------------------------------------

def test_jar_missing_is_reported(tmp_path):
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1
    assert "BaseX.jar not found" in problems[0]
    # The message must carry the CURE, not just the symptom.
    assert preflight.BASEX_VERSION in problems[0]
    assert preflight.BASEX_URL in problems[0]


def test_a_complete_basex_install_is_silent(tmp_path):
    _jar(tmp_path / "BaseX.jar")
    (tmp_path / ".basexhome").write_bytes(b"")
    assert preflight.check(["jar"], basex_dir=tmp_path) == []


def test_a_jar_without_the_home_marker_is_reported(tmp_path):
    """MEASURED 2026-08-25, and the reason this check exists at all.

    `.basexhome` is a 0-byte marker shipped in the official BaseX archive. It is
    how BaseX decides that THIS directory is its home. With it, `CREATE DB` writes
    to `<dir>/data`. Without it, BaseX silently relocates its entire home to
    `$HOME/basex` -- proven by running both, same jar, same command:

        jar only            -> $HOME/basex/data/probeA   (queryable, returns 2)
        jar + .basexhome    -> <dir>/data/probeB         (queryable, returns 2)

    Both BUILD fine, which is what makes it dangerous. `_dbpath()` then falls back
    to `<dir>/data`, finds nothing, and reports "the database has not been built"
    over a database that was built and IS queryable. The user's first run becomes:
    run the build (minutes), get told to run the build.
    """
    (tmp_path / "BaseX.jar").write_bytes(b"file")
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1
    assert ".basexhome" in problems[0]


def test_the_missing_marker_message_names_the_relocation(tmp_path):
    """A message saying only "a file is missing" would send the reader looking for
    a corrupt download. The consequence -- the database goes to the home directory
    and every later check disagrees about where it is -- is the part that makes
    the symptom recognisable."""
    (tmp_path / "BaseX.jar").write_bytes(b"file")
    msg = preflight.check(["jar"], basex_dir=tmp_path)[0].lower()
    assert "home" in msg and "data" in msg


# --- db --------------------------------------------------------------------

def test_db_missing_names_the_build_script(tmp_path):
    problems = preflight.check(["db"], basex_dir=tmp_path, db="x4raw")
    assert len(problems) == 1
    # THE point of this check: BaseX's own error never mentions the script.
    assert "build-corpus.sh" in problems[0]


def test_db_missing_names_the_RIGHT_build_script(tmp_path):
    """x4eff is built by a different script than x4raw. Naming the wrong one
    sends the user to a build that cannot produce the database they lack."""
    problems = preflight.check(["db"], basex_dir=tmp_path, db="x4eff")
    assert "build-effective.sh" in problems[0]
    assert "build-corpus.sh" not in problems[0]


def test_db_present_is_silent(tmp_path):
    (tmp_path / "data" / "x4raw").mkdir(parents=True)
    assert preflight.check(["db"], basex_dir=tmp_path, db="x4raw") == []


def test_db_check_without_a_name_is_a_programming_error(tmp_path):
    with pytest.raises(ValueError):
        preflight.check(["db"], basex_dir=tmp_path)


def test_dbpath_follows_the_basex_config_not_the_default(tmp_path):
    """BaseX writes `.basex` on first use and its DBPATH wins over <dir>/data.

    Checking the default when the config says otherwise would report "not built"
    for a database that exists -- looking in a plausible place instead of the
    real one.
    """
    real = tmp_path / "elsewhere"
    (real / "x4raw").mkdir(parents=True)
    (tmp_path / ".basex").write_text(f"DEBUG = false\nDBPATH = {real}\nLANG = English\n",
                                     encoding="utf-8")
    assert preflight._dbpath(tmp_path) == real
    assert preflight.check(["db"], basex_dir=tmp_path, db="x4raw") == []


# --- java ------------------------------------------------------------------

def test_java_absent_is_reported(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    problems = preflight.check(["java"])
    assert len(problems) == 1
    assert "not on PATH" in problems[0]
    assert str(preflight.MIN_JAVA) in problems[0]


@pytest.mark.parametrize("banner,expected_ok", [
    ('openjdk version "17.0.2" 2022-01-18', False),  # BaseX 12.4's floor; 13.0 needs 21
    ('openjdk version "20.0.2"', False),
    ('java version "24.0.1" 2025-04-15', True),
    ('openjdk version "21.0.1"', True),
    ('java version "11.0.20"', False),      # below the floor
    ('java version "1.8.0_402"', False),    # the pre-9 spelling: major is the 2nd field
])
def test_java_version_floor(monkeypatch, banner, expected_ok):
    """Bytecode major 65 means Java 21 REFUSES to be undercut -- an older JVM
    raises UnsupportedClassVersionError rather than running slowly."""
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/java")

    class _Out:
        stderr = banner
        stdout = ""

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _Out())
    problems = preflight.check(["java"])
    assert (problems == []) is expected_ok, (banner, problems)


def test_an_unparseable_java_banner_refuses_rather_than_assumes(monkeypatch):
    """A non-answer must not be rendered as an answer -- in either direction.

    Guessing "probably fine" here would be this register's founding defect: a
    step that could not determine something, reporting success anyway.
    """
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/java")

    class _Out:
        stderr = "some vendor JVM with a banner nobody has seen before"
        stdout = ""

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _Out())
    problems = preflight.check(["java"])
    assert len(problems) == 1
    assert "could not parse" in problems[0]


# --- uv / disk -------------------------------------------------------------

def test_uv_absent_is_reported(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    problems = preflight.check(["uv"])
    assert "uv is not on PATH" in problems[0]


def test_disk_shortfall_is_reported(monkeypatch, tmp_path):
    class _Usage:
        free = 1 * (1024 ** 3)  # 1 GB

    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda p: _Usage())
    problems = preflight.check(["disk"], basex_dir=tmp_path, disk_gb=3.0)
    assert len(problems) == 1
    assert "1.0 GB free" in problems[0]


def test_ample_disk_is_silent(monkeypatch, tmp_path):
    class _Usage:
        free = 500 * (1024 ** 3)

    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda p: _Usage())
    assert preflight.check(["disk"], basex_dir=tmp_path, disk_gb=3.0) == []


# --- the contract ----------------------------------------------------------

def test_unknown_check_is_rejected_loudly(tmp_path):
    """A typo'd check name must not silently check nothing."""
    with pytest.raises(ValueError):
        preflight.check(["jarr"], basex_dir=tmp_path)


def test_require_raises_with_every_problem(tmp_path):
    with pytest.raises(preflight.Unready) as exc:
        preflight.require(["jar", "db"], basex_dir=tmp_path, db="x4raw")
    assert len(exc.value.problems) == 2


def test_cli_refuses_with_2_not_1(tmp_path, capsys):
    """rc 2 = "not configured". rc 1 would say "your corpus has findings", which
    is a different message to whoever reads it -- the same distinction F39 put
    into the x4validate CLIs."""
    rc = preflight.main(["--need", "jar", "--basex-dir", str(tmp_path)])
    assert rc == 2
    assert "not ready" in capsys.readouterr().err


def test_cli_reports_ok_when_satisfied(tmp_path, capsys):
    _jar(tmp_path / "BaseX.jar")
    (tmp_path / ".basexhome").write_bytes(b"")
    rc = preflight.main(["--need", "jar", "--basex-dir", str(tmp_path)])
    assert rc == 0
    assert "preflight OK" in capsys.readouterr().out


def test_every_check_name_is_reachable_from_the_cli():
    """A check that exists but no caller can request is dead weight; a CLI choice
    with no implementation is a crash. Pin them to each other."""
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--need", nargs="+", choices=list(preflight.CHECKS))
    for name in preflight.CHECKS:
        assert p.parse_args(["--need", name]).need == [name]


# --- build-then-swap (4.1.1 review): the gap between the two renames is not "never built" -------------------------
@pytest.mark.parametrize("marker", ["swap-x4raw.json", "build-x4raw.lock"])
def test_a_missing_database_during_a_swap_or_build_is_not_called_unbuilt(tmp_path, marker):
    (tmp_path / marker).write_text("{}", encoding="utf-8")
    msg = preflight._db(tmp_path, "x4raw")
    assert msg and "has not been built" not in msg and "dbswap.py recover --db x4raw" in msg, msg


def test_TWIN_a_missing_database_with_no_swap_is_still_unbuilt(tmp_path):
    assert "has not been built" in preflight._db(tmp_path, "x4raw")


# --- BaseX 13.0 (4.1.1): the shipped jar, its Java floor, and a jar of the wrong version ----------------------------
def test_the_pins_are_read_from_the_jar_that_ships():
    """The version and the Java floor are facts about the vendored jar, not choices. A jar swapped without the
    constants (or the reverse) must go red here, not at a user's UnsupportedClassVersionError."""
    import struct
    import zipfile
    jar = preflight.HERE / "basex" / "BaseX.jar"
    if not jar.is_file():
        pytest.skip(f"no vendored jar at {jar}")
    with zipfile.ZipFile(jar) as z:
        manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8")
        major = struct.unpack(">H", z.read("org/basex/BaseX.class")[6:8])[0]
    assert f"Implementation-Version: {preflight.BASEX_VERSION}\r\n" in manifest
    assert f"Build-Jdk-Spec: {preflight.MIN_JAVA}\r\n" in manifest
    assert major == preflight.CLASS_MAJOR == preflight.MIN_JAVA + 44, (major, preflight.MIN_JAVA)
    assert (preflight.BASEX_VERSION, preflight.MIN_JAVA) == ("13.0", 21)


def test_a_basex_12_4_jar_is_refused_with_the_cure(tmp_path):
    _jar(tmp_path / "BaseX.jar", "12.4")
    (tmp_path / ".basexhome").write_bytes(b"")
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1, problems
    msg = problems[0]
    assert "BaseX 12.4 found" in msg and "needs BaseX 13.0" in msg, msg
    assert preflight.BASEX_JAR_URL in msg, msg
    assert "Java 21" in msg, msg


@pytest.mark.parametrize("version", ["13.0.1", "13", "14.0", "12.4"])
def test_only_the_exact_version_passes(tmp_path, version):
    """Refused for the RIGHT reason: the version refusal naming what was found, not any other problem."""
    _jar(tmp_path / "BaseX.jar", version)
    (tmp_path / ".basexhome").write_bytes(b"")
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1, (version, problems)
    assert problems[0].startswith(f"BaseX {version} found at "), (version, problems)
    assert f"needs BaseX {preflight.BASEX_VERSION}" in problems[0], (version, problems)


def test_TWIN_the_exact_version_passes(tmp_path):
    _jar(tmp_path / "BaseX.jar", "13.0")
    (tmp_path / ".basexhome").write_bytes(b"")
    assert preflight.check(["jar"], basex_dir=tmp_path) == []


@pytest.mark.parametrize("content", [b"not a jar", b""])
def test_an_unreadable_jar_refuses_rather_than_passes(tmp_path, content):
    """A file that is not a jar is a non-answer about the version: refuse, never read it as 13.0."""
    (tmp_path / "BaseX.jar").write_bytes(content)
    (tmp_path / ".basexhome").write_bytes(b"")
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1 and "could not read" in problems[0], problems


def test_a_jar_whose_manifest_names_no_version_refuses(tmp_path):
    import zipfile
    with zipfile.ZipFile(tmp_path / "BaseX.jar", "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
    (tmp_path / ".basexhome").write_bytes(b"")
    problems = preflight.check(["jar"], basex_dir=tmp_path)
    assert len(problems) == 1 and "names no Implementation-Version" in problems[0], problems


# --- the real-jar tests' skip reason (4.1.1 review: they skipped only when java was ABSENT, and failed on Java 17-20)

def _java_banner(monkeypatch, banner):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/java")

    class _Out:
        stderr = banner
        stdout = ""

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _Out())


@pytest.mark.parametrize("banner, found", [('openjdk version "17.0.2" 2022-01-18', "17"),
                                           ('openjdk version "20.0.2"', "20"),
                                           ('java version "1.8.0_402"', "8")])
def test_a_java_below_the_floor_is_a_skip_reason_naming_both_versions(tmp_path, monkeypatch, banner, found):
    _java_banner(monkeypatch, banner)
    jar = _jar(tmp_path / "BaseX.jar")
    assert preflight.real_basex_unavailable(jar) == f"needs Java {preflight.MIN_JAVA}+ (found {found})"


@pytest.mark.parametrize("banner", ['openjdk version "21.0.1"', 'java version "24.0.1" 2025-04-15'])
def test_TWIN_java_at_or_above_the_floor_with_the_jar_is_no_skip(tmp_path, monkeypatch, banner):
    _java_banner(monkeypatch, banner)
    assert preflight.real_basex_unavailable(_jar(tmp_path / "BaseX.jar")) is None


def test_TWIN_no_jar_and_no_java_are_each_a_skip_reason(tmp_path, monkeypatch):
    _java_banner(monkeypatch, 'openjdk version "21.0.1"')
    assert "jar" in preflight.real_basex_unavailable(tmp_path / "BaseX.jar")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    assert preflight.real_basex_unavailable(_jar(tmp_path / "BaseX.jar")) == \
        f"needs Java {preflight.MIN_JAVA}+ (none on PATH)"


def test_TWIN_an_unparseable_banner_is_a_skip_reason_never_a_pass(tmp_path, monkeypatch):
    _java_banner(monkeypatch, "some vendor JVM")
    why = preflight.real_basex_unavailable(_jar(tmp_path / "BaseX.jar"))
    assert why and why.startswith(f"needs Java {preflight.MIN_JAVA}+ (version unreadable"), why
