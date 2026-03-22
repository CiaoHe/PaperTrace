from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from papertrace_core.diff_review.builder import _semantic_status_for_pair
from papertrace_core.diff_review.claims import split_contribution_claims
from papertrace_core.diff_review.file_mapper import FileMapper, FilePair, MatchCandidate
from papertrace_core.diff_review.models import (
    ReviewClaimIndexEntry,
    ReviewContributionStatus,
    ReviewDiffType,
    ReviewFallbackMode,
    ReviewMatchType,
    ReviewRefinementStatus,
    ReviewSemanticStatus,
)
from papertrace_core.diff_review.projection import project_analysis_result_from_review, project_review_links
from papertrace_core.diff_review.retrieval import (
    ReviewCandidateInput,
    build_hunk_candidates,
    retrieve_claim_hunk_links,
)
from papertrace_core.diff_review.revision import resolve_repo_revision
from papertrace_core.diff_review.unified_diff import (
    build_file_payload,
    build_raw_diff_only_payload,
    generate_raw_unified_diff,
)
from papertrace_core.models import (
    AnalysisResult,
    AnalysisRuntimeMetadata,
    BaseRepoCandidate,
    DiffChangeType,
    DiffCluster,
    PaperContribution,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.settings import Settings


def init_git_repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "papertrace@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "PaperTrace"], check=True)
    for relative_path, content in files.items():
        file_path = root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True, text=True)


def test_file_mapper_marks_ambiguous_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/a.py": "alpha\n", "src/b.py": "beta\n"})
    init_git_repo(current_root, {"src/new_file.py": "gamma\n"})
    settings = Settings()
    mapper = FileMapper(settings)

    monkeypatch.setattr(
        mapper,
        "_rank_candidates",
        lambda *_args, **_kwargs: [
            MatchCandidate(path="src/a.py", similarity=0.72),
            MatchCandidate(path="src/b.py", similarity=0.68),
        ],
    )

    pairs = mapper.map_repositories(source_root, current_root)

    assert any(pair.match_type == ReviewMatchType.AMBIGUOUS for pair in pairs)


def test_file_mapper_marks_low_confidence_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/a.py": "alpha\n"})
    init_git_repo(current_root, {"src/new_file.py": "gamma\n"})
    settings = Settings()
    mapper = FileMapper(settings)

    monkeypatch.setattr(
        mapper,
        "_rank_candidates",
        lambda *_args, **_kwargs: [MatchCandidate(path="src/a.py", similarity=0.44)],
    )

    pairs = mapper.map_repositories(source_root, current_root)

    assert any(pair.match_type == ReviewMatchType.LOW_CONFIDENCE for pair in pairs)


