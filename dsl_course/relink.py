"""Relink a student who switched to a different GitHub account.

A new account is a new immutable id, so nothing the toolkit keys on follows the student
by itself: their Join course is refused (the code is bound to the old id), and every repo,
grading-sheet row and team row still names the old login. The fix faculty make is the
natural one - re-point the row's `github_handle` in `students.csv` at the new account - and
the push-triggered Sync membership moves the rest, here, before the roster reconcile.

A row qualifies only when GitHub says, definitely, that the handle names a DIFFERENT
account from the stored `github_id`, and that stored id still exists under another login;
the new account is on no other row, the handle on no other row, and onboard never linked
this exact (handle, id) pair itself. That last one is the squat guard: onboard wrote the
pair, so the student renamed away and a stranger has since taken the login - a faculty
edit never produces it. Anything uncertain is left alone and said, and more than
`MAX_PER_PASS` qualifying rows in one pass reads as a bad paste, so none is acted on.

The stored `github_id` is the pending marker and is written LAST. Every step re-derives the
old login from it and is idempotent on (old, new), so a run that stops anywhere is finished
by the next one. Nothing is ever deleted: a new-name repo in the way is renamed aside and
archived when nobody but the toolkit ever committed to it, and anything a person wrote on
both sides holds the relink (a REFUSAL - logged, naming the handle, never a red run) until
faculty settle it. The classroom-config commit is a compare-and-set on the commit its
files were read at, so an edit that lands mid-relink fails the commit (the next run
retries) instead of being overwritten. Once the grants have moved, the old account leaves
the org - only a plain member in no faculty team and on no other row; staff are never
touched.

Not moved: a shared drop box's `<handle>/` folder (a bot commit there would read as a late
submission), `snapshots/` (frozen history) and the receipts issue's text.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from . import roster, teams
from .collect import AUTOGRADE_DIR
from .course import (
    COURSE_ADMIN_TEAM,
    GRADEBOOK_PREFIX,
    INSTRUCTORS_TEAM,
    submission_repo,
    submission_suffix,
)
from .discovery import classify_repos
from .enrol_codes import write_column
from .faults import Unusable
from .gh_contents import (
    dump_csv,
    get_blob,
    get_file_with_sha,
    head_commit,
    path_commit_subjects,
    put_file,
    put_files,
    read_csv,
    repo_blob_shas,
)
from .gh_teams import (
    get_team_members,
    id_of_login,
    login_of_id,
    org_member_role,
    remove_org_membership,
)
from .ghcli import BOT_EMAIL, bot_login, gh
from .grades import (
    CHANNEL_EMAIL,
    DISTRIBUTED_PATH,
    INFO_KEY,
    SHEETS_DIR,
    SheetUnreadable,
    dump_distributed,
    key_lines,
    parse_distributed,
    parse_sheet,
)
from .issues import close_by_creator
from .log import log_err, log_err_person, log_ok, log_person, log_step
from .repos import (
    add_collaborator,
    archive_repo,
    collaborator_permission,
    direct_collaborators,
    rename_repo,
    repo_missing,
)
from .sync_roster import revoke_repo_grants

MAX_PER_PASS = 3
RELINKS_DIR = "enrolment/relinks"
ASIDE_PREFIX = "relink-aside-"
WELCOME_REPO = "welcome"
THROTTLE_LABEL = "needs-review"
# Onboard's own commit message for a binding (templates/welcome/onboard.yml). The relink
# writes the same prefix, so a pair the toolkit linked is always recognisable as one.
LINK_MESSAGE = "roster: link @{handle} (id {user_id})"
_CLOSE_COMMENT = (
    "The teaching team has linked this account to your enrolment, so this request is no "
    "longer needed. Accept the organisation invitation in your email if you have not "
    "already; there is nothing else to do here."
)


class Refused(Exception):
    """A relink that needs a person: both accounts hold something a human wrote. The
    message is public-log safe - it never names a repo or the old login."""


@dataclass(frozen=True)
class Pending:
    """One roster row whose handle now names a different account from its stored id."""

    index: int  # data-row index, as `roster.parse` orders them
    email: str
    new: str
    new_id: str
    old: str
    old_id: str
    old_on_roster: bool = False  # the old login is still another row's handle

    @property
    def line(self) -> int:
        return self.index + 2


def _skip(index: int, why: str) -> None:
    """A row this pass leaves alone, by LINE only: the reason is what faculty act on."""
    log_err(f"  students.csv line {index + 2}: {why} - not relinked")


def pending(org: str, students: list[roster.Student]) -> list[Pending]:
    """The rows that qualify for a relink this pass (see the module docstring).

    A lookup GitHub did not answer is COUNTED, not logged per row: an outage would
    otherwise print a line per student. The count is one public line; which rows it was
    goes through `log_person`."""
    handles = Counter(s.github_handle.casefold() for s in students if s.onboarded)
    found: list[Pending] = []
    unanswered = 0
    subjects: tuple[str, ...] | None = None
    for i, s in enumerate(students):
        stored = s.github_id.strip()
        if not s.onboarded or not stored.isdigit():
            continue
        new_id = id_of_login(s.github_handle)
        if not new_id:
            unanswered += 1
            log_person(f"    no definite answer for @{s.github_handle} (line {i + 2})")
            continue
        if new_id == stored:
            continue
        if handles[s.github_handle.casefold()] > 1:
            _skip(i, "this handle is on another row too")
            continue
        if any(o.github_id.strip() == new_id for o in students if o is not s):
            _skip(i, "the account this handle names is linked on another row")
            continue
        old = login_of_id(stored)
        if old == "":
            log_err_person(
                f"  students.csv line {i + 2}: the GitHub account this row was first "
                f"linked to has been deleted - not relinked",
                f"    the row now names @{s.github_handle}; its stored id {stored} is gone",
            )
            continue
        if old is None or old.casefold() == s.github_handle.casefold():
            unanswered += 1
            log_person(f"    no definite answer for id {stored} (line {i + 2})")
            continue
        if subjects is None:
            try:
                subjects = path_commit_subjects(
                    org, roster.CONFIG_REPO, roster.ROSTER_PATH
                )
            except RuntimeError as exc:
                log_err(
                    f"could not read {roster.ROSTER_PATH}'s history ({exc}) - no "
                    f"relink this run"
                )
                return []
        linked = LINK_MESSAGE.format(handle=s.github_handle, user_id=stored).casefold()
        if any(subject.casefold().startswith(linked) for subject in subjects):
            _skip(
                i,
                "the student renamed their account and someone else now holds the old "
                "name - put their CURRENT login in the row",
            )
            continue
        old_on_roster = handles[old.casefold()] > 0
        found.append(
            Pending(
                i, s.hertie_email, s.github_handle, new_id, old, stored, old_on_roster
            )
        )
    if unanswered:
        log_err(
            f"{unanswered} roster row(s) in {org} could not be checked for a switched "
            f"GitHub account (GitHub gave no definite answer) - the next sync checks again"
        )
    if len(found) > MAX_PER_PASS:
        log_err(
            f"{len(found)} roster rows point at a different GitHub account from the one "
            f"they were linked to - more than {MAX_PER_PASS} in one edit reads as a bad "
            f"paste, so none is relinked. Check the github_handle column."
        )
        return []
    return found


# ------------------------------------------------------------------------------ repos


def _named(existing: dict[str, dict], name: str) -> str | None:
    """The listing's own spelling of `name`, which GitHub matches case-insensitively."""
    fold = name.casefold()
    return next((n for n in existing if n.casefold() == fold), None)


