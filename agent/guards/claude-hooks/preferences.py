"""Read local standing preferences through the pinned shared parser."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import save_policy


def main():
    root = Path(__file__).resolve().parents[2]
    text = save_policy.core(root, "preferences").render(root)
    if text:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
