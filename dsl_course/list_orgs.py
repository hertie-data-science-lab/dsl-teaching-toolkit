"""list-orgs -- discover DSL course and cohort orgs dynamically from GitHub.

Source of truth: every org's `.github` repo is tagged by `bootstrap_course.py` -
`dsl-course-hub` for a persistent COURSE org, `dsl-cohort` for a per-year COHORT
org. This tool searches for both topics across all repos the caller can see, reads
each org's `.github/dsl-course.yml`, and emits a JSON / Markdown / YAML inventory
of the two tiers separately.

Usage:
    python3 -m dsl_course.list_orgs                       # JSON to stdout
    python3 -m dsl_course.list_orgs --format markdown     # Markdown tables
    python3 -m dsl_course.list_orgs --format yaml         # YAML

The **Refresh Course Orgs Inventory** workflow runs the markdown form weekly and writes it
to its own job summary - the inventory is a report, never a committed page.
"""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from .central import MissingCentralRef, resolve_central_ref
from .course import COHORT_TOPIC, COURSE_CONFIG, COURSE_HUB_TOPIC
from .discovery import discover_cohorts, org_meta
from .ghcli import gh_json
from .log import log_err
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
    """Find every `.github` repo tagged `dsl-course-hub` and fetch its metadata.

    Returns a list of dicts with keys: org, readable, org_name, course_name, course_code,
    central_ref, url. Sorted by org name.

    An org whose metadata could not be read is carried through with `readable: False` and
    a null tier rather than dropped: the page must still show it (an absence reads as
    "deleted"), and a null matches no tier, so Promote's fan-out skips exactly that org
    and refreshes the rest.
    """
    orgs = []
    for owner in _tagged_orgs(COURSE_HUB_TOPIC):
        meta = _metadata_or_none(owner)
        if meta and meta.get("course"):
            # A cohort org's dsl-course.yml is a pointer back to its course org
            # (`course:`/`org:` keys only). Cohorts bootstrapped before the topic split
            # still carry dsl-course-hub on their .github, so filter them here too -
            # this inventory enumerates COURSE orgs, never their per-year cohorts.
            continue
        declared = meta or {}
        orgs.append(
            {
                "org": owner,
                "readable": meta is not None,
                "org_name": declared.get("org_name", owner),
                "course_name": declared.get("course_name", ""),
                "course_code": declared.get("course_code", ""),
                # The deployment tier this course (and every cohort under it) runs. Read
                # off the metadata already fetched, so the page costs no extra call to say
                # which orgs a promotion would move. A tier that does not resolve is null,
                # like an unreadable one: it matches no tier, so Promote's fan-out names
                # the org and skips it rather than refreshing it at a guessed ref.
                "central_ref": _tier_or_none(owner, declared)
                if meta is not None
                else None,
                "url": f"https://github.com/{owner}",
            }
        )

    orgs.sort(key=lambda o: o["org"].lower())
    return orgs


def discover_cohort_orgs() -> list[dict]:
    """Find every `.github` repo tagged `dsl-cohort` and read its course pointer.

    Returns a list of dicts with keys: org, readable, course, url - sorted by course org,
    then cohort, so the table groups each course's deliveries together. `readable: False`
    is "could not read it", distinct from a genuinely absent or null `course:` key; both
    end up under Orphaned, saying which.
    """
    cohorts = []
    for owner in _tagged_orgs(COHORT_TOPIC):
        meta = _metadata_or_none(owner)
        cohorts.append(
            {
                "org": owner,
                "readable": meta is not None,
                "course": (meta or {}).get("course") or "",
                "url": f"https://github.com/{owner}",
            }
        )
    cohorts.sort(key=lambda c: ((c["course"] or "").lower(), c["org"].lower()))
    return cohorts


def _metadata_or_none(org: str) -> dict | None:
    """`org`'s `.github/dsl-course.yml`, or None when it could not be READ.

    `discovery.org_meta` gives `{}` only for an org that genuinely carries none (a 404 or
    an empty file) and RAISES on anything else, which matters here more than anywhere: the
    tier split reads this file - `course:` present means a cohort - and the inventory is
    fully generated, so a transient failure read as "declares nothing" would file a cohort
    under Course orgs and rewrite the page around it.

    That abort is the right answer for the inventory itself (see `main`), but Promote's
    fan-out reads the same listing to decide which orgs to refresh, and one org's typo
    leaving the whole estate un-refreshed is not a trade worth making. So the failure is
    logged and localised to that org here, and the caller decides."""
    try:
        return org_meta(org)
    except RuntimeError as exc:
        log_err(f"{org}: could not read .github/{COURSE_CONFIG} - {exc}")
        return None


def unreadable(orgs: list[dict], cohorts: list[dict]) -> list[str]:
    """The orgs whose metadata this run could not read - see _metadata_or_none."""
    return sorted(o["org"] for o in [*orgs, *cohorts] if not o["readable"])


def render_tree(orgs: list[dict], cohorts: list[dict]) -> str:
    """The estate as it is actually shaped: each course org, with the cohort orgs that
    point at it nested underneath.

    Two flat tables kept the two tiers apart and made the reader join them by eye - which
    is the one question this page is ever opened to answer ("what is running under this
    course?"). Nesting answers it directly, and a course org with no cohorts becomes
    visible as such rather than being an absence from a second table.

    A cohort whose `course:` pointer names an org that is NOT a discovered course org is
    ORPHANED - the pointer is dangling, or its course org lost its `dsl-course-hub` topic.
    Those cannot nest anywhere, so they are listed at the end rather than dropped: an
    orphan is a fault to fix, and silently omitting it is how it stays unfixed.

    A cohort that exists but is NOT REGISTERED in its course's cohort-courses-pages.yml is
    marked as such. It is a live org that every nightly sync is blind to - membership,
    faculty, site, scheduler all fan out from that registry - so it fails by doing nothing
    at all, which is the one failure mode nothing else here reports. Marked, never
    auto-registered: absence from the registry can be deliberate (a cohort paused on
    purpose), so the page says what it sees and leaves the decision to a person."""
    by_course: dict[str, list[dict]] = {}
    for c in cohorts:
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
        registered = set(discover_cohorts(o["org"]))
        mine = by_course.pop(o["org"], [])
        lines += [
            f"    - [{c['org']}]({c['url']})"
            + ("" if c["org"] in registered else " - **not registered**")
            for c in mine
        ] or ["    - _no cohorts yet_"]

    # Whatever is left over points at no course org this run discovered.
    orphans = [c for rest in by_course.values() for c in rest]
    if orphans:
        lines.append("")
        lines.append("**Orphaned cohort orgs** _(no course org discovered for them)_:")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "yaml"],
        default="json",
        help="Output format when writing to stdout. Default: json.",
    )
    args = parser.parse_args()

    # Discovery is one `gh search repos` call per topic, and the markdown tree reads each
    # course's cohort registry; if either fails there is no inventory. Both are inside the
    # guard, so the Actions log gets a line rather than a traceback.
    try:
        orgs = discover_course_orgs()
        cohorts = discover_cohort_orgs()
        combined = {"course_orgs": orgs, "cohort_orgs": cohorts}
        rendered = (
            json.dumps(combined, indent=2)
            if args.format == "json"
            else yaml.safe_dump(combined, sort_keys=False)
            if args.format == "yaml"
            else render_tree(orgs, cohorts)
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1

    # An org this run could not read must not be reported as if the listing were
    # complete. The inventory still prints - Promote reads the JSON, and a null tier is a
    # value it can act on - but the exit code says the picture is partial.
    partial = unreadable(orgs, cohorts)
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