def _team_repos(org: str) -> set[str]:
    return {
        submission_repo(key, team).casefold()
        for key, per_team in teams.load(org).items()
        for team in per_team
    }


def _repos_of(existing: dict[str, dict], login: str, team_repos: set[str]) -> list[str]:
    """Every live repo named after `login`: its individual submission repos and gradebook."""
    fold = login.casefold()
    names = [
        name
        for name, template in sorted(classify_repos(list(existing.values())).items())
        if template
        and submission_suffix(name, template).casefold() == fold
        and name.casefold() not in team_repos
    ]
    gradebook = _named(existing, f"{GRADEBOOK_PREFIX}{login}")
    if gradebook:
        names.append(gradebook)
    return [n for n in names if not existing[n].get("archived")]


def _renamed(name: str, old: str, new: str) -> str:
    return name[: len(name) - len(old)] + new


def _only_bot_commits(org: str, repo: str) -> bool | None:
    """Whether nobody but the toolkit ever committed to `repo` - a gradebook's starter
    README, a handout from the template. None when that could not be told."""
    me = bot_login()
    if not me:
        return None
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/commits?per_page=100",
        "--jq",
        '.[] | [(.author.login // ""), (.commit.author.email // "")] | @tsv',
    )
    if code != 0:
        return True if "HTTP 409" in out else None  # 409: no commits at all
    for row in out.splitlines():
        login, _, email = row.partition("\t")
        if login.strip() != me and email.strip() != BOT_EMAIL:
            return False
    return True


