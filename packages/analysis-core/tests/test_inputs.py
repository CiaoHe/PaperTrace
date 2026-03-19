from papertrace_core.inputs import (
    detect_paper_source_kind,
    normalize_paper_source,
    normalize_repo_url,
)
from papertrace_core.models import PaperSourceKind


def test_detect_paper_source_kind_handles_arxiv_reference() -> None:
    kind = detect_paper_source_kind("https://arxiv.org/abs/2106.09685 LoRA")
    assert kind == PaperSourceKind.ARXIV


def test_detect_paper_source_kind_handles_local_pdf_path() -> None:
    kind = detect_paper_source_kind("~/papers/lora.pdf")
    assert kind == PaperSourceKind.PDF_FILE


def test_normalize_repo_url_canonicalizes_git_suffix() -> None:
    normalized = normalize_repo_url("https://github.com/openai/clip.git")
    assert normalized == "https://github.com/openai/clip"


def test_normalize_repo_url_rejects_non_github_url() -> None:
    try:
        normalize_repo_url("https://gitlab.com/openai/clip")
    except ValueError as exc:
        assert "GitHub" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-GitHub URL")


def test_normalize_paper_source_expands_local_pdf_path() -> None:
    normalized = normalize_paper_source("~/papers/lora.pdf")
    assert normalized.endswith("/papers/lora.pdf")
