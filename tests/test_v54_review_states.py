"""Backend state projection separates pending, suggestion and sheet qualification."""

from types import SimpleNamespace
from stability_hub.review_tasks import candidate_states
from stability_hub.v5_contracts import ReviewTask


def candidate(lifecycle="draft", status="new", issues=()):
    return SimpleNamespace(id="c1", lifecycle=lifecycle, status=status, issues=issues)


def state(c, tasks=(), changes=()):
    return candidate_states(SimpleNamespace(candidates=[c]), tasks, changes)["c1"]


def task(tid="t1", suggestion=False):
    return ReviewTask(
        id=tid,
        code="identity",
        section="Sheet",
        detail="same shared source",
        members=({"candidate_id": "c1"},),
        actions=("edit",),
        base_revision=1,
        blockers=("Sheet|candidate:c1",),
        proposal={"eligible": suggestion},
    )


def test_no_suggestion_does_not_imply_no_problem_and_suggestion_does_not_resolve_others():
    plain = state(candidate(), [task()])
    assert plain["state"] == "pending" and plain["has_unresolved"] and not plain["suggestion_ids"]
    mixed = state(candidate(), [task(suggestion=True), task("t2")])
    assert mixed["state"] == "suggested" and mixed["has_unresolved"] and len(mixed["task_ids"]) == 2


def test_saved_excluded_and_committed_are_distinct_from_normal_pending():
    assert state(candidate())["state"] == "normal_pending"
    saved = state(candidate(), changes=[{"candidate_ids": ["c1"]}])
    assert saved["modified"] and saved["state"] == "saved_draft"
    assert state(candidate("withdrawn", issues=("old",)))["state"] == "excluded"
    assert state(candidate("submitted"))["state"] == "committed"
    unresolved = state(candidate(status="needs_review"), changes=[{"candidate_ids": ["c1"]}])
    assert unresolved["modified"] and unresolved["state"] == "pending"