def _check_repos(
    org: str, p: Pending, existing: dict[str, dict], team_repos: set[str]
) -> None:
    for name in _repos_of(existing, p.old, team_repos):
        target = _named(existing, _renamed(name, p.old, p.new))
        if target is None:
            continue
        untouched = _only_bot_commits(org, target)
        if untouched is None:
            raise Refused(
                "could not tell whether a repo of the new account's is in use"
            )
        if not untouched:
            raise Refused("the new account already has a repo with work in it")


def _move(existing: dict[str, dict], name: str, to: str, **fields) -> None:
    row = existing.pop(name)
    existing[to] = {
        **row,
        "name": to,
        "url": row.get("url", "").rsplit("/", 1)[0] + f"/{to}",
        **fields,
    }


def _aside_name(org: str, existing: dict[str, dict]) -> str | None:
    for n in range(1, 100):
        name = f"{ASIDE_PREFIX}{n}"
        if _named(existing, name) is None and repo_missing(org, name):
            return name
    return None


def _move_repos(
    org: str, p: Pending, existing: dict[str, dict], team_repos: set[str]
) -> bool:
    """Step 1: rename every repo named after the old login to the new one, setting an
    untouched new-name repo aside first. `existing` is updated in place, so the prune and
    the gradebooks that follow in this pass see the new names."""
    for name in _repos_of(existing, p.old, team_repos):
        to = _renamed(name, p.old, p.new)
        target = _named(existing, to)
        if target is not None:
            aside = _aside_name(org, existing)
            if aside is None or not rename_repo(org, target, aside, person=True):
                return False
            _move(existing, target, aside, archived=True)
            if not archive_repo(org, aside, person=True):
                return False
            log_person(f"  [ok] set {org}/{target} aside as {aside} (archived)")
        gradebook = name.casefold().startswith(GRADEBOOK_PREFIX)
        description = f"Private gradebook for @{p.new}" if gradebook else None
        if not rename_repo(org, name, to, description=description, person=True):
            return False
        _move(existing, name, to)
        log_person(f"  [ok] renamed {org}/{name} -> {to}")
    return True


def _move_grants(
    org: str, p: Pending, existing: dict[str, dict], team_repos: set[str]
) -> bool:
    """Step 2: the new login gets the old one's direct grant on every repo now named after
    it, and the old login's grant and invitations go. The drop box's push grant is the
    handout's, and the next one re-grants it."""
    for repo in _repos_of(existing, p.new, team_repos):
        have = direct_collaborators(org, repo, person=True)
        if have is None:
            return False
        if p.new.casefold() not in have:
            permission = collaborator_permission(org, repo, p.old, person=True)
            if permission is None:
                return False
            if not permission:
                gradebook = repo.casefold().startswith(GRADEBOOK_PREFIX)
                permission = "pull" if gradebook else "maintain"
            if not add_collaborator(
                org, repo, p.new, permission=permission, person=True
            ):
                return False
        _withdrawn, failed = revoke_repo_grants(org, repo, p.old)
        if failed:
            return False
    return True


