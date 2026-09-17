"""The five submission shapes one live run drives, and the config that declares each one.

An assignment's SHAPE is `submit_via` x `visibility`, and the five combinations the engine
acts on behave differently at every stage of the pipeline: what the handout creates, where
a student pushes, whether there is a Feedback issue, what the site page says. A harness
that drove one of them proved the wiring for one of them.

So the run hands out FIVE assignments in one pass, serially, over the same roster and the
same cohort. Each gets its own slug - `<namespace>-<shape name>` - which keeps every repo
inside the one namespace `cleanup` sweeps, and keeps the five apart everywhere a name is
the key: the schedule entry, the grading sheet, the snapshot, the gradebook section.

The shape reaches the engine through the template's `grading_config.yml` on the solution
branch, which is the one place any of it is declared. The New assignment form writes most
of it from its own boxes; `configure` below writes what the form cannot say (`submit_url`)
and states the rest outright, so the file the run hands out under is this table's and not
the form's. The edits are pure text - `read_config` and `write_config` are the only part
that talks to GitHub, exactly as in `schedule_edit`.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from dsl_course import course, gh_contents, ghcli, grades

from . import cleanup

# Where an `external` assignment says the work is handed in. A real https URL because the
# reader refuses anything else (and refuses the scaffold's `CHANGE-ME` placeholder), and a
# deliberately inert host because the value is rendered as a `Submit on ...` button on the
# demo cohort's site for as long as the run lasts.
SUBMIT_URL = "https://example.org/e2e/submit"


@dataclass(frozen=True)
class Shape:
    """One submission shape, as the run hands it out.

    `name` is the slug suffix AND the key every recorded fact is filed under, so a failure
    names the shape rather than a repo. `visibility` is empty where the shape's config
    carries none at all - `external` and `shared` both drop the key, so writing one would
    be writing a line the parser throws away."""

    name: str
    submit_via: str
    visibility: str = ""
    submit_url: str = ""
    autograde: bool = False

    @property
    def key(self) -> str:
        """`course.submit_shape` for this shape - the one word the site page branches on.

        Asked of the engine rather than spelt here, so the front matter this run asserts
        against is the vocabulary the toolkit actually writes."""
        return course.submit_shape(self.submit_via, self.visibility or "private")

    @property
    def creates_unit_repos(self) -> bool:
        """Whether this shape hands each student a repo of their own."""
        return course.creates_unit_repos(self.submit_via)

    @property
    def collects_commits(self) -> bool:
        """Whether there is anything for the student to push - and so anything for the
        cutoff to freeze."""
        return course.collects_commits(self.submit_via)

    @property
    def has_feedback_issue(self) -> bool:
        """Whether a student's feedback has a Feedback issue to go on."""
        return course.has_feedback_issue(self.submit_via, self.visibility or "private")

    @property
    def submit_shared(self) -> bool:
        """Whether the whole cohort hands in to ONE repo."""
        return self.submit_via == "shared"

    def repo(self, run_id: str, handle: str) -> str:
        """The repo this handle's work lands in - `''` where the shape creates none.

        Composed by `course`, not spelt here: the drop box and the per-unit repo are named
        by the same two functions the provisioner names them with, so a rename there
        cannot leave this harness asserting against a repo nobody creates."""
        if self.submit_shared:
            return course.shared_repo(slug(run_id, self))
        if self.creates_unit_repos:
            return course.submission_repo(slug(run_id, self), handle)
        return ""

    def folder(self, handle: str) -> str:
        """The folder INSIDE that repo this handle's work goes in - `<handle>/` in a drop
        box, nothing where the unit has a repo of its own. `collect.Target.path`'s rule,
        from the outside."""
        return f"{handle}/" if self.submit_shared else ""


# The five, in the order the run drives them. `private` is first and carries the
# autograding, because it is the shape every assertion this harness made before the others
# existed was written against: the run must still prove that one end to end.
SHAPES = (
    Shape("private", "github", "private", autograde=True),
    Shape("public", "github", "public"),
    Shape("student-choice", "github", "student_choice"),
    Shape("external", "external", submit_url=SUBMIT_URL),
    Shape("shared", "shared"),
)

BY_NAME = {shape.name: shape for shape in SHAPES}


def slug(run_id: str, shape: Shape) -> str:
    """The assignment slug this shape hands out under.

    A suffix of the run's namespace, so `cleanup.is_run_repo` already owns it and every
    repo hanging off it - `<slug>-<handle>`, `<slug>-submissions` - without a second rule
    that could disagree with the first."""
    return f"{cleanup.slug(run_id)}-{shape.name}"


