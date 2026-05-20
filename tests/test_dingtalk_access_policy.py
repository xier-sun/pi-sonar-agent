from __future__ import annotations

from types import SimpleNamespace

from pi_sonar_agent.core.dingtalk_access_policy import DingTalkAccessPolicy


def test_access_policy_rejects_non_whitelisted_user() -> None:
    policy = DingTalkAccessPolicy(allowed_staff_ids=("staff-1",))
    store = SimpleNamespace(
        count_active_jobs_for_user=lambda _staff_id: 0,
        count_recent_jobs_for_user=lambda _staff_id, *, window_seconds: 0,
    )

    decision = policy.evaluate_trigger(
        job_store=store,
        sender_staff_id="staff-2",
        conversation_id="conv-1",
    )

    assert decision.allowed is False
    assert decision.status == "unauthorized"


def test_access_policy_rate_limits_active_jobs_and_window_counts() -> None:
    policy = DingTalkAccessPolicy(
        max_active_jobs_per_user=1,
        max_jobs_per_window=2,
        window_seconds=600,
    )
    active_store = SimpleNamespace(
        count_active_jobs_for_user=lambda _staff_id: 1,
        count_recent_jobs_for_user=lambda _staff_id, *, window_seconds: 0,
    )
    window_store = SimpleNamespace(
        count_active_jobs_for_user=lambda _staff_id: 0,
        count_recent_jobs_for_user=lambda _staff_id, *, window_seconds: 2,
    )

    active = policy.evaluate_trigger(
        job_store=active_store,
        sender_staff_id="staff-1",
        conversation_id="conv-1",
    )
    window = policy.evaluate_trigger(
        job_store=window_store,
        sender_staff_id="staff-1",
        conversation_id="conv-1",
    )

    assert active.allowed is False
    assert active.status == "rate_limited"
    assert window.allowed is False
    assert window.status == "rate_limited"


def test_access_policy_allows_creator_or_admin_to_cancel_job() -> None:
    policy = DingTalkAccessPolicy(admin_staff_ids=("admin-1",))
    job = SimpleNamespace(trigger_user_id="staff-1")

    assert policy.can_cancel_job(job=job, requester_staff_id="staff-1") is True
    assert policy.can_cancel_job(job=job, requester_staff_id="admin-1") is True
    assert policy.can_cancel_job(job=job, requester_staff_id="staff-2") is False