# ---------------------------------------------------------------------- config files


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_blank(v) for k, v in value.items() if k != INFO_KEY)
    if isinstance(value, list):
        return all(_blank(v) for v in value)
    return False


_KEY_LINE = re.compile(r"^(\s*)(['\"]?)(.+?)\2:(\s|$)")


def _rekey_sheet(text: str, old: str, new: str) -> str | None:
    """A grading sheet with the old login's entries re-keyed to the new one, or None when
    it has none. A TEXT edit of the key lines, so the header (and its FROZEN status), the
    grader's comments and their formatting all survive; the result is re-parsed and must
    hold exactly the old entries under the new key.

    A blank entry already under the new key (the sheet refresh adds one for the new
    handle) is dropped; one with anything typed in it refuses."""
    sheet = parse_sheet(text)
    parents: list[tuple[tuple[str, ...], dict]] = []
    if isinstance(sheet.get("submissions"), dict):
        parents.append((("submissions",), sheet["submissions"]))
    if isinstance(sheet.get("teams"), dict):
        for team, block in sheet["teams"].items():
            if isinstance(block, dict) and isinstance(block.get("members"), dict):
                parents.append((("teams", str(team), "members"), block["members"]))
    renames: list[tuple[tuple[str, ...], object]] = []
    drops: list[tuple[str, ...]] = []
    for path, mapping in parents:
        old_key = next(
            (k for k in mapping if str(k).casefold() == old.casefold()), None
        )
        if old_key is None:
            continue
        new_key = next(
            (k for k in mapping if str(k).casefold() == new.casefold()), None
        )
        if new_key is not None:
            if not _blank(mapping[new_key]):
                raise Refused("both accounts have marks on a grading sheet")
            drops.append(path + (str(new_key),))
        renames.append((path + (str(old_key),), mapping[old_key]))
    if not renames:
        return None
    where = {
        tuple(k.casefold() for k in path): n for path, n in key_lines(text).items()
    }
    lines = text.splitlines(keepends=True)

    def line_of(path: tuple[str, ...]) -> int:
        n = where.get(tuple(k.casefold() for k in path))
        if n is None:
            raise Refused("a grading sheet is laid out in a way the relink cannot edit")
        return n - 1

    for path, _value in renames:
        at = line_of(path)
        m = _KEY_LINE.match(lines[at])
        if not m or m.group(3).casefold() != old.casefold():
            raise Refused("a grading sheet is laid out in a way the relink cannot edit")
        lines[at] = lines[at][: m.start(3)] + new + lines[at][m.end(3) :]
    for at in sorted((line_of(path) for path in drops), reverse=True):
        indent = len(lines[at]) - len(lines[at].lstrip())
        end = at + 1
        while end < len(lines) and (
            not lines[end].strip()
            or len(lines[end]) - len(lines[end].lstrip()) > indent
        ):
            end += 1
        del lines[at:end]
    out = "".join(lines)
    check = parse_sheet(out)
    for path, value in renames:
        node: object = check
        for key in path[:-1] + (new,):
            node = node.get(key) if isinstance(node, dict) else None
        if node != value:
            raise Refused("a grading sheet could not be re-keyed safely")
    return out


def _rekey_teams(text: str, old: str, new: str) -> str | None:
    """teams.csv with the old login's rows re-pointed. A row identical to one the new login
    already has is dropped; the new login in a DIFFERENT team for the same assignment
    refuses."""
    reader = read_csv(text, teams.FIELDS, teams.TEAMS_PATH)
    fields = list(reader.fieldnames or [])
    rows = list(reader)

    def cell(row: dict, name: str) -> str:
        return (row.get(name) or "").strip().casefold()

    new_teams = {
        cell(r, "assignment"): cell(r, "team")
        for r in rows
        if cell(r, "github_handle") == new.casefold()
    }
    kept = []
    changed = False
    for row in rows:
        if cell(row, "github_handle") != old.casefold():
            kept.append(row)
            continue
        changed = True
        assignment = cell(row, "assignment")
        if assignment in new_teams:
            if new_teams[assignment] != cell(row, "team"):
                raise Refused(
                    "the two accounts are in different teams for one assignment"
                )
            continue
        row["github_handle"] = new
        kept.append(row)
    if not changed:
        return None
    return dump_csv(fields, ([row.get(f) or "" for f in fields] for row in kept))


