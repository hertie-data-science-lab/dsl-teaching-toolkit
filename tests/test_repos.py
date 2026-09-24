"""Repositories: whether one exists, whether it is archived, and what a 422 from a
create actually means."""

from __future__ import annotations

import json

import pytest

from dsl_course import repos


def test_an_internal_repo_is_not_a_private_one():
    # GitHub sets the boolean `private` on an `internal` repo as well, so the two readers
    # do NOT agree and this one is the strict half. `internal` is readable by every member
    # of the enterprise, and a mark the whole institution can read is a published mark -
    # so the one thing that rides on this answer, whether a grade may be posted into a
    # repo's Submission receipts issue, has to come back no.
    assert repos.listed_is_private({"visibility": "private"}) is True
    assert repos.listed_is_private({"visibility": "internal"}) is False
    assert repos.listed_is_private({"visibility": "public"}) is False
    # A row nobody could read is not a repo the world can read.
    assert repos.listed_is_private(None) is True
    assert repos.listed_is_private({}) is True


def test_repo_is_archived_reads_the_flag_and_assumes_live_when_it_cannot(monkeypatch):
    # This gates whether the nightly refresh skips a semester, so the failure default is the
    # whole point: an unreadable repo must read as LIVE. Guessing "archived" on a transient
    # error would silently stop converging a running semester with nothing in the log to say
    # so; guessing "live" costs a loud 403 from the write itself, which is the right alarm.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, '{"archived": true}'))
    assert repos.repo_is_archived("Semester-f2025", "classroom-config") is True
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, '{"archived": false}'))
    assert repos.repo_is_archived("Semester-f2026", "classroom-config") is False
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 - bad gateway"))
    assert repos.repo_is_archived("Semester-f2026", "classroom-config") is False


def _forking(monkeypatch, answer: str | tuple[int, str]):
    """Answer the repo GET with `answer` and record every call. Returns the PATCHes."""
    calls: list[tuple[str, ...]] = []
    read = answer if isinstance(answer, tuple) else (0, answer)

    def fake_gh(*args, **k):
        calls.append(args)
        return (0, "") if "--method" in args else read

    monkeypatch.setattr(repos, "gh", fake_gh)
    return calls


def test_allow_forking_patches_a_private_repo_that_is_not_forkable_yet(monkeypatch):
    # `create_repo`'s POST takes no forking field, so this is a PATCH of its own.
    calls = _forking(monkeypatch, '{"private": true, "allow_forking": false}')
    assert repos.allow_forking("Semester-f2026", "materials") is True
    assert calls[-1] == (
        "api",
        "--method",
        "PATCH",
        "repos/Semester-f2026/materials",
        "--field",
        "allow_forking=true",
    )


def test_allow_forking_writes_nothing_when_there_is_nothing_to_change(monkeypatch):
    # The release runs every quarter of an hour, so an unconditional PATCH is 96 writes a
    # day per dest for a flag that changes once - and on a PUBLIC repo GitHub refuses the
    # field outright (422 "only be changed on org-owned private repositories"), which the
    # demo semester logged as a warning on every single tick. Both are already forkable.
    calls = _forking(monkeypatch, '{"private": false, "allow_forking": false}')
    assert repos.allow_forking("Semester-f2026", "public-materials") is True
    calls += _forking(monkeypatch, '{"private": true, "allow_forking": true}')
    assert repos.allow_forking("Semester-f2026", "already-forkable") is True
    assert [c for c in calls if "--method" in c] == []


def test_allow_forking_still_patches_a_repo_it_could_not_read(monkeypatch):
    # Fail-open, like every other read here: a 502 on the GET must not silently stop the
    # setting from converging.
    calls = _forking(monkeypatch, (1, "gh: HTTP 502 - bad gateway"))
    assert repos.allow_forking("Semester-f2026", "materials") is True
    assert [c for c in calls if "--method" in c]


def test_a_refused_forking_patch_is_a_warning_not_an_error(monkeypatch, capsys):
    # Whether the setting exists at all depends on the org's plan, and reddening a
    # quarter-hourly release for a button is how a real failure stops being noticed.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 403"))
    assert repos.allow_forking("Semester-f2026", "materials") is False
    out = capsys.readouterr().out
    assert "[warn]" in out and "forkable" in out


