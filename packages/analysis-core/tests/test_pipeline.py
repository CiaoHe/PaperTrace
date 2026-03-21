from typing import Any, cast

from papertrace_core.cases import detect_case_slug
from papertrace_core.fixtures import load_golden_case, load_paper_fixture
from papertrace_core.heuristics import infer_contributions, infer_mappings
from papertrace_core.models import (
    AnalysisRequest,
    BaseRepoCandidate,
    ContributionMapping,
    CoverageType,
    DiffChangeType,
    DiffCluster,
    DiffCodeAnchor,
    JobStage,
    PaperContribution,
    PaperDocument,
    PaperSection,
    PaperSourceKind,
    ProcessorMode,
)
from papertrace_core.paper_sources import FixturePaperSourceFetcher, paper_document_from_fixture
from papertrace_core.pipeline import run_analysis
from papertrace_core.repo_metadata import FixtureRepoMetadataProvider
from papertrace_core.services import (
    AnalysisService,
    FixtureContributionMapper,
    FixtureDiffAnalyzer,
    HeuristicPaperParser,
    StrategyDrivenRepoTracer,
    build_default_analysis_service,
    sort_repo_candidates,
)
from papertrace_core.settings import get_settings


class EmptyLLMClient:
    def extract_contributions(self, _: object) -> list[object]:
        return []

    def map_contributions(self, _: object, __: object) -> list[object]:
        return []


class SparseRoutingLLMClient:
    def extract_contributions(self, _: object) -> list[object]:
        return [
            PaperContribution(
                id="L1",
                title="Sparse routing encoder",
                section="Method",
                keywords=["routing", "encoder"],
                impl_hints=["Introduce a sparse routing encoder for long-context retrieval."],
            )
        ]

    def map_contributions(self, _: object, __: object) -> list[object]:
        return []


class MappingRescueLLMClient:
    def extract_contributions(self, _: object) -> list[object]:
        return []

    def map_contributions(self, _: object, __: object) -> list[object]:
        return [
            ContributionMapping(
                diff_cluster_id="D1",
                contribution_id="C1",
                confidence=0.84,
                evidence="LLM identified the routing kernel change as the implementation match.",
                completeness="partial",
            )
        ]


def test_detect_case_slug_prefers_lora_fixture() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )

    assert detect_case_slug(request) == "lora"


def test_detect_case_slug_returns_none_for_unknown_paper() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2601.18734",
        repo_url="https://github.com/siyan-zhao/OPSD",
    )

    assert detect_case_slug(request) is None


def test_run_analysis_returns_fixture_payload(monkeypatch: Any) -> None:
    monkeypatch.setenv("ENABLE_LIVE_BY_DEFAULT", "false")
    monkeypatch.setenv("ENABLE_LIVE_PAPER_FETCH", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_TRACE", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_ANALYSIS", "false")
    get_settings.cache_clear()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2305.18290 DPO",
        repo_url="https://github.com/huggingface/trl",
    )

    try:
        result = run_analysis(request)
    finally:
        get_settings.cache_clear()

    assert result.case_slug == "dpo"
    assert result.selected_base_repo.repo_url == "https://github.com/huggingface/trl"
    assert len(result.diff_clusters) == 1


def test_run_analysis_emits_progress_events_in_stage_order(monkeypatch: Any) -> None:
    monkeypatch.setenv("ENABLE_LIVE_BY_DEFAULT", "false")
    monkeypatch.setenv("ENABLE_LIVE_PAPER_FETCH", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_TRACE", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_ANALYSIS", "false")
    get_settings.cache_clear()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    progress_events: list[tuple[JobStage, float, str]] = []

    try:
        result = run_analysis(
            request,
            progress=lambda stage, ratio, detail: progress_events.append((stage, ratio, detail)),
        )
    finally:
        get_settings.cache_clear()

    assert result.case_slug == "flash-attention"
    assert progress_events
    observed_stages = {stage for stage, _, _ in progress_events}
    assert {
        JobStage.PAPER_FETCH,
        JobStage.PAPER_PARSE,
        JobStage.REPO_FETCH,
        JobStage.ANCESTRY_TRACE,
        JobStage.DIFF_ANALYZE,
        JobStage.CONTRIBUTION_MAP,
    } <= observed_stages
    assert progress_events[-1][0] == JobStage.CONTRIBUTION_MAP
    assert progress_events[-1][1] == 1.0


