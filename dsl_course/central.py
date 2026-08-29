"""Where the central toolkit lives.

Every seeded workflow checks this repo out and runs its engine code from it (see
workflows_render), and the generated READMEs link back to it (see profile_readme) - so
both sides must name the same repo/ref. One definition, imported by both.
"""

from __future__ import annotations

CENTRAL = "hertie-data-science-lab/dsl-teaching-toolkit"
# Seeded workflows run the engine code from this ref of the central repo.
#
# `release`, not `main`. Every seeded workflow in every org checks the engine out at run
# time, so whatever sits on this ref IS production, in every live course, from the moment
# it lands - a merge on a Tuesday afternoon reaches a release running that evening with no
# deploy step in between and no way to try it anywhere first. Promoting main to `release`
# deliberately puts a decision in that gap; rollback is a revert on `release`, which every
# org picks up on its next run rather than needing a re-seed.
CENTRAL_REF = "release"
