"""list-orgs -- the DSL course and semester orgs, as an inventory.

COURSE orgs are the ones the toolkit's `orgs.yml` names (`org_registry`), each read off its
`.github/dsl-course.yml`: the tier fan-out reads this list, and a topic is anyone's to set.
SEMESTER orgs are still found by their `dsl-semester` topic, best effort, for the report
only. The output is a JSON / Markdown / YAML inventory of the two tiers separately.

Usage:
    python3 -m dsl_course.list_orgs                       # JSON to stdout
    python3 -m dsl_course.list_orgs --format markdown     # Markdown tables
    python3 -m dsl_course.list_orgs --format yaml         # YAML

The **Refresh Course Orgs Inventory** workflow runs the markdown form weekly and writes it
to its own job summary - the inventory is a report, never a committed page.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from . import invitations, issues, org_registry
from .central import CENTRAL, MissingCentralRef, resolve_central_ref
from .course import (
    COURSE_CONFIG,
    COURSE_HUB_TOPIC,
    OLD_SEMESTER_TOPIC,
    SEMESTER_TOPIC,
)
from .discovery import discover_semesters, org_meta, semester_pointer
from .faults import Unusable, not_migrated_text
from .ghcli import gh_json
from .log import CLIParser, log_err
from .repos import org_exists

# How many results one `gh search repos` page returns. Reading only the first page would
# silently drop every org past it from the inventory, which is indistinguishable from those
# orgs not existing - _tagged_orgs raises instead when the result set fills the limit.
# Raise this (gh allows up to 1000) when the estate outgrows it.
SEARCH_LIMIT = 100


def _tagged_orgs(topic: str) -> list[str]:
    """Owner logins of every LIVE `.github` repo carrying `topic`.

    Raises when the search comes back exactly full: that is indistinguishable from a
    truncated result set, and an inventory silently missing its tail is worse than none.

    Every hit is then confirmed to still exist (`org_exists`) - the search index lags
    org deletion by days, so the topic alone is not evidence the org is there."""
    results = gh_json(
        "search",
        "repos",
        f"topic:{topic}",
        "--limit",
        str(SEARCH_LIMIT),
        "--json",
        "name,owner",
    )
    if len(results) >= SEARCH_LIMIT:
        raise RuntimeError(
            f"`gh search repos topic:{topic}` returned the full {SEARCH_LIMIT}-result "
            f"page, so the result set is truncated and any org past it would be dropped "
            f"from the inventory. Raise SEARCH_LIMIT in dsl_course/list_orgs.py."
        )

    owners = []
    for repo in results:
        if repo.get("name") != ".github":
            continue
        owner = (repo.get("owner") or {}).get("login", "")
        if owner and org_exists(owner):
            owners.append(owner)
    return owners


def _tier_or_none(org: str, declared: dict) -> str | None:
    """The tier `org` declares, or None when it declares something that is not one."""
    try:
        return resolve_central_ref(
            declared.get("central_ref"), source=f"{org}/.github/{COURSE_CONFIG}"
        )
    except MissingCentralRef as exc:
        log_err(str(exc))
        return None


def discover_course_orgs() -> list[dict]:
    """Every course org `orgs.yml` names, with its `.github/dsl-course.yml` metadata.

    Returns a list of dicts with keys: org, readable, course_name, course_code,
    central_ref, url. Sorted by org name.

    An org whose metadata could not be read is carried through with `readable: False` and
    a null tier rather than dropped: the page must still show it (an absence reads as
    "deleted"), and a null matches no tier, so the deploy fan-out skips exactly that org
    and refreshes the rest.
    """
    orgs = []
    # The registry, never a topic search: the topic is anyone's to set, and the fan-out
    # this feeds writes the bot token into every org it refreshes.
    for owner in org_registry.names():
        meta = _metadata_or_none(owner)
        if meta and meta.get("course"):
            # A semester org's dsl-course.yml is a pointer back to its course org
            # (`course:`/`org:` keys only). Semesters bootstrapped before the topic split
            # still carry dsl-course-hub on their .github, so filter them here too -
            # this inventory enumerates COURSE orgs, never their per-year semesters.
            continue
        declared = meta or {}
        orgs.append(
            {
                "org": owner,
                "readable": meta is not None,
                "course_name": declared.get("course_name", ""),
                "course_code": declared.get("course_code", ""),
                # The deployment tier this course (and every semester under it) runs. Read
                # off the metadata already fetched, so the page costs no extra call to say
                # which orgs a promotion would move. A tier that does not resolve is null,
                # like an unreadable one: it matches no tier, so the deploy fan-out names
                # the org and skips it rather than refreshing it at a guessed ref.
                "central_ref": _tier_or_none(owner, declared)
                if meta is not None
                else None,
                "url": f"https://github.com/{owner}",
            }
        )

    orgs.sort(key=lambda o: o["org"].lower())
    return orgs


def discover_semester_orgs() -> list[dict]:
    """Find every `.github` repo tagged `dsl-semester` and read its course pointer.

    Returns a list of dicts with keys: org, readable, course, url - sorted by course org,
    then semester, so the table groups each course's deliveries together. `readable: False`
    is "could not read it", distinct from a genuinely absent or null `course:` key; both
    end up under Orphaned, saying which.
    """
    semesters = []
    tagged = _tagged_orgs(SEMESTER_TOPIC)
    # An org still carrying the OLD topic (decision 0012) is listed as unreadable - so the
    # run's exit code says the picture is partial - and named as NOT_MIGRATED.
    for owner in sorted(set(_tagged_orgs(OLD_SEMESTER_TOPIC)) - set(tagged)):
        log_err(f"{owner}: {not_migrated_text(OLD_SEMESTER_TOPIC, SEMESTER_TOPIC)}")
        semesters.append(
            {
                "org": owner,
                "readable": False,
                "course": "",
                "url": f"https://github.com/{owner}",
            }
        )
    for owner in tagged:
        meta = _pointer_or_none(owner)
        semesters.append(
            {
                "org": owner,
                "readable": meta is not None,
                "course": (meta or {}).get("course") or "",
                "url": f"https://github.com/{owner}",
            }
        )
    semesters.sort(key=lambda c: ((c["course"] or "").lower(), c["org"].lower()))
    return semesters


def _metadata_or_none(org: str) -> dict | None:
    """`org`'s `.github/dsl-course.yml`, or None when it could not be READ.

    `discovery.org_meta` gives `{}` only for an org that genuinely carries none (a 404 or
    an empty file) and RAISES on anything else, which matters here more than anywhere: the
    tier split reads this file - `course:` present means a semester - and the inventory is
    fully generated, so a transient failure read as "declares nothing" would file a semester
    under Course orgs and rewrite the page around it.

    That abort is the right answer for the inventory itself (see `main`), but the deploy
    fan-out reads the same listing to decide which orgs to refresh, and one org's typo
    leaving the whole estate un-refreshed is not a trade worth making. So the failure is
    logged and localised to that org here, and the caller decides."""
    try:
        return org_meta(org)
    except RuntimeError as exc:
        log_err(f"{org}: could not read .github/{COURSE_CONFIG} - {exc}")
        return None


def _pointer_or_none(semester_org: str) -> dict | None:
    """A semester's course pointer, or None when it could not be READ (see
    `_metadata_or_none`: the same rule, for the file a semester keeps in its config repo)."""
    try:
        return semester_pointer(semester_org)
    except RuntimeError as exc:
        log_err(f"{semester_org}: could not read its course pointer - {exc}")
        return None


# Picked once: `issues` finds the issue by this exact title, so a rename opens a second one.
AWAITING_TITLE = "Course orgs awaiting registration"


def awaiting_registration() -> list[dict]:
    """Every org that looks like a course the registry does not name, with why: it has
    invited the bot (a new course, before its set-up) or its `.github` carries the course
    topic. A tagged org whose metadata points at a course is a semester, not a course, and
    a semester a registered course's `semesters.yml` lists is never awaiting anyone."""
    registered = org_registry.course_orgs()
    # A semester a registered course lists is vouched for: the course's own scheduler pass
    # accepts its invitation, so it waits on nobody here.
    known = set(registered)
    for course in org_registry.names():
        try:
            known |= {s.casefold() for s in discover_semesters(course)}
        except Unusable as exc:
            # One course's broken registry vouches for nothing, and stops nothing else.
            log_err(f"{course}: {exc}")
    found: dict[str, dict] = {}
    for org in invitations.pending_orgs():
        if org.casefold() not in known:
            found.setdefault(org.casefold(), {"org": org, "why": "invited the bot"})
    try:
        tagged = _tagged_orgs(COURSE_HUB_TOPIC)
    except RuntimeError as exc:
        # The report's second source is best effort: the invitations above still file.
        log_err(str(exc))
        tagged = []
    for org in tagged:
        if org.casefold() in known or org.casefold() in found:
            continue
        meta = _metadata_or_none(org)
        if not (meta or {}).get("course"):
            found[org.casefold()] = {"org": org, "why": f"tagged {COURSE_HUB_TOPIC}"}
    return sorted(found.values(), key=lambda o: o["org"].lower())


