"""Run this checkout with the active Python, independent of stale installations."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys


def main() -> int:
    repository = Path(__file__).resolve().parent
    source = repository / "src"
    expected_main = source / "deskvision" / "main.py"
    if not expected_main.is_file():
        print(f"MikoType source is missing: {expected_main}", file=sys.stderr)
        return 2

    # Affect this process only. No PATH, proxy, VPN, or environment installation
    # is changed; even PYTHONPATH pointing at an older checkout cannot win.
    sys.path.insert(0, str(source))
    os.chdir(repository)
    try:
        entrypoint = importlib.import_module("deskvision.main")
    except ImportError as exc:
        print(
            f"MikoType dependencies are unavailable in {sys.executable}: {exc}\n"
            'Activate your project environment and install: python -m pip install -e "."',
            file=sys.stderr,
        )
        return 2
    if Path(entrypoint.__file__).resolve() != expected_main.resolve():
        print(
            f"Wrong MikoType source loaded: {entrypoint.__file__}\n"
            f"Expected: {expected_main}",
            file=sys.stderr,
        )
        return 2
    return entrypoint.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
