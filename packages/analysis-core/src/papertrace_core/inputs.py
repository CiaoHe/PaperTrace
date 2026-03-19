from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from papertrace_core.models import PaperSourceKind

ARXIV_ABS_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def detect_paper_source_kind(value: str) -> PaperSourceKind:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Paper source cannot be empty")

    if ARXIV_ABS_RE.search(normalized):
        return PaperSourceKind.ARXIV

    if normalized.lower().startswith(("http://", "https://")) and ".pdf" in normalized.lower():
        return PaperSourceKind.PDF_URL

    if normalized.lower().endswith(".pdf"):
        return PaperSourceKind.PDF_FILE

    return PaperSourceKind.TEXT_REFERENCE


def normalize_paper_source(value: str) -> str:
    normalized = value.strip()
    kind = detect_paper_source_kind(normalized)
    if kind == PaperSourceKind.PDF_FILE:
        return str(Path(normalized).expanduser())
    return normalized


def normalize_repo_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise ValueError("Repository URL must be a GitHub http(s) URL")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        raise ValueError("Repository URL must include owner and repository name")

    owner = path_parts[0]
    repo = path_parts[1].removesuffix(".git")
    return f"https://github.com/{owner}/{repo}"
