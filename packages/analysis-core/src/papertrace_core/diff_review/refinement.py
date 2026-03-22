from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from papertrace_core.diff_review.locks import review_build_lock
from papertrace_core.diff_review.models import (
    ReviewFileEntry,
    ReviewManifest,
    ReviewRefinementStatus,
    StoredReviewFilePayload,
)
from papertrace_core.diff_review.projection import (
    ReviewProjection,
    project_analysis_result_from_review,
    project_review_links,
)
from papertrace_core.diff_review.retrieval import (
    ClaimHunkLink,
    ReviewCandidateInput,
    build_hunk_candidates,
    retrieve_claim_hunk_links,
)
from papertrace_core.llm import _extract_json_block, build_llm_client
from papertrace_core.settings import get_settings
from papertrace_core.storage import (
    get_job_result,
    get_review_artifact_dir,
    get_review_manifest,
    mark_review_refinement_status,
    replace_job_result,
    update_review_file_payload,
    update_review_manifest,
)

ALLOWED_VERDICTS = {"supports", "partial", "unrelated"}


def refine_review_links_for_job(job_id: str) -> ReviewManifest | None:
    settings = get_settings()
    llm_client = build_llm_client(settings)
    manifest = get_review_manifest(job_id)
    result = get_job_result(job_id)
    artifact_dir = get_review_artifact_dir(job_id)
    if manifest is None or result is None or artifact_dir is None:
        return None
    if llm_client is None:
        if manifest.refinement_status != ReviewRefinementStatus.DISABLED:
            updated = manifest.model_copy(update={"refinement_status": ReviewRefinementStatus.DISABLED})
            update_review_manifest(job_id, updated)
            mark_review_refinement_status(job_id, ReviewRefinementStatus.DISABLED, detail="LLM refinement disabled.")
        return updated if "updated" in locals() else manifest

    with review_build_lock(manifest.cache_key, settings) as acquired:
        if not acquired:
            mark_review_refinement_status(job_id, ReviewRefinementStatus.QUEUED, detail="Refinement waiting for lock.")
            return manifest
        mark_review_refinement_status(job_id, ReviewRefinementStatus.RUNNING, detail="Refining claim-hunk links.")
        running_manifest = manifest.model_copy(update={"refinement_status": ReviewRefinementStatus.RUNNING})
        update_review_manifest(job_id, running_manifest)
        try:
            candidate_inputs = _load_review_candidate_inputs(artifact_dir, running_manifest)
            retrieval = retrieve_claim_hunk_links(
                claim_entries=running_manifest.claim_index,
                contributions=result.contributions,
                candidate_hunks=build_hunk_candidates(result, candidate_inputs, settings),
            )
            accepted_links = _refine_links_with_llm(
                llm_client=llm_client,
                manifest=running_manifest,
                result=result,
                candidates_by_claim_id=retrieval.candidates_by_claim_id,
            )
            projection = project_review_links(
                claim_entries=running_manifest.claim_index,
                links=accepted_links,
                candidate_links_by_claim_id=retrieval.candidates_by_claim_id,
                refinement_status=ReviewRefinementStatus.READY,
            )
            refined_manifest = _apply_projection_to_manifest(running_manifest, projection)
            refined_manifest = refined_manifest.model_copy(update={"refinement_status": ReviewRefinementStatus.READY})
            _persist_refined_file_payloads(job_id, artifact_dir, refined_manifest, projection)
            update_review_manifest(job_id, refined_manifest)
            projected_result = project_analysis_result_from_review(
                result,
                projection.claim_entries,
                accepted_links,
            )
            replace_job_result(job_id, projected_result)
            mark_review_refinement_status(job_id, ReviewRefinementStatus.READY, detail="Refinement complete.")
            return refined_manifest
        except Exception as exc:
            failed_manifest = running_manifest.model_copy(update={"refinement_status": ReviewRefinementStatus.FAILED})
            update_review_manifest(job_id, failed_manifest)
            mark_review_refinement_status(
                job_id,
                ReviewRefinementStatus.FAILED,
                detail=f"Refinement failed: {exc}",
            )
            return failed_manifest


def _load_review_candidate_inputs(artifact_dir: Path, manifest: ReviewManifest) -> list[ReviewCandidateInput]:
    entry_by_id = {
        entry.file_id: entry
        for entry in [
            *manifest.review_queue,
            *[entry for bucket in manifest.secondary_buckets.values() for entry in bucket.files],
        ]
    }
    inputs: list[ReviewCandidateInput] = []
    raw_dir = artifact_dir / "raw"
    for file_id, entry in entry_by_id.items():
        raw_diff_path = raw_dir / f"{file_id}.diff"
        if not raw_diff_path.exists():
            continue
        file_path = entry.current_path or entry.source_path
        if not file_path:
            continue
        inputs.append(
            ReviewCandidateInput(
                file_id=file_id,
                file_path=file_path,
                language=entry.language,
                raw_unified_diff=raw_diff_path.read_text(encoding="utf-8"),
            )
        )
    return inputs


