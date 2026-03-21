from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch
from papertrace_core.models import AnalysisRequest, PaperDocument, PaperSourceKind
from papertrace_core.services import resolve_target_repo_url
from papertrace_core.settings import Settings


def build_paper_document(*, source_ref: str, title: str, abstract: str = "", text: str = "") -> PaperDocument:
    return PaperDocument(
        source_kind=PaperSourceKind.ARXIV,
        source_ref=source_ref,
        title=title,
        abstract=abstract,
        text=text or f"{title}\n{abstract}",
    )


def test_resolve_target_repo_url_infers_known_case_from_paper_source() -> None:
    request = AnalysisRequest(paper_source="https://arxiv.org/abs/2106.09685 LoRA")
    paper_document = build_paper_document(
        source_ref=request.paper_source,
        title="LoRA: Low-Rank Adaptation of Large Language Models",
        abstract="Low-rank adaptation updates only injected matrices.",
    )

    repo_url, warnings = resolve_target_repo_url(request, paper_document, contributions=[], settings=None)

    assert repo_url == "https://github.com/microsoft/LoRA"
    assert warnings == ["Resolved target repository from known paper case: https://github.com/microsoft/LoRA."]


def test_resolve_target_repo_url_infers_direct_github_mention() -> None:
    request = AnalysisRequest(paper_source="https://arxiv.org/abs/9999.99999")
    paper_document = build_paper_document(
        source_ref=request.paper_source,
        title="Implementation-linked paper",
        text=(
            "Code is available at https://github.com/example/research-repo and "
            "the experiments reuse the published checkpoints."
        ),
    )

    repo_url, warnings = resolve_target_repo_url(request, paper_document, contributions=[], settings=None)

    assert repo_url == "https://github.com/example/research-repo"
    assert warnings == [
        "Resolved target repository from GitHub URL mentioned in the paper: https://github.com/example/research-repo."
    ]


def test_resolve_target_repo_url_falls_back_to_remote_search(monkeypatch: MonkeyPatch) -> None:
    request = AnalysisRequest(paper_source="Routing transformers paper")
    paper_document = build_paper_document(
        source_ref=request.paper_source,
        title="Routing transformers paper",
        abstract="Sparse routing for larger context windows.",
    )

    monkeypatch.setattr("papertrace_core.services.infer_target_repo_from_cases", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "papertrace_core.services.infer_target_repo_from_paper_mentions", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "papertrace_core.services.infer_target_repo_from_remote_search",
        lambda *_args, **_kwargs: ("https://github.com/example/sparse-routing", ["remote search note"]),
    )

    repo_url, warnings = resolve_target_repo_url(
        request,
        paper_document,
        contributions=[],
        settings=Settings(),
    )

    assert repo_url == "https://github.com/example/sparse-routing"
    assert warnings == [
        "remote search note",
        "Resolved target repository from remote paper-to-repo search: https://github.com/example/sparse-routing.",
    ]
