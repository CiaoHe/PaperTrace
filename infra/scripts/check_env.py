from __future__ import annotations

import sys
from pathlib import Path

EXPECTED_PYTHON = Path("/Users/kakusou/micromamba/envs/agent311/bin/python")
REQUIRED_PATHS = [
    Path(".cache"),
    Path(".local"),
    Path("fixtures/golden"),
]
OPTIONAL_FILES = [
    Path(".env"),
    Path(".env.example"),
]


def main() -> int:
    if not EXPECTED_PYTHON.exists():
        print(f"Missing expected Python interpreter: {EXPECTED_PYTHON}", file=sys.stderr)
        return 1

    for directory in REQUIRED_PATHS:
        directory.mkdir(parents=True, exist_ok=True)

    missing = [path for path in OPTIONAL_FILES if not path.exists()]
    if missing:
        print(
            "Warning: missing environment files: " + ", ".join(str(path) for path in missing),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