def _refine_links_with_llm(
    *,
    llm_client: Any,
    manifest: ReviewManifest,
    result: Any,
    candidates_by_claim_id: dict[str, list[ClaimHunkLink]],
) -> list[ClaimHunkLink]:
    contributions_by_id = {contribution.id: contribution for contribution in result.contributions}
    accepted_links: list[ClaimHunkLink] = []
    for claim in manifest.claim_index:
        candidates = candidates_by_claim_id.get(claim.claim_id, [])
        if not candidates:
            continue
        contribution = contributions_by_id.get(claim.contribution_id)
        if contribution is None:
            continue
        decisions = _request_refinement_decisions(
            llm_client=llm_client,
            claim=claim.model_dump(mode="json"),
            contribution=contribution.model_dump(mode="json"),
            candidates=candidates,
        )
        verdict_by_hunk_id = {item["hunk_id"]: item["verdict"] for item in decisions}
        accepted = [
            candidate
            for candidate in candidates
            if verdict_by_hunk_id.get(candidate.hunk_id) in {"supports", "partial"}
        ]
        accepted_links.extend(accepted)
    return accepted_links


def _request_refinement_decisions(
    *,
    llm_client: Any,
    claim: dict[str, Any],
    contribution: dict[str, Any],
    candidates: list[ClaimHunkLink],
) -> list[dict[str, str]]:
    prompt = (
        "Judge which retrieved diff hunks actually support the paper claim.\n"
        "Return JSON only as an array of objects with keys: hunk_id, verdict, reason.\n"
        "verdict must be one of: supports, partial, unrelated.\n"
        "Do not invent new hunk ids. Only classify the provided candidates.\n\n"
        f"Contribution: {json.dumps(contribution, ensure_ascii=False)}\n"
        f"Claim: {json.dumps(claim, ensure_ascii=False)}\n"
        "Candidates: "
        f"{json.dumps([_candidate_prompt_payload(candidate) for candidate in candidates], ensure_ascii=False)}\n"
    )
    response = llm_client.client.chat.completions.create(
        model=llm_client.model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You verify code-review evidence for ML paper claims. Return JSON only and stay conservative."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    payload = _extract_json_block(response.choices[0].message.content or "[]")
    if not isinstance(payload, list):
        raise ValueError("LLM refinement did not return a JSON array.")
    validated: list[dict[str, str]] = []
    valid_hunk_ids = {candidate.hunk_id for candidate in candidates}
    for item in payload:
        if not isinstance(item, dict):
            continue
        hunk_id = str(item.get("hunk_id") or "").strip()
        verdict = str(item.get("verdict") or "").strip().lower()
        if hunk_id not in valid_hunk_ids or verdict not in ALLOWED_VERDICTS:
            continue
        validated.append(
            {
                "hunk_id": hunk_id,
                "verdict": verdict,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return validated


def _candidate_prompt_payload(candidate: ClaimHunkLink) -> dict[str, Any]:
    return {
        "hunk_id": candidate.hunk_id,
        "file_id": candidate.file_id,
        "file_path": candidate.file_path,
        "cluster_ids": list(candidate.cluster_ids),
        "snippet": candidate.snippet[:1200],
        "score": round(candidate.score, 4),
    }


def _apply_projection_to_manifest(manifest: ReviewManifest, projection: ReviewProjection) -> ReviewManifest:
    def update_entry(entry: ReviewFileEntry) -> ReviewFileEntry:
        file_projection = projection.file_links.get(entry.file_id)
        if file_projection is None:
            return entry.model_copy(
                update={
                    "linked_claim_count": 0,
                    "linked_claim_ids": [],
                    "linked_contribution_keys": [],
                }
            )
        return entry.model_copy(
            update={
                "linked_claim_count": len(file_projection.linked_claim_ids),
                "linked_claim_ids": file_projection.linked_claim_ids,
                "linked_contribution_keys": file_projection.linked_contribution_keys,
            }
        )

    updated_review_queue = sorted(
        [update_entry(entry) for entry in manifest.review_queue],
        key=lambda item: (
            -(1 if item.linked_claim_count > 0 else 0),
            -_significance_rank(item.significance),
            -item.stats.changed_line_count,
            item.current_path or item.source_path or "",
        ),
    )
    updated_secondary = {
        key: bucket.model_copy(update={"files": [update_entry(entry) for entry in bucket.files]})
        for key, bucket in manifest.secondary_buckets.items()
    }
    return manifest.model_copy(
        update={
            "review_queue": updated_review_queue,
            "secondary_buckets": updated_secondary,
            "claim_index": projection.claim_entries,
            "contribution_status": projection.contribution_status,
        }
    )


def _persist_refined_file_payloads(
    job_id: str,
    artifact_dir: Path,
    manifest: ReviewManifest,
    projection: ReviewProjection,
) -> None:
    entry_by_id = {
        entry.file_id: entry
        for entry in [
            *manifest.review_queue,
            *[entry for bucket in manifest.secondary_buckets.values() for entry in bucket.files],
        ]
    }
    for file_id, entry in entry_by_id.items():
        file_path = artifact_dir / "files" / f"{file_id}.json"
        if not file_path.exists():
            continue
        stored = StoredReviewFilePayload.model_validate_json(file_path.read_text(encoding="utf-8"))
        stored.linked_claim_ids = list(entry.linked_claim_ids)
        file_projection = projection.file_links.get(file_id)
        stored.linked_cluster_ids = list(file_projection.linked_cluster_ids) if file_projection is not None else []
        stored.hunks = [
            hunk.model_copy(
                update={
                    "linked_claim_ids": (
                        list(projection.hunk_links[hunk.hunk_id].linked_claim_ids)
                        if hunk.hunk_id in projection.hunk_links
                        else []
                    ),
                    "linked_contribution_keys": (
                        list(projection.hunk_links[hunk.hunk_id].linked_contribution_keys)
                        if hunk.hunk_id in projection.hunk_links
                        else []
                    ),
                }
            )
            for hunk in stored.hunks
        ]
        update_review_file_payload(job_id, file_id, stored)


def _significance_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)
