"""The course org registry: what parses, what raises, and the file the toolkit ships."""

from __future__ import annotations

import pytest

from dsl_course import org_registry


def test_names_are_read_casefolded():
    text = "course_orgs:\n  - hertie-maths-data-science-C23\n  - hertie-nlp-e1282\n"
    assert org_registry.parse(text) == {
        "hertie-maths-data-science-c23",
        "hertie-nlp-e1282",
    }


@pytest.mark.parametrize(
    "text",
    [
        "",
        "course_orgs: hertie-nlp-e1282\n",
        "orgs: []\n",
        "course_orgs:\n  - a/b\n",
        "[",
    ],
)
def test_anything_else_raises(text):
    # A registry read as empty would refresh nothing and look like a quiet deploy.
    with pytest.raises(RuntimeError, match="orgs.yml"):
        org_registry.parse(text)


def test_the_shipped_registry_names_every_live_course_org():
    orgs = org_registry.parse(org_registry.PATH.read_text())
    assert {
        "hertie-dsl-demo-course-e1234",
        "hertie-intro-to-data-science-c11",
        "hertie-maths-data-science-c23",
        "hertie-nlp-e1282",
    } <= orgs
