from __future__ import annotations

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
from papertrace_core.storage import _enrich_cluster_code_anchors


def test_enrich_cluster_code_anchors_backfills_missing_snippets() -> None:
    result = AnalysisResult(
        case_slug="example",
        summary="example",
        selected_base_repo=BaseRepoCandidate(
            repo_url="https://github.com/example/base",
            strategy="test",
            confidence=0.9,
            evidence="test",
        ),
        base_repo_candidates=[],
        contributions=[
            PaperContribution(
                id="C1",
                title="Low-rank adaptation modules",
                section="Section 3",
                keywords=["adapter", "rank"],
                impl_hints=["Inject trainable low-rank modules."],
            )
        ],
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
    )
    cluster = DiffCluster(
        id="D1",
        label="LoRA adapter modules",
        change_type=DiffChangeType.NEW_MODULE,
        files=["src/lora.py"],
        summary="Adds low-rank adapter wiring.",
        code_anchors=[],
        semantic_tags=[],
    )

    enriched = _enrich_cluster_code_anchors(
        cluster,
        base_snapshot={"src/lora.py": "class Linear:\n    pass\n"},
        target_snapshot={
            "src/lora.py": (
                "class Linear:\n"
                "    pass\n\n"
                "class LoraLinear:\n"
                "    def __init__(self, rank):\n"
                "        self.rank = rank\n"
            )
        },
        result=result,
    )

    assert enriched.code_anchors
    assert enriched.code_anchors[0].file_path == "src/lora.py"
    assert "LoraLinear" in enriched.code_anchors[0].snippet
    assert enriched.code_anchors[0].anchor_kind in {"addition", "modification"}