def test_unified_diff_hunk_ids_are_stable(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    return x + 1\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    return x + 2\n"})
    settings = Settings()

    raw_diff = generate_raw_unified_diff(
        source_root,
        current_root,
        source_path="src/model.py",
        current_path="src/model.py",
        context_lines=settings.review_context_lines,
    )
    payload_a = build_file_payload(
        file_id="file-1",
        source_path="src/model.py",
        current_path="src/model.py",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        raw_unified_diff=raw_diff,
        semantic_status=ReviewSemanticStatus.FALLBACK_TEXT,
        fallback_mode=ReviewFallbackMode.NONE,
        fallback_html_path=None,
        linked_claim_ids=[],
        linked_contribution_keys=[],
    )
    payload_b = build_file_payload(
        file_id="file-1",
        source_path="src/model.py",
        current_path="src/model.py",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        raw_unified_diff=raw_diff,
        semantic_status=ReviewSemanticStatus.FALLBACK_TEXT,
        fallback_mode=ReviewFallbackMode.NONE,
        fallback_html_path=None,
        linked_claim_ids=[],
        linked_contribution_keys=[],
    )

    assert payload_a.hunks
    assert [hunk.hunk_id for hunk in payload_a.hunks] == [hunk.hunk_id for hunk in payload_b.hunks]


def test_review_file_payload_hunks_are_metadata_only(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    current_root = tmp_path / "current"
    init_git_repo(source_root, {"src/model.py": "def forward(x):\n    hidden = x + 1\n    return hidden\n"})
    init_git_repo(current_root, {"src/model.py": "def forward(x):\n    hidden = x + 2\n    return hidden * 2\n"})
    settings = Settings()

    raw_diff = generate_raw_unified_diff(
        source_root,
        current_root,
        source_path="src/model.py",
        current_path="src/model.py",
        context_lines=settings.review_context_lines,
    )
    payload = build_file_payload(
        file_id="file-1",
        source_path="src/model.py",
        current_path="src/model.py",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        raw_unified_diff=raw_diff,
        semantic_status=ReviewSemanticStatus.FALLBACK_TEXT,
        fallback_mode=ReviewFallbackMode.NONE,
        fallback_html_path=None,
        linked_claim_ids=[],
        linked_contribution_keys=[],
    )

    serialized_hunks = payload.model_dump_json()

    assert "hidden = x + 2" not in serialized_hunks
    assert "return hidden * 2" not in serialized_hunks
    assert payload.hunks


def test_raw_diff_only_payload_degrades_when_diff_parser_is_not_available() -> None:
    payload = build_raw_diff_only_payload(
        file_id="file-1",
        source_path="src/model.py",
        current_path="src/model.py",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        raw_unified_diff="--- a/src/model.py\n+++ b/src/model.py\n@@ -1 +1 @@\n-invalid\n+valid\n",
        semantic_status=ReviewSemanticStatus.FALLBACK_TEXT,
        linked_claim_ids=["claim-1"],
        linked_contribution_keys=["contrib-1"],
    )

    assert payload.fallback_mode == "raw_diff_only"
    assert payload.stats.changed_line_count == 2
    assert payload.hunks == []
    assert payload.linked_claim_ids == ["claim-1"]


def test_resolve_repo_revision_falls_back_to_content_fingerprint(tmp_path: Path) -> None:
    repo_root = tmp_path / "plain-dir"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "module.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    settings = Settings()

    revision = resolve_repo_revision(repo_root, settings)

    assert len(revision) == 16


def test_claim_splitter_normalizes_soft_hyphen_and_whitespace() -> None:
    contribution = PaperContribution(
        id="C1",
        title="Adaptive Decoder",
        section="Method",
        keywords=["decoder"],
        impl_hints=["We use adap\u00adtive decoding.\n  It improves stability."],
    )

    claims = split_contribution_claims(contribution, status=ReviewContributionStatus.UNMAPPED)

    assert claims
    assert all(len(claim.claim_id) == 20 for claim in claims)
    assert any("adaptive decoding" in claim.claim_text.lower() for claim in claims)


def test_semantic_status_for_pair_covers_primary_cases() -> None:
    added = FilePair(
        source_path=None,
        current_path="src/new_module.py",
        diff_type=ReviewDiffType.ADDED,
        match_type=ReviewMatchType.ADDED,
        similarity=0.0,
        language="python",
    )
    deleted = FilePair(
        source_path="src/old_module.py",
        current_path=None,
        diff_type=ReviewDiffType.DELETED,
        match_type=ReviewMatchType.DELETED,
        similarity=0.0,
        language="python",
    )
    unsupported = FilePair(
        source_path="kernels/op.cu",
        current_path="kernels/op.cu",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        similarity=1.0,
        language="cuda",
    )
    modified = FilePair(
        source_path="src/model.py",
        current_path="src/model.py",
        diff_type=ReviewDiffType.MODIFIED,
        match_type=ReviewMatchType.EXACT_PATH,
        similarity=1.0,
        language="python",
    )

    assert _semantic_status_for_pair(added) == ReviewSemanticStatus.NEW_FILE
    assert _semantic_status_for_pair(deleted) == ReviewSemanticStatus.DELETED_FILE
    assert _semantic_status_for_pair(unsupported) == ReviewSemanticStatus.UNSUPPORTED_LANGUAGE
    assert _semantic_status_for_pair(modified) == ReviewSemanticStatus.FALLBACK_TEXT


def test_deterministic_claim_linking_projects_statuses() -> None:
    contribution_a = PaperContribution(
        id="C1",
        title="LoRA adapter modules",
        section="Method",
        keywords=["adapter", "low-rank"],
        impl_hints=["Inject lora adapter matrices into target modules."],
    )
    contribution_b = PaperContribution(
        id="C2",
        title="Frozen backbone fine-tuning",
        section="Training",
        keywords=["freeze", "optimizer"],
        impl_hints=["Keep pretrained weights frozen and only optimize adapter parameters."],
    )
    claims = [
        ReviewClaimIndexEntry(
            claim_id="claim-1",
            claim_label="C1.S1",
            contribution_key="contrib-1",
            contribution_id="C1",
            section="Method",
            claim_text="Inject low-rank adapter matrices into target modules.",
            status=ReviewContributionStatus.UNMAPPED,
        ),
        ReviewClaimIndexEntry(
            claim_id="claim-2",
            claim_label="C2.S1",
            contribution_key="contrib-2",
            contribution_id="C2",
            section="Training",
            claim_text="Freeze pretrained weights and train only LoRA parameters.",
            status=ReviewContributionStatus.UNMAPPED,
        ),
    ]
    result = AnalysisResult(
        case_slug="custom",
        summary="custom",
        selected_base_repo=BaseRepoCandidate(
            repo_url="base",
            strategy="test",
            confidence=1.0,
            evidence="test",
        ),
        base_repo_candidates=[],
        contributions=[contribution_a, contribution_b],
        diff_clusters=[
            DiffCluster(
                id="D1",
                label="Adapter layer",
                change_type=DiffChangeType.NEW_MODULE,
                files=["peft/tuners/lora/layer.py"],
                summary="Adds LoRA adapter layer wiring.",
                semantic_tags=["adapter", "low-rank"],
            ),
            DiffCluster(
                id="D2",
                label="Freeze backbone",
                change_type=DiffChangeType.MODIFIED_TRAIN,
                files=["peft/utils/other.py"],
                summary="Freezes backbone parameters for LoRA-only training.",
                semantic_tags=["freeze", "optimizer"],
            ),
        ],
        mappings=[],
        metadata=AnalysisRuntimeMetadata(
            paper_source_kind=PaperSourceKind.TEXT_REFERENCE,
            paper_fetch_mode=ProcessorMode.HEURISTIC,
            parser_mode=ProcessorMode.HEURISTIC,
            repo_tracer_mode=ProcessorMode.HEURISTIC,
            diff_analyzer_mode=ProcessorMode.HEURISTIC,
            contribution_mapper_mode=ProcessorMode.HEURISTIC,
            selected_repo_strategy="test",
            fallback_notes=[],
        ),
        warnings=[],
    )
    candidate_hunks = build_hunk_candidates(
        result,
        [
            ReviewCandidateInput(
                file_id="file-1",
                file_path="peft/tuners/lora/layer.py",
                language="python",
                raw_unified_diff=(
                    "--- a/peft/tuners/lora/layer.py\n"
                    "+++ b/peft/tuners/lora/layer.py\n"
                    "@@ -1 +1 @@\n"
                    "-class LinearLayer\n"
                    "+class LoraAdapterLayer\n"
                    "+def inject_adapter(module):\n"
                ),
            ),
            ReviewCandidateInput(
                file_id="file-2",
                file_path="peft/utils/other.py",
                language="python",
                raw_unified_diff=(
                    "--- a/peft/utils/other.py\n"
                    "+++ b/peft/utils/other.py\n"
                    "@@ -1 +1 @@\n"
                    "-def mark_trainable(model):\n"
                    "+def freeze_backbone_and_train_lora(model):\n"
                    "+    backbone_is_frozen = True\n"
                    '+    parameter.requires_grad = "lora" in name\n'
                ),
            ),
        ],
        Settings(),
    )
    retrieval = retrieve_claim_hunk_links(
        claim_entries=claims,
        contributions=[contribution_a, contribution_b],
        candidate_hunks=candidate_hunks,
    )
    projection = project_review_links(
        claim_entries=claims,
        links=retrieval.accepted_links,
        candidate_links_by_claim_id=retrieval.candidates_by_claim_id,
        refinement_status=ReviewRefinementStatus.DISABLED,
    )
    projected_result = project_analysis_result_from_review(result, projection.claim_entries, retrieval.accepted_links)

    assert len(retrieval.accepted_links) >= 2
    assert {entry.status for entry in projection.contribution_status} == {ReviewContributionStatus.MAPPED}
    assert all(entry.status == ReviewContributionStatus.MAPPED for entry in projection.claim_entries)
    assert set(projection.file_links) == {"file-1", "file-2"}
    assert len(projection.hunk_links) >= 2
    assert {mapping.diff_cluster_id for mapping in projected_result.mappings} == {"D1", "D2"}
    assert projected_result.unmatched_contribution_ids == []
    assert projected_result.unmatched_diff_cluster_ids == []


def test_project_review_links_marks_refining_when_candidates_exist_without_acceptance() -> None:
    claim = ReviewClaimIndexEntry(
        claim_id="claim-1",
        claim_label="C1.S1",
        contribution_key="contrib-1",
        contribution_id="C1",
        section="Method",
        claim_text="Inject low-rank adapter matrices into target modules.",
        status=ReviewContributionStatus.UNMAPPED,
    )
    candidate_link = retrieve_claim_hunk_links(
        claim_entries=[claim],
        contributions=[
            PaperContribution(
                id="C1",
                title="LoRA adapter modules",
                section="Method",
                keywords=["adapter", "low-rank"],
                impl_hints=["Inject lora adapter matrices into target modules."],
            )
        ],
        candidate_hunks=[
            build_hunk_candidates(
                AnalysisResult(
                    case_slug="custom",
                    summary="custom",
                    selected_base_repo=BaseRepoCandidate(
                        repo_url="base",
                        strategy="test",
                        confidence=1.0,
                        evidence="test",
                    ),
                    base_repo_candidates=[],
                    contributions=[],
                    diff_clusters=[],
                    mappings=[],
                    metadata=AnalysisRuntimeMetadata(
                        paper_source_kind=PaperSourceKind.TEXT_REFERENCE,
                        paper_fetch_mode=ProcessorMode.HEURISTIC,
                        parser_mode=ProcessorMode.HEURISTIC,
                        repo_tracer_mode=ProcessorMode.HEURISTIC,
                        diff_analyzer_mode=ProcessorMode.HEURISTIC,
                        contribution_mapper_mode=ProcessorMode.HEURISTIC,
                        selected_repo_strategy="test",
                        fallback_notes=[],
                    ),
                    warnings=[],
                ),
                [
                    ReviewCandidateInput(
                        file_id="file-1",
                        file_path="peft/tuners/lora/layer.py",
                        language="python",
                        raw_unified_diff=(
                            "--- a/peft/tuners/lora/layer.py\n"
                            "+++ b/peft/tuners/lora/layer.py\n"
                            "@@ -1 +1 @@\n"
                            "-class LinearLayer\n"
                            "+class LoraAdapterLayer\n"
                            "+def inject_adapter(module):\n"
                        ),
                    )
                ],
                Settings(),
            )[0]
        ],
    )
    projection = project_review_links(
        claim_entries=[claim],
        links=[],
        candidate_links_by_claim_id=candidate_link.candidates_by_claim_id,
        refinement_status=ReviewRefinementStatus.QUEUED,
    )

    assert projection.claim_entries[0].status == ReviewContributionStatus.REFINING
    assert projection.contribution_status[0].status == ReviewContributionStatus.REFINING