def test_default_analysis_service_recomposes_fixture_result(monkeypatch: Any) -> None:
    monkeypatch.setenv("ENABLE_LIVE_BY_DEFAULT", "false")
    monkeypatch.setenv("ENABLE_LIVE_PAPER_FETCH", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_TRACE", "false")
    monkeypatch.setenv("ENABLE_LIVE_REPO_ANALYSIS", "false")
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://api.github.com")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_settings.cache_clear()
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )

    try:
        result = build_default_analysis_service().analyze(request)
    finally:
        get_settings.cache_clear()

    assert result.case_slug == "flash-attention"
    assert result.contributions
    assert "attention" in result.contributions[0].title.lower()
    assert result.base_repo_candidates[0].strategy == "paper_mention"
    assert result.metadata.selected_repo_strategy
    assert result.metadata.paper_source_kind == PaperSourceKind.ARXIV
    assert result.metadata.paper_fetch_mode == ProcessorMode.FIXTURE
    assert result.metadata.parser_mode in {ProcessorMode.HEURISTIC, ProcessorMode.LLM}
    assert result.metadata.repo_tracer_mode == ProcessorMode.STRATEGY_CHAIN
    assert result.metadata.diff_analyzer_mode == ProcessorMode.FIXTURE
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert result.metadata.selected_repo_strategy == result.selected_base_repo.strategy
    assert "Diff analyzer is currently fixture-backed." in result.metadata.fallback_notes


def test_repo_tracer_prefers_readme_declaration_over_paper_mention() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("lora"))

    trace_output = StrategyDrivenRepoTracer(repo_metadata_provider=FixtureRepoMetadataProvider()).trace(
        request, paper_document, []
    )

    assert trace_output.selected_base_repo.strategy == "readme_base_declaration"
    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"
    assert len(trace_output.candidates) == 1
    assert trace_output.candidates[0].strategy == "readme_base_declaration"
    assert trace_output.mode == ProcessorMode.STRATEGY_CHAIN


def test_repo_tracer_falls_back_to_code_fingerprint_when_no_mentions_exist() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("flash-attention"))

    trace_output = StrategyDrivenRepoTracer(repo_metadata_provider=FixtureRepoMetadataProvider()).trace(
        request, paper_document, []
    )

    assert trace_output.selected_base_repo.strategy == "paper_mention"
    assert trace_output.candidates[0].repo_url == "https://github.com/openai/triton"


def test_sort_repo_candidates_prioritizes_strategy_before_confidence() -> None:
    candidates = [
        BaseRepoCandidate(
            repo_url="https://github.com/example/high-confidence",
            strategy="code_fingerprint",
            confidence=0.98,
            evidence="fingerprint",
        ),
        BaseRepoCandidate(
            repo_url="https://github.com/example/lower-confidence",
            strategy="readme_declaration",
            confidence=0.75,
            evidence="readme",
        ),
    ]

    sorted_candidates = sort_repo_candidates(candidates)

    assert sorted_candidates[0].strategy == "readme_declaration"


def test_infer_contributions_extracts_lora_patterns() -> None:
    paper_fixture = load_paper_fixture("lora")

    contributions = infer_contributions("lora", paper_fixture.title, paper_fixture.text)

    assert len(contributions) == 2
    assert contributions[0].id == "C1"
    assert "low-rank" in contributions[0].keywords


def test_infer_mappings_matches_lora_clusters_to_contributions() -> None:
    golden = load_golden_case("lora")

    mappings = infer_mappings(golden.contributions, golden.diff_clusters)

    assert len(mappings) == 2
    assert mappings[0].diff_cluster_id == "D1"
    assert mappings[0].contribution_id == "C1"
    assert "cluster files:" in mappings[0].evidence


def test_service_records_fallback_notes_when_llm_returns_empty_payloads() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    service = AnalysisService(
        paper_source_fetcher=FixturePaperSourceFetcher(),
        paper_parser=HeuristicPaperParser(llm_client=cast(Any, EmptyLLMClient())),
        repo_tracer=StrategyDrivenRepoTracer(repo_metadata_provider=FixtureRepoMetadataProvider()),
        diff_analyzer=FixtureDiffAnalyzer(),
        contribution_mapper=FixtureContributionMapper(llm_client=cast(Any, EmptyLLMClient())),
    )

    result = service.analyze(request)

    assert result.metadata.parser_mode == ProcessorMode.HEURISTIC
    assert result.metadata.contribution_mapper_mode == ProcessorMode.HEURISTIC
    assert "Paper parser received an empty llm response and fell back." in result.metadata.fallback_notes
    assert "Contribution mapper received an empty llm response and fell back." not in result.metadata.fallback_notes


