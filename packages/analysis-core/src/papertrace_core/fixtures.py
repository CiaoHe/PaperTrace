from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from papertrace_core.models import AnalysisResult

GOLDEN_FIXTURE_DIR = Path("fixtures/golden")
PAPER_FIXTURE_DIR = Path("fixtures/papers")
REPO_FIXTURE_DIR = Path("fixtures/repos")


class FixtureMention(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    alias: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    confidence: float = 0.8
    evidence: str = Field(min_length=1)


class PaperFixture(BaseModel):
    title: str
    text: str
    codebase_mentions: list[FixtureMention] = Field(default_factory=list)


class RepoFixture(BaseModel):
    repo_url: str
    fork_parent: str | None = None
    readme: str
    notes: str = ""
    explicit_mentions: list[FixtureMention] = Field(default_factory=list)


def available_case_slugs() -> list[str]:
    return sorted(path.stem for path in GOLDEN_FIXTURE_DIR.glob("*.json"))


def load_golden_case(case_slug: str) -> AnalysisResult:
    fixture_path = GOLDEN_FIXTURE_DIR / f"{case_slug}.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return AnalysisResult.model_validate(payload)


def load_paper_fixture(case_slug: str) -> PaperFixture:
    fixture_path = PAPER_FIXTURE_DIR / f"{case_slug}.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return PaperFixture.model_validate(payload)


def load_repo_fixture(case_slug: str) -> RepoFixture:
    fixture_path = REPO_FIXTURE_DIR / f"{case_slug}.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    return RepoFixture.model_validate(payload)