def _rekey_distributed(text: str, old: str, new: str) -> str | None:
    """distributed.csv with the old login's EMAIL rows re-pointed, so nobody is mailed
    twice. The gradebook rows stay, so the next distribute rewrites the renamed gradebook
    for the new login."""
    records = parse_distributed(text)
    moved = [
        k
        for k in records
        if k[2] == CHANNEL_EMAIL and k[0].casefold() == old.casefold()
    ]
    if not moved:
        return None
    for key in moved:
        value = records.pop(key)
        records.setdefault((new, key[1], key[2]), value)
    return dump_distributed(records)


def _config_changes(
    org: str, p: Pending
) -> tuple[dict[str, bytes], list[str], tuple[str, str] | None]:
    """Everything in classroom-config that names the old login, rewritten for the new one:
    `(files to write, paths to remove, the commit they were read at)`. Raises `Refused`
    for a collision. The commit goes to `put_files` as its `base`, so a write that landed
    after this read makes the commit fail rather than be overwritten."""
    config = roster.CONFIG_REPO
    base = head_commit(org, config)
    if base is None:
        return {}, [], None
    live = repo_blob_shas(org, config, base[1])

    def read(path: str) -> bytes:
        content = get_blob(org, config, live[path])
        if content is None:
            raise RuntimeError("a classroom-config file changed while it was read")
        return content

    files: dict[str, bytes] = {}
    delete: list[str] = []
    rewrites: list[tuple[str, Callable[[str, str, str], str | None]]] = [
        (teams.TEAMS_PATH, _rekey_teams)
    ]
    rewrites += [
        (path, _rekey_sheet)
        for path in sorted(live)
        if path.startswith(f"{SHEETS_DIR}/")
        and path.endswith(".yml")
        and path.count("/") == 1
    ]
    rewrites.append((DISTRIBUTED_PATH, _rekey_distributed))
    for path, rewrite in rewrites:
        if path not in live:
            continue
        try:
            text = rewrite(read(path).decode(), p.old, p.new)
        except SheetUnreadable as exc:
            raise Refused("a grading sheet does not parse right now") from exc
        if text is not None:
            files[path] = text.encode()
    for path in sorted(live):
        parts = path.split("/")
        if len(parts) != 3 or parts[0] != AUTOGRADE_DIR:
            continue
        stem, dot, ext = parts[2].partition(".")
        if stem.casefold() != p.old.casefold() or not dot:
            continue
        to = f"{parts[0]}/{parts[1]}/{p.new}.{ext}"
        if to in live:
            raise Refused("both accounts have autograde results for one assignment")
        files[to] = read(path)
        delete.append(path)
    return files, delete, base


# --------------------------------------------------------------------------- one row


def _remove_old_member(org: str, p: Pending) -> bool:
    """Take the old account out of the org (or cancel its invitation) - only a plain
    `member`, in no faculty team and named on no other roster row. The faculty-access
    floor: staff are never demoted, so anything else is left and said privately. False
    only when the removal itself failed."""
    if p.old_on_roster:
        log_person(f"  [skip] @{p.old} is still on the roster - left in {org}")
        return True
    role = org_member_role(org, p.old)
    if role == "":
        return True
    if role != "member":
        log_person(f"  [skip] @{p.old} is {role or 'unreadable'} in {org} - left")
        return True
    for team in (INSTRUCTORS_TEAM, COURSE_ADMIN_TEAM):
        members = get_team_members(org, team)
        if members is None or p.old.casefold() in {m.casefold() for m in members}:
            log_person(f"  [skip] @{p.old} may be faculty ({team}) in {org} - left")
            return True
    if not remove_org_membership(org, p.old):
        log_err("could not remove a relinked student's old account from the org")
        return False
    log_person(f"  [ok] removed @{p.old} from {org}")
    return True


