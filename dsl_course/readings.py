"""A session's reading list: which file in a `readings/NN_.../` folder is prose to inline,
which are files to list, and how the prose nests under the heading above it.

The rule in one place, because the cohort site, the public course site and the generated
syllabus all render the same folder and used to each decide it for themselves.
"""

from __future__ import annotations

from collections.abc import Callable

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


# The overlay is OPTIONAL - a session with only PDFs needs nothing written - and additive:
# it renders above the file list, never instead of it (see `readings_block`). Its stem
# equals `schedule_plan.READINGS_SECTION` by coincidence - a file stem and a folder name -
# not by derivation.


def is_reading_overlay(name: str) -> bool:
    """Is this path a session's optional prose reading list (`READINGS.md`, `.txt`, `.bib`)?

    The ONE test that decides prose-vs-file for a readings folder, by NAME rather than by
    extension - see `READING_OVERLAY_NAMES` above for why that distinction is the whole point.
    Takes a path or a bare name; only the last segment is read, so it works on the repo-tree
    paths, the release-relative names and the local filenames its callers each hold."""
    return name.rsplit("/", 1)[-1].lower() in READING_OVERLAY_NAMES


def readings_block(names: list[str], read_overlay: Callable[[str], str | None]) -> str:
    """A session's reading list: its overlay prose, then every OTHER file by name.

    THE rule, in one place, because its three readers - the cohort site, the public course
    site and the generated syllabus - each used to decide it for themselves and disagreed. A
    folder holding only PDFs rendered as links on one site, as a name list on another, and as
    nothing whatsoever in the syllabus, where a session came out an empty heading.

    Additive, never suppressive. The overlay is prose a reader wants first, so it leads; the
    files are what a student downloads, so they always follow. Neither hides the other:
    - files only - the file list IS the reading list, and nobody had to write anything;
    - overlay only - the URL-only week, one line of markdown and no citation format imposed;
    - both - prose on top, files beneath.

    `names` is every path in the session's readings folder; `read_overlay` reads one of them,
    and is a callable because its two callers hold different transports - the public site a
    local file, the syllabus a blob in the course org. Lazy, so only the overlay is ever
    fetched: reading eagerly would pull every PDF over the API just to find the prose.

    Raw filenames, deliberately: deriving "Blitzstein 2019 ch.1" from `blitzstein-ch1.pdf`
    would be inventing a citation. Faculty who want that write it in the overlay.

    The overlay is never ALSO listed as a download - its content is already on the page."""
    prose, files = [], []
    for name in sorted(names):
        if is_reading_overlay(name):
            text = (read_overlay(name) or "").strip()
            if text:
                prose.append(text)
        else:
            files.append(f"- {name.rsplit('/', 1)[-1]}")
    return "\n\n".join(prose + (["\n".join(files)] if files else []))


# How far the inlined reading list's own headings are pushed down, so they nest under the
# session heading the page puts above them. A reading file written in the Hertie syllabus
# shape opens with `# Session 1 readings` and sub-heads `## Required Readings`; at their
# written levels those outrank the page's own `<h2>Session 1`, which is exactly backwards.
HEADING_SHIFT = 2


def demote_headings(text: str, shift: int = HEADING_SHIFT) -> str:
    """Push every ATX heading in faculty-written markdown down `shift` levels.

    `shift` is the caller's, because the heading it has to nest under differs: the site puts
    a session at `<h2>`, the syllabus generator at `<h3>`.

    Only outside fenced code blocks: a `# comment` on the first line of a shell example is
    not a heading, and deepening it would rewrite the example. A heading also needs
    whitespace after its hashes, so `#hashtag` stays prose. Levels clamp at 6, which is as
    deep as HTML goes."""
    out, fenced = [], False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[hashes:]
            if rest[:1] in (" ", "\t", ""):
                line = "#" * min(hashes + shift, 6) + rest
        out.append(line)
    return "\n".join(out)
