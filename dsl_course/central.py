"""Where the central toolkit lives, and which ref of it an org runs.

Every seeded workflow checks this repo out and runs its engine code from it (see
workflows_render), and the generated READMEs link back to it (see profile_readme) - so
both sides must name the same repo/ref. One definition, imported by both.
"""

from __future__ import annotations

import re

from .log import log_err

CENTRAL = "hertie-data-science-lab/dsl-teaching-toolkit"

# The three deployment tiers, in promotion order. `main` is dev (CI only, nobody live),
# `staging` is the demo course org and its cohorts, `release` is every real org. Both tier
# branches are fast-forwards of `main` - see .github/workflows/promote.yml and
# docs-admin-arch/central-admin.md.
TIERS = ("main", "staging", "release")

# The DEFAULT ref a seeded workflow runs the engine from, for any org that does not say
# otherwise in its own `.github/dsl-course.yml` `central_ref:`.
#
# `release`, not `main`. Every seeded workflow in every org checks the engine out at run
# time, so whatever sits on this ref IS production, in every live course, from the moment
# it lands - a merge on a Tuesday afternoon reaches a release running that evening with no
# deploy step in between and no way to try it anywhere first. Promoting main to `release`
# deliberately puts a decision in that gap; rollback is a revert on `main` promoted
# forward, which every org picks up on its next run rather than needing a re-seed.
CENTRAL_REF = "release"

# What the renderers and the seeded templates leave where the ref goes, so a workflow is
# pinned to the ORG's ref at the point it is placed rather than to a module constant at
# the point it is rendered. workflows_render.for_placement and welcome do the substitution.
CENTRAL_REF_PLACEHOLDER = "__CENTRAL_REF__"

# A full commit SHA is the only non-tier ref accepted: it is the one form that cannot move
# under an org's feet, which is the whole point of pinning one. Abbreviations are refused
# because `actions/checkout` resolves them inconsistently.
_SHA = re.compile(r"[0-9a-f]{40}")


def resolve_central_ref(value: object, *, source: str) -> str:
    """The ref a declared `central_ref:` means, or the default when it declares nothing.

    Junk is refused LOUDLY and falls back to `CENTRAL_REF`: this value is rendered into
    the checkout step of every workflow in the org, so a typo would take the whole org's
    Actions tab down at the first run - hours or days after the edit, with nothing pointing
    at the cause. `source` names the file (or flag) the value came from, so the log line
    says where to go and fix it."""
    if value is None:
        return CENTRAL_REF
    ref = str(value).strip()
    if ref in TIERS or _SHA.fullmatch(ref):
        return ref
    log_err(
        f"{source}: central_ref '{ref}' is not one of {', '.join(TIERS)} or a full "
        f"40-character commit SHA - falling back to '{CENTRAL_REF}'"
    )
    return CENTRAL_REF
