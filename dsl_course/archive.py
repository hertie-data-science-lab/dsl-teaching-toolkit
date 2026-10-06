"""dsl-course archive -- archive a finished semester: every repo read-only, nothing deleted.

The documented entry point (`python3 -m dsl_course.archive`). The work lives in
`dsl_course.teardown`, whose module name is a frozen CLI contract: an org whose Archive
semester workflow has not been re-rendered yet still runs it by that name.

Usage:
    python3 -m dsl_course.archive --course-org COURSE --semester-org SEMESTER --no-preview
"""

from __future__ import annotations

import sys

from . import teardown


def main() -> int:
    return teardown.main()


if __name__ == "__main__":
    sys.exit(main())