def awaiting_body(orgs: list[dict]) -> str:
    rows = "\n".join(
        f"- [{o['org']}](https://github.com/{o['org']}) - {o['why']}" for o in orgs
    )
    return (
        "These orgs look like new courses but are not in `orgs.yml`, so the bot does not "
        "join them and they are never refreshed:\n\n"
        f"{rows}\n\n"
        "Check who asked for each one, then add it to `orgs.yml` by pull request. The bot "
        "accepts a waiting invitation on the next automatic run. This issue is rewritten "
        "daily by *Bot Token Canary* and closes itself when the list is empty."
    )


def file_awaiting(orgs: list[dict]) -> int:
    """Keep the one issue in the toolkit repo in step with `orgs`; the error count."""
    if not orgs:
        return issues.close_issues_titled(
            CENTRAL, AWAITING_TITLE, "Every course org is registered."
        )
    return issues.upsert_issue(CENTRAL, AWAITING_TITLE, awaiting_body(orgs)).errors


def unreadable(orgs: list[dict], semesters: list[dict]) -> list[str]:
    """The orgs whose metadata this run could not read - see _metadata_or_none."""
    return sorted(o["org"] for o in [*orgs, *semesters] if not o["readable"])


def render_tree(orgs: list[dict], semesters: list[dict]) -> str:
    """The estate as it is actually shaped: each course org, with the semester orgs that
    point at it nested underneath.

    Two flat tables kept the two tiers apart and made the reader join them by eye - which
    is the one question this page is ever opened to answer ("what is running under this
    course?"). Nesting answers it directly, and a course org with no semesters becomes
    visible as such rather than being an absence from a second table.

    A semester whose `course:` pointer names an org that is NOT a discovered course org is
    ORPHANED - the pointer is dangling, or its course org lost its `dsl-course-hub` topic.
    Those cannot nest anywhere, so they are listed at the end rather than dropped: an
    orphan is a fault to fix, and silently omitting it is how it stays unfixed.

    A semester that exists but is NOT REGISTERED in its course's semesters.yml is
    marked as such. It is a live org that every nightly sync is blind to - membership,
    faculty, site, scheduler all fan out from that registry - so it fails by doing nothing
    at all, which is the one failure mode nothing else here reports. Marked, never
    auto-registered: absence from the registry can be deliberate (a semester paused on
    purpose), so the page says what it sees and leaves the decision to a person."""
    by_course: dict[str, list[dict]] = {}
    for c in semesters:
        by_course.setdefault(c["course"], []).append(c)

    lines = []
    for o in orgs:
        name = " - ".join(x for x in (o["course_name"], o["course_code"]) if x)
        lines.append(
            f"- **[{o['org']}]({o['url']})**"
            + (f" - {name}" if name else "")
            + (
                f" - **{COURSE_CONFIG} unreadable**"
                if not o["readable"]
                else f" - toolkit `{o['central_ref']}`"
                if o["central_ref"]
                else " - **`central_ref:` is not a tier**"
            )
        )
        # Straight through discovery, so there is ONE parser for that file: it raises on
        # a malformed registry, and this page would rather fail than render a course as
        # running nothing because its registry could not be read.
        registered = set(discover_semesters(o["org"]))
        mine = by_course.pop(o["org"], [])
        lines += [
            f"    - [{c['org']}]({c['url']})"
            + ("" if c["org"] in registered else " - **not registered**")
            for c in mine
        ] or ["    - _no semesters yet_"]

    # Whatever is left over points at no course org this run discovered.
    orphans = [c for rest in by_course.values() for c in rest]
    if orphans:
        lines.append("")
        lines.append(
            "**Orphaned semester orgs** _(no course org discovered for them)_:"
        )
        lines += [
            f"- [{c['org']}]({c['url']}) -> "
            + (
                f"**{COURSE_CONFIG} unreadable**"
                if not c["readable"]
                else f"`{c['course'] or 'no course: pointer'}`"
            )
            for c in sorted(orphans, key=lambda c: c["org"].lower())
        ]
    return "\n".join(lines)


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "yaml"],
        default="json",
        help="Output format when writing to stdout. Default: json.",
    )
    parser.add_argument(
        "--awaiting-registration",
        action="store_true",
        help="Print, as JSON, the orgs that look like courses but are not in orgs.yml "
        "(needs the bot's token: it reads the bot's pending invitations).",
    )
    parser.add_argument(
        "--file-awaiting",
        metavar="JSON",
        help="Open, update or close the toolkit repo's 'Course orgs awaiting "
        "registration' issue from a file --awaiting-registration wrote.",
    )
    args = parser.parse_args()

    if args.file_awaiting:
        return file_awaiting(json.loads(Path(args.file_awaiting).read_text()))
    if args.awaiting_registration:
        try:
            print(json.dumps(awaiting_registration()))
        except RuntimeError as exc:
            log_err(str(exc))
            return 1
        return 0

    # Discovery is one `gh search repos` call per topic, and the markdown tree reads each
    # course's semester registry; if either fails there is no inventory. Both are inside the
    # guard, so the Actions log gets a line rather than a traceback.
    try:
        orgs = discover_course_orgs()
        # The semester listing is the one part of the inventory a topic search still
        # finds (the report shows semesters no registry lists), and the fan-out never reads
        # it: a search that fails is a line, never an empty fan-out.
        try:
            semesters = discover_semester_orgs()
        except RuntimeError as exc:
            log_err(f"semester orgs not listed: {exc}")
            semesters = []
        combined = {"course_orgs": orgs, "semester_orgs": semesters}
        rendered = (
            json.dumps(combined, indent=2)
            if args.format == "json"
            else yaml.safe_dump(combined, sort_keys=False)
            if args.format == "yaml"
            else render_tree(orgs, semesters)
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1

    # An org this run could not read must not be reported as if the listing were
    # complete. The inventory still prints - the fan-out reads the JSON, and a null tier is a
    # value it can act on - but the exit code says the picture is partial.
    partial = unreadable(orgs, semesters)
    if partial:
        log_err(
            "could not read the metadata of: "
            + ", ".join(partial)
            + " - the inventory below is incomplete"
        )

    print(rendered)
    return 1 if partial else 0


if __name__ == "__main__":
    sys.exit(main())