def test_contribution_mapper_uses_llm_review_when_heuristic_matches_are_weak() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/9999.00020",
        repo_url="https://github.com/example/routing-kernel",
    )
    contribution = PaperContribution(
        id="C1",
        title="Routing kernel",
        section="Method",
        keywords=["routing", "kernel"],
        impl_hints=["Introduce a routing kernel."],
    )
    weak_cluster = DiffCluster(
        id="D1",
        label="Core update",
        change_type=DiffChangeType.MODIFIED_CORE,
        files=["src/router.py"],
        summary="Core implementation changes.",
        code_anchors=[
            DiffCodeAnchor(
                patch_id="anchor-weak-1",
                file_path="src/router.py",
                start_line=10,
                end_line=14,
                snippet="def build_stage(tokens):\n    return normalize(tokens)\n",
                original_snippet=None,
                reason="generic staged normalization logic",
                anchor_kind="addition",
            )
        ],
        semantic_tags=["core"],
    )

    output = FixtureContributionMapper(llm_client=cast(Any, MappingRescueLLMClient())).map(
        request,
        contributions=[contribution],
        diff_clusters=[weak_cluster],
    )

    assert output.mode == ProcessorMode.LLM
    assert output.mappings
    assert output.mappings[0].diff_cluster_id == "D1"
    assert output.mappings[0].contribution_id == "C1"
    assert "LLM identified" in output.mappings[0].evidence


def test_contribution_mapper_marks_zero_grounding_matches_as_weak() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/9999.00021",
        repo_url="https://github.com/example/infra-heavy-repo",
    )
    contribution = PaperContribution(
        id="C1",
        title="IO-aware fused attention kernel",
        section="Method",
        keywords=["attention", "kernel", "fused"],
        impl_hints=["Fuse tiled attention into an IO-aware kernel."],
    )
    weak_cluster = DiffCluster(
        id="D9",
        label="Attention kernel packaging",
        change_type=DiffChangeType.MODIFIED_INFRA,
        files=["setup.py", "docs/notes.md"],
        summary="Attention kernel packaging updates inferred from setup.py and release notes.",
        code_anchors=[
            DiffCodeAnchor(
                patch_id="anchor-weak-9",
                file_path="setup.py",
                start_line=1,
                end_line=4,
                snippet="from setuptools import setup\nsetup(name='papertrace')\n",
                original_snippet=None,
                reason="packaging update",
                anchor_kind="addition",
            )
        ],
        semantic_tags=["infra"],
    )

    output = FixtureContributionMapper().map(
        request,
        contributions=[contribution],
        diff_clusters=[weak_cluster],
    )

    assert output.mode == ProcessorMode.HEURISTIC
    assert output.mappings
    assert output.mappings[0].coverage_type == CoverageType.MISSING
    assert any("marked 1 mapping" in warning for warning in output.warnings)


def test_heuristic_paper_parser_merges_llm_output_with_heuristic_evidence() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/llm-augmented-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.PDF_FILE,
        source_ref=request.paper_source,
        title="Sparse Routing Distillation",
        abstract="We introduce a sparse routing encoder for long-context retrieval.",
        sections=[
            PaperSection(
                heading="2 Method",
                text=(
                    "We introduce a sparse routing encoder that compresses long documents into routing slots.\n"
                    "Algorithm 1 describes the sparse routing update."
                ),
            ),
            PaperSection(
                heading="4 Experiments",
                text=("Implementation details: cached slot reuse keeps CPU-friendly local validation stable."),
            ),
        ],
        text=(
            "Sparse Routing Distillation\n"
            "We introduce a sparse routing encoder for long-context retrieval.\n"
            "Algorithm 1 describes the sparse routing update.\n"
            "Implementation details: cached slot reuse keeps CPU-friendly local validation stable."
        ),
    )

    result = HeuristicPaperParser(llm_client=cast(Any, SparseRoutingLLMClient())).parse(request, paper_document)

    assert result.mode == ProcessorMode.LLM
    assert result.contributions
    assert any(contribution.title == "Sparse routing encoder" for contribution in result.contributions)
    merged = next(
        contribution for contribution in result.contributions if contribution.title == "Sparse routing encoder"
    )
    assert "Algorithm 1" in merged.evidence_refs
    assert len(merged.impl_hints) >= 2


