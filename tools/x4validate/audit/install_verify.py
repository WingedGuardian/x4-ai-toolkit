"""Installed-source smoke tests; never deploy a mod to the real game.

Run only after integrating the reviewed commits. All tool writes use the existing
audit fixtures; production BaseX queries are read-only positive controls.
"""
import json
from pathlib import Path
import re
import time
import tomllib
import driver as D
import exercise
import extra
import fix_verify


def native_refs_count_query(db, ident, basex_dir):
    """The native BaseX control for `ask.py refs <ident>`: `count(...)` over ask's OWN refs
    query. M-e (v4.0.0 delta review): this was a hand-written five-attribute
    form (@ref/@macro/@name/@ware/@component = id) after issue #5 widened `refs` to the
    macro./ware. prefixes, bullet/@class and add_blueprints lists -- so the two counts
    compared different questions, and any corpus hit in a widened form failed the check.
    Derived from `ask.q_refs`, so the control can never drift from the forms ask searches."""
    import importlib.util
    import sys
    basex_dir = str(basex_dir)
    if basex_dir not in sys.path:
        sys.path.insert(0, basex_dir)       # ask imports its siblings (basex_run, preflight)
    spec = importlib.util.spec_from_file_location('install_verify_ask', Path(basex_dir) / 'ask.py')
    ask = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: ask defines dataclasses, and dataclasses looks its module up
    # in sys.modules (unregistered, the import died with AttributeError -- MEASURED).
    sys.modules[spec.name] = ask
    spec.loader.exec_module(ask)
    # Verbatim, never whitespace-collapsed: the add_blueprints tokenizer is
    # translate(@wares, '[],', '   ') -- three spaces -- and collapsing them to one makes
    # translate DELETE ']' and ',' instead, gluing list tokens together.
    return 'count(' + ask.q_refs(db, ident) + ')'


def main():
    original = D.SOURCE
    D.PKG = original/'tools/x4validate'
    D.PYTHON = D.PKG/'.venv/Scripts/python.exe'
    D.BASEX = original/'tools/basex'
    fingerprints = json.loads((D.OUT/'repair-tested-bytes.json').read_text())
    mappings = [(D.LANE, D.SOURCE), (fix_verify.CAT, D.MODDING/'tools/x4cat-spike'),
                (fix_verify.DEV, D.MODDING/'dev')]
    for tested in fingerprints:
        path = Path(tested)
        installed = next(target/path.relative_to(root) for root,target in mappings
                         if path.is_relative_to(root))
        # External repositories check out CRLF. Require exact source text while
        # accepting Git's newline conversion; keep the raw tested-byte manifest.
        exercise.check('installed source equals reviewed '+installed.name, True,
            path.read_bytes().replace(b'\r\n', b'\n') ==
            installed.read_bytes().replace(b'\r\n', b'\n'))
    # Inputs remain disposable, package and command code now select installation.
    exercise.fixtures()
    extra.load_controls()
    env = D.environment()
    code = ('import x4validate; print(x4validate.__file__); '
            f'assert str(x4validate.__file__).startswith({str(D.PKG)!r})')
    D.run('installed-import-origin', [D.PYTHON, '-c', code], env=env)
    launcher_env = dict(env); launcher_env.pop('PYTHONPATH', None)
    roster = tomllib.loads((D.PKG/'pyproject.toml').read_text())['project']['scripts']
    for command in roster:
        D.run('installed-launcher-'+command,
              ['uv', 'run', '--frozen', '--no-sync', command, '--help'], env=launcher_env)
    for command, args in [
        ('x4xref', ['who-calls', 'find_station', '--tsv', D.OUT/'fixture/invalid-xref.tsv']),
        ('x4effective', ['--db', D.OUT/'fixture/not-sqlite.db', 'ls', 'ware']),
        ('x4save', ['info', D.OUT/'fixture/invalid-deflate.xml.gz']),
    ]:
        D.run('installed-launcher-boundary-'+command,
              ['uv', 'run', '--frozen', '--no-sync', command, *args], (2,), env=launcher_env)
    for db in ('x4raw', 'x4eff'):
        result = D.run('installed-basex-positive-'+db,
            [D.PYTHON, D.BASEX/'ask.py', 'refs', 'ore', '--db', db, '--limit', '3'],
            (0,3), cwd=D.BASEX, env=env)
        count_query = native_refs_count_query(db, 'ore', D.BASEX)
        native = D.run('installed-basex-native-count-'+db,
            ['java', '-cp', 'BaseX.jar', 'org.basex.BaseX', '-q', count_query],
            cwd=D.BASEX/'basex', env=launcher_env)
        match = re.search(r'3 displayed of (\d+) total item\(s\)', result['stdout'])
        exercise.check('installed BaseX '+db+' displays bounded positives', True,
            bool(match) and int(match.group(1)) == int(native['stdout'].strip())
            and int(match.group(1)) >= 3)
        D.run('installed-basex-invalid-'+db,
            [D.PYTHON, D.BASEX/'ask.py', 'attr', 'name[false()]', '--db', db],
            (2,), cwd=D.BASEX, env=env)
    # The separately owned tools stay in their original repositories.
    fix_verify.CAT = D.MODDING/'tools/x4cat-spike'
    fix_verify.DEV = D.MODDING/'dev'
    env = D.environment(False)
    D.run('installed-deploy-regressions', [D.PYTHON, '-m', 'pytest', '_tools/test_deploy.py', '-q',
          '-o', f'cache_dir={D.OUT / "installed-deploy-cache"}'],
          env=env, cwd=fix_verify.DEV)
    env['PYTHONPATH'] = str(fix_verify.CAT)
    D.run('installed-launcher-x4cat', ['uv', 'run', '--frozen', '--no-sync', 'x4cat', '--help'],
          env=launcher_env, cwd=fix_verify.CAT)
    D.run('installed-x4cat-regressions', [D.PYTHON, '-m', 'pytest', 'tests', '-q', '-rs',
          '-o', f'cache_dir={D.OUT / "installed-cat-cache"}'],
          env=env, cwd=fix_verify.CAT)
    template = fix_verify.CAT/'templates/extension_poc'
    out = D.OUT/('installed-scaffold-'+str(time.time_ns()))
    # Refuse a previous output rather than implicitly overwriting it.
    if out.exists():
        raise RuntimeError(f'Installed scaffold output already exists: {out}')
    D.run('installed-x4cat-scaffold', ['uv', 'run', '--frozen', '--no-sync', 'x4cat', 'init',
          'audit_installed', '-o', out, '--template-dir', template], env=launcher_env, cwd=fix_verify.CAT)
    exercise.check('installed scaffold complete', True,
                   (out/'content.xml').is_file() and (out/'src/md/audit_installed.xml').is_file())
    backup = json.loads((D.OUT/'install-backup/manifest.json').read_text())
    lock = str(fix_verify.CAT/'uv.lock')
    import hashlib
    exercise.check('pre-existing local x4cat lockfile preserved', backup[lock],
                   hashlib.sha256(Path(lock).read_bytes()).hexdigest())
    return bool(D.ISSUES)


if __name__ == '__main__':
    raise SystemExit(main())
