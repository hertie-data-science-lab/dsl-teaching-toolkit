// The one help screen (`#help`, the ? in the top bar): what persists in the course, what
// belongs to one term's cohort, and where each thing lives. Inline SVG, drawn with the
// theme's tokens so both themes read.

const COURSE_ITEMS = ['Course details', 'Materials', 'Assignment templates', 'Public website'];
const COHORT_ITEMS = ['Schedule', 'Staff', 'Roster', 'Assignments: teams, marks', 'Student site'];

const WHERE = [
  'Course details, materials and assignment templates live in the course and carry over to every term.',
  'The public website belongs to the course: the open part of its materials, for anyone.',
  'Dates live in the cohort’s schedule: releases, hand outs, due dates and events.',
  'Staff and the roster live in the cohort: who teaches and who takes the course this term.',
  'Teams and marks live with each assignment of the cohort; open it from Assignments.',
  'The student site belongs to the cohort; students see only their own term.',
];

function Diagram() {
  const list = (items: string[], x: number, y: number) =>
    items.map((t, i) => <text class="hd-item" x={x} y={y + i * 22}>{t}</text>);
  return (
    <svg class="help-diagram" viewBox="0 0 760 330" role="img" aria-labelledby="hd-title hd-desc">
      <title id="hd-title">Course, cohorts and students</title>
      <desc id="hd-desc">
        The course persists: course details, materials, assignment templates and the public website. It has one cohort per term,
        each with its schedule, staff, roster, assignments with teams and marks, and student site. Students join a cohort, never the course.
      </desc>
      <defs>
        <marker id="hd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path class="hd-head" d="M0 0L10 5L0 10z" />
        </marker>
      </defs>
      <rect class="hd-box" x="10" y="50" width="230" height="200" rx="12" />
      <text class="hd-h" x="30" y="86">Course</text>
      <text class="hd-sub" x="30" y="108">persists across terms</text>
      {list(COURSE_ITEMS, 30, 144)}

      <rect class="hd-box hd-now" x="310" y="14" width="270" height="200" rx="12" />
      <text class="hd-h" x="330" y="48">Cohort: Fall 2026</text>
      <text class="hd-sub" x="330" y="70">one per term</text>
      {list(COHORT_ITEMS, 330, 104)}

      <rect class="hd-box" x="310" y="236" width="270" height="80" rx="12" />
      <text class="hd-h" x="330" y="270">Cohort: Spring 2027</text>
      <text class="hd-sub" x="330" y="292">the next term, same course</text>

      <path class="hd-line" d="M240 130 H300" marker-end="url(#hd-arrow)" />
      <path class="hd-line" d="M240 200 C 272 200, 272 276, 300 276" marker-end="url(#hd-arrow)" />
      <text class="hd-note" x="248" y="120">runs as</text>

      <rect class="hd-box hd-people" x="620" y="60" width="132" height="100" rx="12" />
      <text class="hd-h" x="634" y="94">Students</text>
      <text class="hd-sub" x="634" y="118">join a cohort,</text>
      <text class="hd-sub" x="634" y="138">never the course</text>
      <path class="hd-line" d="M620 110 H590" marker-end="url(#hd-arrow)" />
    </svg>
  );
}

export function HelpScreen() {
  return (
    <>
      <div class="page-head">
        <div><h1>How the console is organised</h1><p class="lede">A course persists from year to year. Each term runs as a cohort of it.</p></div>
      </div>
      <div class="stack">
        <section class="panel section">
          <Diagram />
        </section>
        <section class="panel section">
          <h2>Where does it live</h2>
          <ul class="where-list">{WHERE.map((w) => <li>{w}</li>)}</ul>
        </section>
      </div>
    </>
  );
}