def title(run_id: str, shape: Shape) -> str:
    """The assignment's name, which is also the heading its section gets in the student's
    gradebook README. One per shape, so five sections in one file can be told apart."""
    return f"E2E {shape.name} {run_id}"


# One `key: value` line of `grading_config.yml`, live or commented out. The scaffold pads
# the setting to a column and puts its explanation after ` # `, and an instructor editing
# the file by hand keeps that - so the rewrite below does too.
_SETTING = re.compile(r"^(?P<off>#\s*)?(?P<key>[a-z_]+):(?P<rest>.*)$")
_COLUMN = 29
_COMMENT = " # "


def set_setting(text: str, key: str, value: str) -> str:
    """`text` with `key` LIVE and set to `value`, its explanation kept.

    What an instructor does to a seeded `grading_config.yml`: type over the value, and
    uncomment the line if the scaffold left it commented (which is how `submit_url`
    arrives - see `scaffold._grading_config`).

    A key that is not in the file, or is in it twice, RAISES rather than being appended:
    this file decides what the handout creates, and a run that silently added a setting
    the scaffold never writes would be testing a file no instructor could have."""
    out: list[str] = []
    hits = 0
    for line in text.splitlines():
        match = _SETTING.match(line)
        if match is None or match.group("key") != key:
            out.append(line)
            continue
        hits += 1
        _was, sep, comment = match.group("rest").partition(_COMMENT)
        setting = f"{key}: {value}"
        out.append(f"{setting:<{_COLUMN}}{sep}{comment}".rstrip() if sep else setting)
    if hits != 1:
        raise ValueError(
            f"`{key}:` appears {hits} time(s) at the top level of this grading_config.yml "
            f"- expected exactly one line to edit"
        )
    return "\n".join(out) + "\n"


def configure(text: str, shape: Shape) -> str:
    """The seeded `grading_config.yml` as an instructor would leave it for `shape`.

    Every value the shape is made of is written HERE, including the two the New assignment
    form also asks for: the form's boxes are one way to say it and this file is the only
    thing the engine reads, so the run states the shape outright rather than trusting the
    dropdown it clicked. Idempotent, so a config the form already got right comes back
    byte for byte and the harness commits nothing.

    `visibility` is written only where the shape has one: `external` and `shared` carry no
    per-unit repo for it to describe, the parser drops the key there, and the scaffold
    leaves the line commented out for exactly that reason."""
    out = set_setting(text, "submit_via", shape.submit_via)
    if shape.visibility:
        out = set_setting(out, "visibility", shape.visibility)
    if shape.submit_url:
        out = set_setting(out, "submit_url", shape.submit_url)
    return out


def read_config(course_org: str, slug: str) -> tuple[str, str]:
    """`(text, blob sha)` of one template's `grading_config.yml`, off the SOLUTION branch.

    That branch and no other: the solution branch is where the definition lives
    (`grades.load_grading_spec`), and a config read from `main` would be a file the engine
    never looks at."""
    read = gh_contents.get_file_with_sha(
        course_org, slug, grades.GRADING_FILE, course.SOLUTION_BRANCH
    )
    if read is None:
        raise RuntimeError(
            f"{course_org}/{slug} has no {grades.GRADING_FILE} on its "
            f"{course.SOLUTION_BRANCH} branch - did New assignment finish?"
        )
    return read


def write_config(course_org: str, slug: str, text: str, sha: str) -> None:
    """Write it back on the solution branch, refusing if it moved since it was read.

    Its own PUT rather than `gh_contents.put_file`, for the one field that helper has no
    argument for: `branch`. Without it the Contents API writes to the DEFAULT branch, and
    the run would hand out under the config the form seeded while asserting against the
    one it meant to write - the two differing in exactly the settings under test.

    `sha` is the one the text was read at (`read_config`), so a commit that landed in
    between is refused rather than silently reverted - same contract as
    `schedule_edit.put_schedule`."""
    code, out = ghcli.gh(
        "api",
        "--method",
        "PUT",
        f"repos/{course_org}/{slug}/contents/{grades.GRADING_FILE}",
        "--field",
        "message=e2e: declare this assignment's shape",
        "--field",
        f"branch={course.SOLUTION_BRANCH}",
        "--field",
        f"sha={sha}",
        "--field",
        "content=@-",
        stdin=base64.b64encode(text.encode()).decode(),
    )
    if code != 0:
        raise RuntimeError(
            f"could not write {slug}/{grades.GRADING_FILE} on "
            f"{course.SOLUTION_BRANCH}: {out[:200]}"
        )