def test_heuristic_paper_parser_uses_fetched_paper_document() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/microsoft/LoRA",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("lora"))

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert result.contributions
    assert result.contributions[0].id == "C1"


def test_heuristic_paper_parser_infers_dpo_from_pdf_text_without_case_alias() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/uploaded-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("dpo")).model_copy(
        update={
            "source_kind": PaperSourceKind.PDF_FILE,
            "title": "Direct Preference Optimization",
            "text": (
                "Direct Preference Optimization is a simple preference objective. "
                "Our method removes the need for an explicit reward model and "
                "optimizes directly on preference data."
            ),
        }
    )

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert any(
        contribution.title == "Direct preference optimization objective" for contribution in result.contributions
    )


def test_heuristic_paper_parser_derives_generic_contribution_from_pdf_abstract() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/unknown-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("lora")).model_copy(
        update={
            "source_kind": PaperSourceKind.PDF_FILE,
            "title": "Sparse Routing Encoder",
            "text": (
                "We introduce a sparse routing encoder for long-context retrieval. "
                "The encoder compresses long documents while preserving retrieval accuracy."
            ),
        }
    )

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert result.contributions
    assert "sparse routing encoder" in result.contributions[0].title.lower()


def test_heuristic_paper_parser_extracts_enumerated_contributions_from_sections() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/section-aware-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.PDF_FILE,
        source_ref=request.paper_source,
        title="Structured Retrieval Distillation",
        abstract="We present a distillation pipeline for retrieval models.",
        sections=[
            PaperSection(
                heading="1 Our Contributions",
                text=(
                    "1. We introduce a retrieval distillation objective that preserves hard-negative ranking.\n"
                    "2. We present a teacher-student data curation pipeline for long-context corpora.\n"
                    "3. We show stable CPU-friendly evaluation for local validation."
                ),
            )
        ],
        text=(
            "Structured Retrieval Distillation\n"
            "We present a distillation pipeline for retrieval models.\n"
            "1. We introduce a retrieval distillation objective that preserves hard-negative ranking.\n"
            "2. We present a teacher-student data curation pipeline for long-context corpora.\n"
            "3. We show stable CPU-friendly evaluation for local validation."
        ),
    )

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert len(result.contributions) >= 2
    assert all("contributions" in contribution.section.lower() for contribution in result.contributions[:2])
    assert any(
        "retrieval distillation objective" in contribution.title.lower() for contribution in result.contributions
    )
    assert any(contribution.problem_solved for contribution in result.contributions)
    assert any(contribution.implementation_complexity for contribution in result.contributions)
    assert "Paper parser did not find an explicit method section." in result.warnings


def test_heuristic_paper_parser_synthesizes_cross_section_evidence() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/cross-section-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.PDF_FILE,
        source_ref=request.paper_source,
        title="Sparse Routing Distillation",
        abstract=(
            "We introduce a sparse routing encoder for long-context retrieval and pair it with"
            " a distillation objective for stable CPU validation."
        ),
        sections=[
            PaperSection(
                heading="2 Method",
                text=(
                    "We introduce a sparse routing encoder that compresses long documents into routing slots.\n"
                    "The routing encoder preserves hard-negative retrieval quality "
                    "rather than dense full-context passes.\n"
                    "Algorithm 1 describes the sparse routing update."
                ),
            ),
            PaperSection(
                heading="4 Experiments",
                text=(
                    "Implementation details: the sparse routing encoder uses CPU-friendly "
                    "batching and cached slot reuse.\n"
                    "Table 3 shows stable latency under local validation."
                ),
            ),
        ],
        text=(
            "Sparse Routing Distillation\n"
            "We introduce a sparse routing encoder for long-context retrieval "
            "and pair it with a distillation objective.\n"
            "We introduce a sparse routing encoder that compresses long documents into routing slots.\n"
            "The routing encoder preserves hard-negative retrieval quality "
            "rather than dense full-context passes.\n"
            "Algorithm 1 describes the sparse routing update.\n"
            "Implementation details: the sparse routing encoder uses CPU-friendly batching and cached slot reuse."
        ),
    )

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert result.contributions
    assert any("sparse routing encoder" in contribution.title.lower() for contribution in result.contributions)
    assert any(len(contribution.impl_hints) >= 2 for contribution in result.contributions)
    assert any("Algorithm 1" in contribution.evidence_refs for contribution in result.contributions)
    assert any((contribution.implementation_complexity or 0) >= 4 for contribution in result.contributions)


