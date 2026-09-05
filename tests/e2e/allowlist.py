"""Which orgs this harness may touch - the innermost of the three fences.

Outermost is `ghcli`'s `DSL_ORG_ALLOWLIST`, which refuses a write at the transport and so
catches code that never asked this module anything. This one is the harness's own copy of
the same fact, stated as a literal rather than read from the environment, so that a
mistyped env var cannot WIDEN the run: `DSL_E2E_ORGS` may only ever narrow.

Both demo orgs are named here because the pipeline needs both - the assignment template is
created in the course org and the submission repos land in the cohort org.
"""

from __future__ import annotations

import os

from dsl_course import ghcli

DEMO_ORGS = frozenset(
    {
        "hertie-dsl-demo-course-e1234",
        "hertie-dsl-demo-f2026",
    }
)

_NARROW_ENV = "DSL_E2E_ORGS"


def orgs() -> frozenset[str]:
    """The orgs this run may touch: `DEMO_ORGS`, narrowed by `DSL_E2E_ORGS` if it is set.

    An org named there that is not a demo org RAISES rather than being ignored, because
    the one thing an operator could mean by it is the one thing this must not do."""
    named = {n.strip() for n in os.environ.get(_NARROW_ENV, "").split(",") if n.strip()}
    if not named:
        return DEMO_ORGS
    outside = sorted(named - DEMO_ORGS)
    if outside:
        raise RuntimeError(
            f"{_NARROW_ENV} may only narrow the demo orgs, never widen them - "
            f"{', '.join(outside)} is not one of {', '.join(sorted(DEMO_ORGS))}"
        )
    return frozenset(named)


def assert_allowed(org: str) -> None:
    """Raise unless `org` is in scope for this run."""
    if org not in orgs():
        raise RuntimeError(
            f"{org} is not in scope for this run ({', '.join(sorted(orgs()))})"
        )


def assert_fence() -> frozenset[str]:
    """Raise unless the transport fence is up and points at the demo orgs only.

    The harness runs with a maintainer token that can delete repositories, so this is
    checked before anything is dispatched and again before cleanup deletes anything."""
    fence = ghcli.org_allowlist()
    if fence is None:
        raise RuntimeError(
            "DSL_ORG_ALLOWLIST is not set - refusing to run against real orgs without "
            f"the transport fence. Set it to {','.join(sorted(orgs()))}."
        )
    outside = sorted(fence - DEMO_ORGS)
    if outside:
        raise RuntimeError(
            f"DSL_ORG_ALLOWLIST reaches past the demo orgs ({', '.join(outside)})"
        )
    return fence
