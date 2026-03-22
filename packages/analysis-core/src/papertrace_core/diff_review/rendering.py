from __future__ import annotations

import re
import subprocess
from pathlib import Path

from papertrace_core.settings import Settings

NODE_VERSION_RE = re.compile(r"v?(?P<major>\d+)")


def has_supported_node_runtime(settings: Settings) -> bool:
    try:
        completed = subprocess.run(
            [settings.review_node_binary, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.review_fallback_render_timeout_seconds,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False

    version_text = (completed.stdout or completed.stderr).strip()
    match = NODE_VERSION_RE.search(version_text)
    return bool(match and int(match.group("major")) >= 18)


def render_prebuilt_diff2html(raw_diff_path: Path, output_path: Path, settings: Settings) -> bool:
    helper_path = settings.resolved_review_diff2html_helper_path
    if not helper_path.exists() or not has_supported_node_runtime(settings):
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                settings.review_node_binary,
                str(helper_path),
                str(raw_diff_path),
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.review_fallback_render_timeout_seconds,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        output_path.unlink(missing_ok=True)
        return False
    return output_path.exists() and output_path.stat().st_size > 0
