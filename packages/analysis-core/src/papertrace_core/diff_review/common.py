from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

SOFT_HYPHEN = "\u00ad"
ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
PUNCTUATION_RE = re.compile(r"[\W_]+", re.UNICODE)
WHITESPACE_RE = re.compile(r"\s+")
HYPHENATED_LINE_RE = re.compile(r"(\w)-\s*\n\s*([a-z])")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def stable_digest(value: Any, *, length: int = 24) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()[:length]


def sha256_text(value: str, *, length: int = 64) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def normalize_identifier_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.lower()
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    normalized = WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def normalize_claim_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    for marker in ZERO_WIDTH_CHARS:
        normalized = normalized.replace(marker, "")
    normalized = normalized.replace(SOFT_HYPHEN, "")
    normalized = HYPHENATED_LINE_RE.sub(r"\1\2", normalized)
    normalized = WHITESPACE_RE.sub(" ", normalized).strip().lower()
    return normalized


def normalize_changed_line(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.rstrip()


def language_for_path(relative_path: str | None) -> str:
    if not relative_path:
        return "unknown"
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".py", ".pyi"}:
        return "python"
    if suffix in {".cu", ".cuh"}:
        return "cuda"
    if suffix in {".cc", ".cpp", ".h"}:
        return "cpp"
    if suffix == ".rs":
        return "rust"
    if suffix in {".json"}:
        return "json"
    if suffix in {".md", ".txt"}:
        return "text"
    return suffix.lstrip(".") or "unknown"


def path_tokens(relative_path: str) -> set[str]:
    path = Path(relative_path)
    tokens = {normalize_identifier_text(part) for part in path.parts if part}
    if path.stem:
        tokens.add(normalize_identifier_text(path.stem))
    return {token for token in tokens if token}
