r"""Fail BEFORE the long work, with a message that names the real cause.

WHY THIS EXISTS (every failure below was REPRODUCED, 2026-08-24, not reasoned
about). Nothing checked anything, so the three ways a first run goes wrong all
surfaced as an error blaming the wrong component -- or blaming nothing at all:

  no java on PATH      ask.py -> "error: BaseX query failed: [WinError 2] The
                       system cannot find the file specified"  (rc 2)
                       It blames BaseX for a missing JVM and does not even name
                       the file it could not find. build-corpus.sh is worse: it
                       runs stage.py to completion FIRST (build-corpus.sh:49)
                       and does not touch java until :54, so the whole staging
                       pass is spent before anything can fail.

  no BaseX.jar         "Could not find or load main class org.basex.BaseX"
                       -- accurate for a Java developer, meaningless to a user
                       who has never heard of a classpath.

  DB never built       "Stopped at <abs path>, 1/17: [FODC0002] Resource
                       '<abs path>/x4raw' not found."  (rc 2)
                       The one line that says "run build-corpus.sh" lives on
                       ask.py's ZERO-RESULT path, which an unbuilt DB can never
                       reach. Worst first-run experience in the tool.

THE JAVA FLOOR IS MEASURED, NOT ASSUMED. Read out of the shipped jar itself
(BaseX 13.0, since toolkit 4.1.1): `Implementation-Version: 13.0`,
`Build-Jdk-Spec: 21`, and `org/basex/BaseX.class` carries bytecode major **65**.
Major 65 IS Java 21 -- an older JVM does not run it slowly, it refuses to load
the class at all. (`Main-Class` is `BaseXGUI`, which is why every caller uses
`java -cp BaseX.jar org.basex.BaseX` and never `java -jar`.) Until 4.1.0 the jar
was 12.4: Java 17, major 61. `test_the_pins_are_read_from_the_jar_that_ships`
holds these constants to the vendored jar.

THE JAR VERSION IS CHECKED EXACTLY. The jar's manifest must say
`Implementation-Version: 13.0`; any other version, or a file whose manifest
cannot be read, is refused. The toolkit's BaseX facts (the dbswap rename
semantics, the lock texts, the error texts) are MEASURED on that version only, so
a different jar is an untested configuration, not a compatible one.

Stdlib only, and it deliberately does NOT import x4validate: this must be able
to report "you have no JVM" on a machine where nothing else is configured
either. A diagnostic that needs the world to be healthy cannot diagnose.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: Bytecode major 65 = Java 21. See the module docstring -- read from the jar, not chosen.
MIN_JAVA = 21
CLASS_MAJOR = 65
BASEX_VERSION = "13.0"
BASEX_URL = "https://basex.org/download/"
#: The official jar of exactly BASEX_VERSION (basex.org/download links it). SHA-256 of the copy the toolkit ships:
#: 022bc5a4ec057591d6c577b3e0c1ef6f0c71373f68ddb5cad3d3b2afb2c83a8d (basex.org publishes no checksum).
BASEX_JAR_URL = "https://files.basex.org/releases/13.0/BaseX130.jar"
#: 0-byte upstream marker pinning BaseX's home to the directory holding it.
#: Absent, BaseX relocates to $HOME/basex and build/verify disagree silently.
HOME_MARKER = ".basexhome"

#: Rough working-set for a full corpus build (index + transient staging). The
#: index alone MEASURED 2.8 GB on 2026-08-24; the staging tree is transient but
#: concurrent with it.
DEFAULT_DISK_GB = 3.0

CHECKS = ("java", "jar", "db", "uv", "disk")


class Unready(RuntimeError):
    """Preconditions are not met. Carries the human-readable problems."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def _java() -> str | None:
    exe = shutil.which("java")
    if exe is None:
        return ("java is not on PATH. BaseX is a Java program, so nothing here can run "
                f"without a JVM.\n    Install Java {MIN_JAVA} or newer (Temurin, Oracle "
                "JDK, or your distro openjdk package), then re-run.\n    Without this "
                "you get 'BaseX query failed: [WinError 2]', which blames BaseX for a "
                "missing JVM.")
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                             check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"java was found at {exe} but could not be run ({exc})."
    # Both spellings occur: `java version "24.0.1"` and `openjdk version "17.0.2"`,
    # plus the pre-9 form `java version "1.8.0_402"` whose major is the SECOND field.
    blob = (out.stderr or "") + (out.stdout or "")
    m = re.search(r'version\s+"(\d+)(?:\.(\d+))?', blob)
    if not m:
        # Do NOT guess a pass. An unparseable banner is a non-answer, and this
        # module exists so a non-answer cannot read as an answer.
        first = blob.strip().splitlines()[0] if blob.strip() else "(no output)"
        return (f"java at {exe} printed a version banner this check could not parse:\n"
                f"    {first}\n"
                f"    Refusing to assume it is >= {MIN_JAVA}. Check by hand with "
                f"`java -version`.")
    major = int(m.group(1))
    if major == 1:  # the 1.8 spelling -> 8
        major = int(m.group(2) or 0)
    if major < MIN_JAVA:
        return (f"java at {exe} is version {major}; BaseX {BASEX_VERSION} needs Java "
                f"{MIN_JAVA} or newer.\n    Its classes are bytecode major {CLASS_MAJOR}, so an "
                f"older JVM refuses to load them outright -- you would get "
                f"UnsupportedClassVersionError, not a slow run.")
    return None