def test_heuristic_paper_parser_reports_gap_when_only_abstract_is_available() -> None:
    request = AnalysisRequest(
        paper_source="/tmp/abstract-only-paper.pdf",
        repo_url="https://github.com/example/research-repo",
    )
    paper_document = PaperDocument(
        source_kind=PaperSourceKind.PDF_FILE,
        source_ref=request.paper_source,
        title="Compact Sparse Routing",
        abstract=(
            "We introduce a sparse routing encoder for document retrieval and show strong local validation results."
        ),
        sections=[],
        text=(
            "Compact Sparse Routing\n"
            "Abstract\n"
            "We introduce a sparse routing encoder for document retrieval and show strong local validation results."
        ),
    )

    result = HeuristicPaperParser().parse(request, paper_document)

    assert result.mode == ProcessorMode.HEURISTIC
    assert result.contributions
    assert "Paper parser did not find an explicit contributions section." in result.warnings
    assert "Paper parser did not find an explicit method section." in result.warnings
    assert "Paper parser relied on abstract-level evidence only." in result.warnings


def test_repo_tracer_extracts_repo_mentions_from_paper_document_text() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2106.09685 LoRA",
        repo_url="https://github.com/example/project",
    )
    paper_document = paper_document_from_fixture(request, load_paper_fixture("lora")).model_copy(
        update={
            "text": (
                "Our implementation builds on top of "
                "https://github.com/huggingface/transformers and TRL training utilities."
            )
        }
    )

    trace_output = StrategyDrivenRepoTracer(repo_metadata_provider=FixtureRepoMetadataProvider()).trace(
        request, paper_document, []
    )

    assert trace_output.selected_base_repo.repo_url == "https://github.com/huggingface/transformers"
    assert any(candidate.strategy == "paper_mention" for candidate in trace_output.candidates)


def test_contribution_mapper_preserves_unmatched_items_without_fixture_fallback() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    mapper = FixtureContributionMapper()

    output = mapper.map(
        request,
        contributions=[
            load_golden_case("flash-attention").contributions[0],
            PaperContribution(
                id="C2",
                title="Offline cache warmup",
                section="Appendix",
                keywords=["cache", "warmup"],
                impl_hints=["Add a cache warmup stage before training."],
            ),
        ],
        diff_clusters=load_golden_case("flash-attention").diff_clusters,
    )

    assert output.mode == ProcessorMode.HEURISTIC
    assert output.mappings
    assert output.unmatched_contribution_ids == ["C2"]
    assert output.unmatched_diff_cluster_ids == []
    assert output.mappings[0].implementation_coverage > 0
    assert output.mappings[0].coverage_type in {CoverageType.FULL, CoverageType.PARTIAL, CoverageType.APPROXIMATED}
    assert output.mappings[0].learning_entry_point is not None


def test_contribution_mapper_returns_empty_matches_with_explicit_unmatched_ids() -> None:
    request = AnalysisRequest(
        paper_source="https://arxiv.org/abs/2205.14135 Flash Attention",
        repo_url="https://github.com/Dao-AILab/flash-attention",
    )
    mapper = FixtureContributionMapper()

    output = mapper.map(
        request,
        contributions=load_golden_case("flash-attention").contributions,
        diff_clusters=[
            load_golden_case("flash-attention")
            .diff_clusters[0]
            .model_copy(
                update={
                    "id": "D9",
                    "label": "Packaging updates",
                    "summary": "Packaging updates inferred from setup.py.",
                    "files": ["setup.py"],
                    "code_anchors": [],
                }
            )
        ],
    )

    assert output.mode == ProcessorMode.HEURISTIC
    assert output.mappings == []
    assert output.unmatched_contribution_ids == ["C1"]
    assert output.unmatched_diff_cluster_ids == ["D9"]
    assert "no confident heuristic matches" in " ".join(output.warnings).lower()


