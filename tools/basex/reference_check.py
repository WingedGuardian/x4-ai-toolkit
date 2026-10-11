"""Refuse (rc 2) unless the reference tree exists and holds base-game XML (issue #10).

The shell entry into `_effective.require_base_documents`, the one check every builder
shares. `build-corpus.sh` and `build-effective.sh` run it FIRST -- before preflight
(which runs `java -version`), before `DROP DB` / `rm -rf` of the previous artifact -- so
a wrong reference path leaves the previous x4raw / x4eff exactly as it was.

    uv run python reference_check.py [--reference PATH]

Without --reference it checks the CONFIGURED tree ($X4_REFERENCE / x4-paths.env), which
is what build-effective.py builds from. rc 0 = usable (prints the document count),
rc 2 = refused or unconfigured.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from x4validate import _effective, _merge, _paths


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reference", default=None,
                   help="reference tree to check (default: the configured one)")
    p.add_argument("--what", default="build",
                   help="what the caller is about to do, for the refusal message")
    args = p.parse_args(argv)
    try:
        config = (_merge.Config(reference=Path(args.reference)) if args.reference
                  else _merge.Config())
        n = _effective.require_base_documents(config, args.what)
    except (_effective.EmptyReference, _paths.Unconfigured) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    print(f"reference: {n} base+DLC XML document(s) under {config.reference}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
