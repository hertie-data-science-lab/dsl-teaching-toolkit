"""One template with two formats - a notebook to run and a LaTeX write-up to read - taken
through every stage that reads `formats`: scaffold, derive, collect, the sheet and the
return of marks (decision 0009 rule 10)."""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from dsl_course import collect, derive, grades, scaffold
from tests.test_collect import _QUESTION_NB, _capture_archive, _checkout
from tests.test_grades import ROSTER_ADA, _distribute, _schedule_with
from tests.test_scaffold import _solution_files, fake  # noqa: F401 - the fixture

QUESTIONS = "questions:\n  Q1: 10\n  Q2: {points: 5, file: starter.tex}\n"


def test_a_two_format_template_is_scaffolded_derived_collected_and_returned(
    fake,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    # scaffold: one starter per format on main, a fenced model answer per format
    written = _solution_files(monkeypatch)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", ["ipynb", "latex"]) == 0
    assert {"starter.ipynb", "starter.tex"} <= fake.written("assignment-1-f2026")

    # derive: each file type in its own fence vocabulary
    for name in ("starter.ipynb", "starter.tex"):
        stripped = derive.strip_source(f"solution/{name}", written[f"solution/{name}"])
        assert stripped.replaced == 1 and "42" not in stripped.text

    # the first format is the runnable one; Q2 is marked from the write-up
    gspec = grades.parse_grading_spec(written["grading_config.yml"] + QUESTIONS)
    assert gspec.formats == ("ipynb", "latex") and gspec.format == "ipynb"
    assert gspec.runs_completion_check
    assert gspec.question_files == {"Q2": "starter.tex"}

    # collect: at the cutoff the grader copies carry the notebook's questions and the
    # write-up. Hand-marked here, so no hidden tests and no completion check run.
    gspec = grades.parse_grading_spec(
        written["grading_config.yml"]
        .replace("grader_pdf: false", "grader_pdf: true")
        .replace("completion_check: true", "completion_check: false")
        + QUESTIONS
    )
    sched = _schedule_with("assignment-1")
    monkeypatch.setattr(collect.schedule, "load", lambda org: sched)
    monkeypatch.setattr(collect, "load_grading_spec", lambda *a, **k: gspec)
    monkeypatch.setattr(collect, "sandbox_unusable", lambda: "")
    monkeypatch.setattr(
        collect, "sync_sheet", lambda *a, **k: SimpleNamespace(written=True)
    )
    _checkout(monkeypatch, {"starter.ipynb": _QUESTION_NB, "starter.tex": "\\A"})
    archived = _capture_archive(monkeypatch)
    monkeypatch.setattr(collect, "_run_limited", lambda argv, **k: True)
    monkeypatch.setattr(collect, "_pdf_engine_present", lambda: False)
    assert collect.collect("Org", "assignment-1-f2026", "Semester") == 0
    assert {
        ".system/autograde/assignment-1/alice.ipynb",
        ".system/autograde/assignment-1/alice.starter.tex",
    } <= set(archived)

    # the sheet: one total, a feedback cell per question, the file named beside Q2
    spec = grades.sheet_spec(sched, "assignment-1", "assignment-1", gspec, False)
    assert spec.question_files == {"Q2": "starter.tex"}
    sheet = grades.new_sheet(spec, [("ada-l", ["ada-l"])])
    block = sheet["submissions"]["ada-l"]
    assert block[grades.QUESTION_FEEDBACK_KEY] == {"Q1": None, "Q2": None}
    assert "# /5 · starter.tex" in grades.dump_sheet(sheet, spec, "OPEN")

    # distribute: overall then per-question feedback, in the gradebook and the email
    block.update(
        score_individual={"Q1": "9", "Q2": "4"},
        feedback_individual="Well done.",
        **{grades.QUESTION_FEEDBACK_KEY: {"Q1": None, "Q2": "Cite the lemma."}},
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": yaml.safe_dump(sheet, sort_keys=False)},
        grading="title: Two formats\n" + QUESTIONS,
        roster_rows=ROSTER_ADA,
        include_feedback=True,
    )
    assert out["rc"] == 0
    ((_repo, files, _d),) = out["gradebooks"]
    book = yaml.safe_load(files["grades.yml"])["assignments"]["assignment-1"]
    assert book["final_grade"] == "13"  # one total over both formats
    assert book[grades.QUESTION_FEEDBACK_KEY] == {"Q2": "Cite the lemma."}
    assert "- **Q2:** Cite the lemma." in files["README.md"]
    ((message,),) = out["outbox"]
    assert message[2].index("Well done.") < message[2].index("Q2: Cite the lemma.")
