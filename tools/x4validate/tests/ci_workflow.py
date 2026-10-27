"""Read both committed workflow files without overlooking a moved job."""
from pathlib import Path


def load_ci(root=None):
    from ruamel.yaml import YAML
    yaml = YAML(typ="safe")
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    main = yaml.load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    full = yaml.load((root / ".github/workflows/full-qualification.yml").read_text(encoding="utf-8"))
    assert not (set(main["jobs"]) & set(full["jobs"])), "duplicate workflow job identity"
    main["jobs"].update(full["jobs"])
    return main