def _jar(basex_dir: Path) -> str | None:
    jar = basex_dir / "BaseX.jar"
    if not jar.is_file():
        return (f"BaseX.jar not found at {jar}\n"
                f"    It ships with the toolkit: reinstall it, or download BaseX {BASEX_VERSION} "
                f"({BASEX_JAR_URL}, listed at {BASEX_URL}) and save it there as BaseX.jar. "
                f"The jar alone is enough -- lib/ is NOT needed (verified).\n"
                f"    Without it java reports 'Could not find or load main class "
                f"org.basex.BaseX', which does not say what to do about it.")
    if not (basex_dir / HOME_MARKER).is_file():
        return (f"{HOME_MARKER} is missing from {basex_dir}\n"
                f"    It is a 0-byte marker shipped in the BaseX archive, and it is how "
                f"BaseX decides that this directory is its home.\n"
                f"    Without it BaseX relocates its home to $HOME/basex, so the database "
                f"is built into $HOME/basex/data while every check here looks in "
                f"{basex_dir / 'data'} -- the build SUCCEEDS and is then reported as "
                f"never built (MEASURED both directions, 2026-08-25).\n"
                f"    Fix:  touch {basex_dir / HOME_MARKER}")
    return _jar_version(jar)


def _jar_version(jar: Path) -> str | None:
    """Refuse any jar that is not exactly BASEX_VERSION -- including one whose version cannot be read."""
    import zipfile
    try:
        with zipfile.ZipFile(jar) as z:
            manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return (f"could not read the BaseX version from {jar} ({exc}).\n"
                f"    It is not a readable jar, so its version is unknown; this toolkit needs BaseX "
                f"{BASEX_VERSION}.\n    Replace it with the jar the toolkit ships, or {BASEX_JAR_URL}")
    m = re.search(r"^Implementation-Version:[ \t]*(\S+)[ \t]*\r?$", manifest, re.M)
    if m is None:
        return (f"{jar} names no Implementation-Version in its manifest, so it cannot be told apart "
                f"from another BaseX.\n    This toolkit needs BaseX {BASEX_VERSION}: replace it with "
                f"the jar the toolkit ships, or {BASEX_JAR_URL}")
    found = m.group(1)
    if found == BASEX_VERSION:
        return None
    return (f"BaseX {found} found at {jar}; this toolkit needs BaseX {BASEX_VERSION} "
            f"(Java {MIN_JAVA} or newer).\n"
            f"    The jar ships with the toolkit, so an update that left the old jar behind is the usual cause: "
            f"reinstall the toolkit, or download {BASEX_JAR_URL} and save it as {jar}.\n"
            f"    Databases built by BaseX 12.4 open in {BASEX_VERSION}; nothing needs rebuilding for the "
            f"version change itself.")


