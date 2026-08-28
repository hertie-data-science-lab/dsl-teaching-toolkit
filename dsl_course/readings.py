"""The reading-overlay filenames: which file in a `readings/NN_.../` folder is prose to
inline, and which are files to list.
"""

from __future__ import annotations

# A session's OPTIONAL prose reading list, the one file in a `readings/NN_.../` folder that
# is inlined as text rather than listed as a download. Named once here because `scaffold`
# seeds it and `site`/`syllabus` match on it, so a rename that reached only one of them
# would have the scaffold quietly seeding a file the renderer no longer recognises as prose.
#
# Matched by whole filename, never by extension. Deciding by extension made an uploaded
# `lecture-notes.md` or `refs.bib` - a reading in its own right - get swallowed into the page
# as prose instead of listed as a file a student can download.
READING_OVERLAY_FILE = "READINGS.md"
READING_OVERLAY_NAMES = frozenset(
    {"readings.md", "readings.markdown", "readings.txt", "readings.bib"}
)