def test_set_visibility_patches_the_one_field_and_says_nothing_public_on_failure(
    monkeypatch, capsys
):
    # `POST /repos/{o}/{r}/generate` takes `private` and nothing else, so a public
    # assignment repo is generated private and flipped here.
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args, **k):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(repos, "gh", fake_gh)
    assert repos.set_visibility("Semester-f2026", "assignment-1-ada", "public") is True
    assert calls == [
        (
            "api",
            "--method",
            "PATCH",
            "repos/Semester-f2026/assignment-1-ada",
            "--field",
            "visibility=public",
        )
    ]
    # The repo is somebody's, so the failure line carries no name in a public log.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 403"))
    assert (
        repos.set_visibility(
            "Semester-f2026", "assignment-1-ada", "public", person=True
        )
        is False
    )
    out = capsys.readouterr()
    assert "assignment-1-ada" not in out.out + out.err
    assert "could not set a repo's visibility" in out.err


def test_one_repo_read_answers_every_question_about_it(monkeypatch):
    # Four questions about ONE object; a sweep asks several of them about the same repo,
    # and each used to be its own `GET repos/{org}/{name}`.
    reads = []
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (
            reads.append(a)
            or (0, '{"default_branch": "trunk", "private": false, "archived": true}')
        ),
    )
    assert repos.repo_exists("Org", "r")
    assert repos.repo_is_archived("Org", "r") is True
    assert repos.repo_is_private("Org", "r") is False
    assert repos.default_branch("Org", "r") == "trunk"
    assert len(reads) == 1


def test_a_failed_repo_read_is_retried_not_pinned_for_the_run(monkeypatch):
    # functools.cache does not memoise a raise, which is what keeps a 502 on one question
    # from answering every later question about that repo for the rest of the process.
    answers = [(1, "gh: HTTP 502 bad gateway"), (0, '{"default_branch": "main"}')]
    monkeypatch.setattr(repos, "gh", lambda *a, **k: answers.pop(0))
    assert repos.default_branch("Org", "r", fallback="main") == "main"
    assert repos.default_branch("Org", "r") == "main"
    assert answers == []


def test_default_branch_raises_for_a_writer_and_falls_back_for_a_reader(monkeypatch):
    # A writer would otherwise aim a commit at a branch that may not exist; a reader that
    # would just find nothing asks for the guess explicitly.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 bad gateway"))
    assert repos.default_branch("Org", "r", fallback="main") == "main"
    with pytest.raises(RuntimeError, match="default branch"):
        repos.default_branch("Org", "r")


def test_create_repo_only_treats_a_genuine_name_clash_422_as_success(monkeypatch):
    # A bare `"422" in out` swallowed an invalid-name/policy 422 as success, so the caller
    # then wrote into a repo that was never created. Only the name-clash message is success.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    assert repos.create_repo("Org", "good") is True
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (
            1,
            "HTTP 422: Validation Failed - name already exists on this account",
        ),
    )
    assert repos.create_repo("Org", "dup") is True
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (1, "HTTP 422: Validation Failed - name is invalid"),
    )
    assert repos.create_repo("Org", "bad name") is False


def test_repo_missing_is_true_only_on_a_404(monkeypatch):
    # `repo_exists` is optimistic (any failure = absent) because it answers a create-if-
    # missing question. `repo_missing` answers "may I record something permanent on the
    # strength of absence?" - so a 5xx or a rate limit is neither present nor absent.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.repo_missing("O", "r")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "HTTP 502 bad gateway"))
    assert not repos.repo_missing("O", "r")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, "{}"))
    assert not repos.repo_missing("O", "r")


