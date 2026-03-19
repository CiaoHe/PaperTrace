from __future__ import annotations

import json
from pathlib import Path

from papertrace_api.main import app


def main() -> int:
    output = Path(".cache/openapi.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