def _dbpath(basex_dir: Path) -> Path:
    """Where BaseX keeps its databases.

    Defaults to <basex_dir>/data, but BaseX writes a `.basex` config on first use
    and its DBPATH wins. Reading it rather than assuming the default is the
    difference between checking the real location and checking a plausible one.
    """
    cfg = basex_dir / ".basex"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == "DBPATH" and val.strip():
                return Path(val.strip())
    return basex_dir / "data"


def _db(basex_dir: Path, db: str) -> str | None:
    path = _dbpath(basex_dir) / db
    if path.is_dir():
        return None
    # Build-then-swap (dbswap.py): between its two renames the live name is briefly absent, and a killed swap can
    # leave it absent. Calling that "never built" sends the reader to a full rebuild; the journal or build lock
    # says which it is.
    busy = [n for n in (f"swap-{db}.json", f"build-{db}.lock") if (basex_dir / n).exists()]
    if busy:
        return (f"the '{db}' database is absent because a swap is in progress or was interrupted "
                f"({', '.join(busy)} present).\n"
                f"    Wait for a running build, or finish an interrupted swap:\n"
                f"    cd tools/x4validate && uv run python ../basex/dbswap.py recover --db {db}")
    script = "build-corpus.sh" if db == "x4raw" else "build-effective.sh"
    return (f"the '{db}' database has not been built (nothing at {path}).\n"
            f"    Run:  bash {script}\n"
            f"    Without it BaseX reports \"[FODC0002] Resource '...{db}' not found\", "
            f"which never mentions the build script.")


def _uv() -> str | None:
    if shutil.which("uv"):
        return None
    return ("uv is not on PATH. The build scripts invoke x4validate through "
            "`uv run python`.\n    Install it from https://docs.astral.sh/uv/")


def _disk(where: Path, need_gb: float) -> str | None:
    try:
        free_gb = shutil.disk_usage(where).free / (1024 ** 3)
    except OSError as exc:
        return f"could not determine free space on {where} ({exc})."
    if free_gb >= need_gb:
        return None
    return (f"only {free_gb:.1f} GB free on {where}; a full corpus build needs about "
            f"{need_gb:.0f} GB (the index MEASURED 2.8 GB on 2026-08-24, plus a "
            f"transient staging tree).")


def check(needs, basex_dir=None, db: str | None = None,
          disk_gb: float = DEFAULT_DISK_GB) -> list[str]:
    """Return a list of human-readable problems; empty means ready."""
    root = Path(basex_dir) if basex_dir else HERE / "basex"
    problems: list[str | None] = []
    for need in needs:
        if need not in CHECKS:
            raise ValueError(f"unknown preflight check {need!r}; known: {CHECKS}")
        if need == "java":
            problems.append(_java())
        elif need == "jar":
            problems.append(_jar(root))
        elif need == "db":
            if not db:
                raise ValueError("the 'db' check needs a database name")
            problems.append(_db(root, db))
        elif need == "uv":
            problems.append(_uv())
        elif need == "disk":
            problems.append(_disk(root, disk_gb))
    return [p for p in problems if p]


def require(needs, basex_dir=None, db: str | None = None,
            disk_gb: float = DEFAULT_DISK_GB) -> None:
    problems = check(needs, basex_dir, db, disk_gb)
    if problems:
        raise Unready(problems)


def render(problems: list[str]) -> str:
    return "\n".join(["BaseX tooling is not ready:"] + [f"  - {p}" for p in problems])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="preflight.py",
        description="Check BaseX preconditions and refuse (rc 2) rather than fail late.")
    p.add_argument("--need", nargs="+", default=["java", "jar"], choices=list(CHECKS),
                   help="which preconditions to require (default: java jar)")
    p.add_argument("--db", help="database name; required by the 'db' check")
    p.add_argument("--basex-dir", default=None, help="override the BaseX directory")
    p.add_argument("--disk-gb", type=float, default=DEFAULT_DISK_GB)
    args = p.parse_args(argv)

    try:
        problems = check(args.need, args.basex_dir, args.db, args.disk_gb)
    except ValueError as exc:
        print(f"preflight: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(render(problems), file=sys.stderr)
        # rc 2 = "not configured", never 1 ("the thing you asked about has
        # findings"). The same distinction F39 put into the x4validate CLIs.
        return 2
    print(f"preflight OK ({', '.join(args.need)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