def test_is_collaborator_asks_for_direct_grants_only(monkeypatch):
    # `GET /collaborators/{login}` 204s for anyone who can reach the repo AT ALL - through
    # a team, or by being an org owner - so it answers "has access", not "is a direct
    # collaborator". Its one caller revokes on the answer, and only a direct grant is
    # revocable: reading team access as a direct grant reported revokes that removed
    # nothing, on repos named after a handle nobody had ever been granted directly.
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        repos, "gh", lambda *a, **k: seen.append(a) or (0, "Ada-L\nhertie-dsl-bot\n")
    )
    assert repos.is_collaborator("Semester", "assignment-1-ada-l", "ada-l") is True
    assert any("affiliation=direct" in a for a in seen[0])
    assert repos.is_collaborator("Semester", "assignment-1-ada-l", "zoe-z") is False

    # An unreadable answer is neither - the caller must not revoke on a rate limit.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 bad gateway"))
    assert repos.is_collaborator("Semester", "assignment-1-ada-l", "ada-l") is None
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.is_collaborator("Semester", "gone", "ada-l") is False


def test_pending_invitations_picks_out_one_logins_ids(monkeypatch):
    # GitHub logins are case-insensitive, and the listing carries everyone's invitations -
    # cancelling the wrong one revokes a colleague's access to a repo they still need.
    listing = "12\tZoe-Z\n13\tada-l\n14\tzoe-z\n"
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, listing))
    assert repos.pending_invitations("O", "r", "zoe-z") == ["12", "14"]
    assert repos.pending_invitations("O", "r", "nobody") == []


def test_an_unreadable_invitation_listing_is_none_not_empty(monkeypatch):
    # The caller is about to revoke: a rate limit must never read as "nothing to cancel".
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "HTTP 403: rate limited"))
    assert repos.pending_invitations("O", "r", "zoe-z") is None
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.pending_invitations("O", "r", "zoe-z") == []


def test_a_repo_named_after_a_student_is_not_announced_in_a_public_log(
    monkeypatch, capsys
):
    # `grades-<handle>` and `<slug>-<handle>` name a student. These workflows run in the
    # org's PUBLIC `.github`, so the happy path must be silent without DSL_VERBOSE - the
    # skip branch already was, the create branch was not.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    assert repos.create_repo("Semester-f2026", "grades-ada-l", person=True) is True
    assert "ada-l" not in capsys.readouterr().out

    monkeypatch.setenv("DSL_VERBOSE", "1")
    assert repos.create_repo("Semester-f2026", "grades-ada-l", person=True) is True
    assert "ada-l" in capsys.readouterr().out


def test_a_repo_named_after_nobody_is_still_announced(monkeypatch, capsys):
    # The flag must not silence the ordinary org fixtures - bootstrap and scaffold read
    # these lines to know what they just built.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    assert repos.create_repo("Course-Org", "welcome") is True
    assert "repo created: Course-Org/welcome" in capsys.readouterr().out


def test_an_existing_student_repo_is_not_named_either(monkeypatch, capsys):
    # The already-exists race: `assign` checks first, but two hourly ticks can overlap.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (1, "HTTP 422: name already exists on this account"),
    )
    assert (
        repos.generate_from_template(
            template_org="Course-Org",
            template_name="a1-template",
            owner="Semester-f2026",
            name="a1-ada-l",
            person=True,
        )
        is True
    )
    assert "ada-l" not in capsys.readouterr().out


def test_a_failed_student_repo_create_still_names_it(monkeypatch, capsys):
    # The carve-out: an error a faculty member must act on is worse unactionable than named.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "HTTP 403: forbidden"))
    assert repos.create_repo("Semester-f2026", "grades-ada-l", person=True) is False
    assert "grades-ada-l" in capsys.readouterr().err


def _rulesets(
    monkeypatch, existing: tuple[int, str], post: tuple[int, str] = (0, "{}")
):
    """Answer the rulesets GET with `existing` and the POST with `post`. Returns the
    calls, each as `(args, stdin)`."""
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def fake_gh(*args, stdin=None, **k):
        calls.append((args, stdin))
        return post if "POST" in args else existing

    monkeypatch.setattr(repos, "gh", fake_gh)
    return calls


