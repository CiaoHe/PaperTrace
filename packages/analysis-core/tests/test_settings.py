from __future__ import annotations

from papertrace_core.settings import Settings


def test_live_defaults_can_enable_all_remote_stages() -> None:
    settings = Settings.model_validate({"ENABLE_LIVE_BY_DEFAULT": True})

    assert settings.use_live_paper_fetch() is True
    assert settings.use_live_repo_trace() is True
    assert settings.use_live_repo_analysis() is True


def test_explicit_stage_flags_still_work_when_global_default_is_disabled() -> None:
    settings = Settings.model_validate(
        {
            "ENABLE_LIVE_PAPER_FETCH": True,
            "ENABLE_LIVE_REPO_TRACE": True,
            "ENABLE_LIVE_REPO_ANALYSIS": True,
        }
    )

    assert settings.use_live_paper_fetch() is True
    assert settings.use_live_repo_trace() is True
    assert settings.use_live_repo_analysis() is True