def _record(org: str, p: Pending, repos: list[str]) -> bool:
    body = {
        "old_login": p.old,
        "old_id": p.old_id,
        "new_login": p.new,
        "new_id": p.new_id,
        "relinked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repos": repos,
        "not_moved": "shared drop-box folders, snapshots/, receipts issue text",
    }
    return put_file(
        org,
        roster.CONFIG_REPO,
        f"{RELINKS_DIR}/{p.old_id}.json",
        (json.dumps(body, indent=2) + "\n").encode(),
        f"enrolment: record the relink of @{p.new}",
        person=True,
    )


def _write_id(org: str, p: Pending) -> bool:
    got = get_file_with_sha(org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if got is None:
        return False
    raw, sha = got
    written = write_column(
        org,
        raw,
        sha,
        "github_id",
        [(p.index, p.email, p.new_id)],
        LINK_MESSAGE.format(handle=p.new, user_id=p.new_id)
        + " after an account switch",
        replacing=p.old_id,
    )
    roster._roster_text.cache_clear()
    teams._teams_text.cache_clear()
    return written is not None


def relink_row(
    org: str, p: Pending, existing: dict[str, dict], dry_run: bool = False
) -> bool:
    """Move one student to their new account. False when a step's write failed (the id is
    untouched, so the next run resumes); raises `Refused` for a collision - checked before
    anything moves, so a refused relink moves nothing."""
    team_repos = _team_repos(org)
    _check_repos(org, p, existing, team_repos)
    _config_changes(org, p)
    if dry_run:
        log_person(f"    DRY-RUN relink @{p.old} -> @{p.new} in {org}")
        return True
    if not _move_repos(org, p, existing, team_repos):
        return False
    if not _move_grants(org, p, existing, team_repos):
        return False
    files, delete, base = _config_changes(org, p)
    if (files or delete) and not put_files(
        org,
        roster.CONFIG_REPO,
        files,
        f"relink: move @{p.old}'s rows to @{p.new}",
        delete=delete,
        person=True,
        base=base,
    ):
        return False
    closed, failed = close_by_creator(
        f"{org}/{WELCOME_REPO}", p.new, THROTTLE_LABEL, _CLOSE_COMMENT
    )
    if failed:
        return False
    log_person(f"  [ok] closed {closed} unresolved Join issue(s) from @{p.new}")
    if not _remove_old_member(org, p):
        return False
    if not _record(org, p, _repos_of(existing, p.new, team_repos)):
        return False
    return _write_id(org, p)


def sync(org: str, existing: dict[str, dict] | None, dry_run: bool = False) -> int:
    """Relink every row that qualifies in `org`. Returns the error count: a write that
    failed counts, a refusal and an uncertain row do not."""
    try:
        got = get_file_with_sha(org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    except RuntimeError as exc:
        log_err(f"could not read {org}'s roster for the relink check: {exc}")
        return 1
    if got is None:
        return 0  # no roster: sync_roster reports it
    found = pending(org, roster.parse(got[0]))
    if not found:
        return 0
    if existing is None:
        log_err(f"{len(found)} relink(s) in {org} wait for a repo listing - next run")
        return 0
    log_step(f"Relinking {len(found)} student(s) who switched GitHub account in {org}")
    done = errors = 0
    for p in found:
        log_person(f"  relink @{p.old} (id {p.old_id}) -> @{p.new} (id {p.new_id})")
        try:
            if relink_row(org, p, existing, dry_run=dry_run):
                done += 1
            else:
                log_err(
                    f"  students.csv line {p.line}: relink stopped part-way - the next "
                    f"run resumes it"
                )
                errors += 1
        except Refused as exc:
            log_err(
                f"relink of @{p.new} (students.csv line {p.line}) is held: {exc}. Settle "
                f"that and the next Sync membership finishes the move."
            )
        except Unusable:
            raise
        except RuntimeError as exc:
            log_err(
                f"  students.csv line {p.line}: relink could not read what it needs "
                f"({exc}) - the next run tries again"
            )
            errors += 1
    if done:
        suffix = " (dry run)" if dry_run else ""
        log_ok(f"relinked {done} student(s) to their new GitHub account{suffix}")
    return errors