def test_the_drop_box_ruleset_forbids_force_push_and_deletion_on_the_default_branch(
    monkeypatch,
):
    calls = _rulesets(monkeypatch, (0, ""))
    assert repos.protect_shared_repo("Semester", "assignment-3-submissions") is True
    (read, _), (write, body) = calls
    assert read[1] == "--paginate"
    assert "repos/Semester/assignment-3-submissions/rulesets" in read[2]
    assert write[:4] == (
        "api",
        "--method",
        "POST",
        "repos/Semester/assignment-3-submissions/rulesets",
    )
    sent = json.loads(body)
    assert sent["name"] == repos.DROP_BOX_RULESET
    assert sent["target"] == "branch" and sent["enforcement"] == "active"
    # No bypass: a listed actor is one more account that may rewrite the semester's work.
    assert sent["bypass_actors"] == []
    # The branch is named by GitHub's own alias, not by "main" - the drop box inherits its
    # default branch from the course template, and a ruleset on the wrong branch is none.
    assert sent["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert {r["type"] for r in sent["rules"]} == {"deletion", "non_fast_forward"}


def test_the_drop_box_ruleset_is_not_posted_twice(monkeypatch):
    # The handout re-fires on every tick, so a second POST would 422 on the name for the
    # rest of the term. One listing answers it.
    calls = _rulesets(monkeypatch, (0, f"some-other-rule\n{repos.DROP_BOX_RULESET}\n"))
    assert repos.protect_shared_repo("Semester", "assignment-3-submissions") is True
    assert len(calls) == 1


def test_a_drop_box_that_could_not_be_protected_says_what_it_costs(monkeypatch, capsys):
    _rulesets(monkeypatch, (1, "gh: HTTP 502"), post=(1, "gh: HTTP 403 - forbidden"))
    assert repos.protect_shared_repo("Semester", "assignment-3-submissions") is False
    err = capsys.readouterr().err
    assert "erase the whole semester's work" in err


def test_direct_collaborators_unions_collaborators_and_un_accepted_invitations(
    monkeypatch,
):
    # An invited-but-not-yet-joined student is NOT a collaborator row, so a set built from
    # the collaborators alone would re-grant them on every tick until they accept.
    asked: list[str] = []

    def fake_gh(*args, **k):
        asked.append(args[2])
        return (0, "Anna\nBot\n" if "collaborators" in args[2] else "Late-Joiner\n")

    monkeypatch.setattr(repos, "gh", fake_gh)
    assert repos.direct_collaborators(
        "Semester", "assignment-3-submissions"
    ) == frozenset({"anna", "bot", "late-joiner"})
    assert len(asked) == 2


def test_direct_collaborators_cannot_answer_when_either_listing_fails(
    monkeypatch, capsys
):
    # None, never the empty set: the caller grants everyone again on None, and would grant
    # nobody on an empty set it read as "nothing is there".
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    assert repos.direct_collaborators("Semester", "assignment-3-submissions") is None
    assert "could not read Semester/assignment-3-submissions's collaborators" in (
        capsys.readouterr().err
    )


def test_a_plan_refusal_of_the_drop_box_ruleset_warns_but_does_not_fail(
    monkeypatch, capsys
):
    """Rulesets on a private repo need GitHub Team; on Free the handout must stay green
    and say plainly that the drop box is unprotected."""
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append(args)
        return (
            1,
            "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)",
        )

    monkeypatch.setattr(repos, "gh", fake_gh)
    assert repos.protect_shared_repo("org", "a1-submissions") is True
    err = capsys.readouterr().err
    assert "NOT protected" in err and "GitHub Team" in err
    assert len(calls) == 2  # the read, then the refused POST


def test_any_other_ruleset_failure_is_still_counted(monkeypatch, capsys):
    monkeypatch.setattr(repos, "gh", lambda *a, stdin=None: (1, "HTTP 502 bad gateway"))
    assert repos.protect_shared_repo("org", "a1-submissions") is False


def test_a_visibility_patch_waits_out_a_repo_still_being_created(monkeypatch):
    """Right after `generate` GitHub refuses the PATCH with 422 "still in progress"; the
    flip retries on that answer alone and succeeds once the repo has settled."""
    answers = iter(
        [
            (
                1,
                "Failed to update visibility. A previous repository operation is still in progress. (HTTP 422)",
            ),
            (
                1,
                "Failed to update visibility. A previous repository operation is still in progress. (HTTP 422)",
            ),
            (0, "public"),
        ]
    )
    slept = []
    monkeypatch.setattr(repos, "gh", lambda *a, **k: next(answers))
    monkeypatch.setattr(repos.time, "sleep", slept.append)
    assert repos.set_visibility("org", "a1-ada", "public") is True
    assert slept == [repos._SETTLE_DELAY, repos._SETTLE_DELAY]


def test_any_other_visibility_refusal_is_not_retried(monkeypatch, capsys):
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return 1, "HTTP 403 Forbidden"

    monkeypatch.setattr(repos, "gh", fake)
    monkeypatch.setattr(repos.time, "sleep", lambda s: None)
    assert repos.set_visibility("org", "a1-ada", "public") is False
    assert len(calls) == 1


# The three refusals that mean "the repo is busy, ask again" - one per live shape seen on
# 2026-09-17: a PATCH straight after `generate`, a grant straight after a visibility flip,
# and a topics PUT/DELETE in either window.
SETTLING_ANSWERS = (
    "Failed to update visibility. A previous repository operation is still in progress. (HTTP 422)",
    (
        '{"errors":[{"resource":"TeamRepository","code":"unprocessable",'
        '"message":"This repository is locked and cannot be modified."}]}'
    ),
    "A conflicting repository operation is still in progress. (HTTP 409)",
)


@pytest.mark.parametrize("answer", SETTLING_ANSWERS)
def test_the_settle_retry_waits_out_every_answer_that_means_busy(monkeypatch, answer):
    answers = iter([(1, answer), (1, answer), (0, "done")])
    slept: list[float] = []
    monkeypatch.setattr(repos, "gh", lambda *a, **k: next(answers))
    monkeypatch.setattr(repos.time, "sleep", slept.append)
    assert repos.gh_settled("api", "-X", "PUT", "whatever") == (0, "done")
    assert slept == [repos._SETTLE_DELAY, repos._SETTLE_DELAY]


def test_the_settle_retry_ignores_any_other_refusal(monkeypatch):
    # A 403 is a scope or a permission problem: waiting a minute cannot fix it, and
    # retrying would turn one wrong answer into a minute of them on every repo.
    calls = []
    monkeypatch.setattr(
        repos, "gh", lambda *a, **k: calls.append(a) or (1, "gh: HTTP 403 Forbidden")
    )
    monkeypatch.setattr(repos.time, "sleep", lambda s: None)
    assert repos.gh_settled("api", "-X", "PUT", "whatever")[0] == 1
    assert len(calls) == 1


def test_the_settle_retry_gives_up_after_the_whole_window(monkeypatch):
    # It has to END: a repo that is still locked a minute later is a real failure, and the
    # caller's own error line is what says so.
    calls = []
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: calls.append(a) or (1, SETTLING_ANSWERS[1]),
    )
    monkeypatch.setattr(repos.time, "sleep", lambda s: None)
    assert repos.gh_settled("api", "-X", "PUT", "whatever")[0] == 1
    assert len(calls) == repos._SETTLE_ATTEMPTS


def test_a_collaborator_grant_waits_out_a_locked_repo(monkeypatch, capsys):
    # Live 2026-09-17: the flip to public locked the repo, and the collaborator PUT a
    # second later came back "not a real account?" - the student was left off their own
    # assignment repo and the handout recorded `failed-no-collaborator`.
    answers = iter([(1, SETTLING_ANSWERS[1]), (1, SETTLING_ANSWERS[1]), (0, "")])
    monkeypatch.setattr(repos, "gh", lambda *a, **k: next(answers))
    monkeypatch.setattr(repos.time, "sleep", lambda s: None)
    assert (
        repos.add_collaborator("Semester-f2026", "a1-ada", "ada", person=True) is True
    )
    assert capsys.readouterr().err == ""


def test_a_topics_put_waits_out_a_locked_repo(monkeypatch):
    answers = iter([(1, SETTLING_ANSWERS[2]), (0, "")])
    monkeypatch.setattr(repos, "gh", lambda *a, **k: next(answers))
    monkeypatch.setattr(repos.time, "sleep", lambda s: None)
    assert repos.set_repo_topics("Semester-f2026", "a1-ada", ["Assignment"]) is True