def test_infer_mappings_traces_steps_and_review_order() -> None:
    contribution = PaperContribution(
        id="C7",
        title="Sparse routing encoder",
        section="Method",
        keywords=["sparse", "routing", "encoder", "slots"],
        impl_hints=[
            "Compress long documents into routing slots.",
            "Reuse cached slots during local validation.",
            "Apply temperature-scaled reranking before decoding.",
        ],
        baseline_difference="rather than dense full-context passes",
        evidence_refs=["Algorithm 1"],
    )
    diff_cluster = DiffCluster(
        id="D7",
        patch_id="cluster-patch-7",
        label="Sparse routing encoder",
        change_type=DiffChangeType.NEW_MODULE,
        files=["src/sparse_router.py", "src/train.py"],
        summary=(
            "Sparse routing encoder inferred from src/sparse_router.py; bucketed as new_module because"
            " content includes routing encoder slots and local validation cache reuse."
        ),
        code_anchors=[
            DiffCodeAnchor(
                patch_id="anchor-1",
                file_path="src/sparse_router.py",
                start_line=10,
                end_line=24,
                snippet=(
                    "def build_routing_slots(documents, temperature):\n"
                    "    sparse_slots = compress_documents_into_slots(documents)\n"
                    "    return rerank_with_temperature(sparse_slots, temperature)\n"
                ),
                original_snippet=None,
                reason="matched routing, slots, and temperature-scaled reranking logic",
                anchor_kind="addition",
            ),
            DiffCodeAnchor(
                patch_id="anchor-2",
                file_path="src/train.py",
                start_line=30,
                end_line=36,
                snippet="cached_slots = reuse_cached_slots(batch)\nreturn evaluate_with_cached_slots(cached_slots)\n",
                original_snippet=None,
                reason="matched cached slot reuse during local validation",
                anchor_kind="modification",
            ),
        ],
        semantic_tags=["routing", "encoder", "cache"],
    )

    mappings = infer_mappings([contribution], [diff_cluster])

    assert len(mappings) == 1
    assert mappings[0].learning_entry_point == "src/sparse_router.py"
    assert mappings[0].reading_order[0] == "src/sparse_router.py"
    assert "untraced implementation steps:" in " ".join(mappings[0].missing_aspects)
    assert mappings[0].implementation_coverage > 0.5
    assert mappings[0].snippet_fidelity > 0.4
    assert mappings[0].formula_fidelity > 0.2
    assert mappings[0].matched_anchor_patch_ids == ["anchor-1", "anchor-2"]
    assert any("anchor-backed evidence:" in note for note in mappings[0].fidelity_notes)


def test_infer_mappings_uses_symbolic_formula_overlap_for_fidelity() -> None:
    contribution = PaperContribution(
        id="C1",
        title="Direct preference optimization objective",
        section="Method",
        keywords=["preference", "objective"],
        impl_hints=["Optimize a temperature-scaled log-sigmoid objective over chosen and rejected responses."],
        evidence_refs=["Algorithm 1", "beta-scaled log-sigmoid preference loss"],
    )
    diff_cluster = DiffCluster(
        id="D1",
        label="Preference loss implementation",
        change_type=DiffChangeType.MODIFIED_LOSS,
        files=["trl/trainer/dpo_trainer.py"],
        summary="Implements a beta-scaled preference loss with torch.logsigmoid over chosen and rejected logits.",
        code_anchors=[
            DiffCodeAnchor(
                patch_id="anchor-formula-1",
                file_path="trl/trainer/dpo_trainer.py",
                start_line=44,
                end_line=52,
                snippet=(
                    "def dpo_loss(beta, chosen_logps, rejected_logps):\n"
                    "    margin = beta * (chosen_logps - rejected_logps)\n"
                    "    return -F.logsigmoid(margin).mean()\n"
                ),
                original_snippet=None,
                reason="matched beta-scaled preference loss over chosen and rejected responses",
                anchor_kind="addition",
            )
        ],
        semantic_tags=["loss", "preference"],
    )

    mappings = infer_mappings([contribution], [diff_cluster])

    assert len(mappings) == 1
    assert mappings[0].formula_fidelity >= 0.5
    assert any("symbolic formula overlap:" in note for note in mappings[0].fidelity_notes)
