// One render per screen, with the contracts example as the status fixture.

import { options } from 'preact';
import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import { AppAuth } from '../src/auth/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import { signal } from '@preact/signals';
import { EnvCtx, type Env } from '../src/env';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { AssignmentScreen, AssignmentsScreen } from '../src/screens/Assignments';
import { CohortScreen } from '../src/screens/Cohort';
import { CourseScreen, SetupList, TemplateScreen, readyWords, semesterChip, stepLink } from '../src/screens/Course';
import { HomeScreen, ReadonlyScreen, SignInScreen } from '../src/screens/Home';
import { InstructorsScreen, StudentsScreen } from '../src/screens/People';
import { ReleaseScreen, ScheduleScreen } from '../src/screens/Schedule';
import { OperationsScreen, SiteScreen } from '../src/screens/Site';
import type { CohortProps } from '../src/screens/types';
import { generateSyllabus } from '../src/ops/defs';
import { OutcomeView } from '../src/ops/Panel';
import { ScreenBoundary } from '../src/ui/boundary';
import { Footer, Sidenav, Topbar } from '../src/ui/shell';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = {
  org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: 'A course.', write: true,
  admins: ['a-example'], cohorts: [cohort], meta: { assignment_defaults: { late_window_days: 10, late_penalty_per_day: '10%', max_team_size: 5 } },
};
const ready: Loaded = { kind: 'ready', status: STATUS, sha: 's', stale: [] };

const SCHEDULE = `timezone: Europe/Berlin
semester_start: 2026-09-07
semester_end: 2026-12-18
releases:
  s5:
    event_datetime: 2026-10-08T10:00
    title: Trees and ensembles
    details: Trees, bagging and *random forests*.
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/05_trees
assignments:
  assignment-2:
    course_source_repo: assignment-2-f2026
    details: Fit, regularise and **explain** a model.
    handout_datetime: 2026-09-15T10:00
    due_datetime: 2026-09-27T23:59
events:
  midterm:
    kind: exam
    title: Midterm
    details: Room 2.61, closed book.
    event_datetime: 2026-10-22T10:00
  reading:
    title: Reading week
    event_datetime: 2026-10-26
`;
const STUDENTS = '﻿hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@students.example.org,Anna Adams,enrolled,anna-a,101,SECRETCODE1,2026-09-02T09:30:00Z\nben@example,Ben Baker,enrolled,,,,\ncarla@students.example.org,Carla Cohen,auditor,,,SECRETCODE3,2026-09-02T09:30:00Z\n';
const PEOPLE = 'instructors:\n  - github_handle: a-example\n    role: instructor\n    email: a@staff.example.org\n    name: Dr A. Example\n    photo: images/a.jpg\n  - github_handle: b-sample\n    role: teaching_assistant\n    email: b@staff.example.org\n    name: B. Sample\n    start: "2026-09-01"\n    end: "2026-12-31"\n';
const GRADING = 'title: Group project\ntype: group\nteam_formation: self_select\nmax_team_size: 4\nsubmit_via: assignment_repo\nvisibility: private\nformats: [ipynb]\nautograde: sometimes\ncompletion_check: true\ngrader_pdf: false\nquestions:\n  proposal: 20\n  analysis: 30\n';
const OUTCOME = JSON.stringify({ schema: 'dsl.outcome/1', op: 'release.now', run_id: 4821, actor: 'a', preview: false, conclusion: 'done', summary: 'x', counts: { files: 7 }, reasons: [{ code: 'RELEASED', text: 'lectures/03 copied' }] });

const TREE = { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/05_trees_and_ensembles/slides.html', 'lectures/05_trees_and_ensembles/notes.pdf', 'lectures/03_regularisation/slides.html'] };

const files = new StaticFiles(
  {
    [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE,
    [`${COHORT_ORG}/semester-config/students.csv`]: STUDENTS,
    [`${COHORT_ORG}/semester-config/instructors.yml`]: PEOPLE,
    [`${COHORT_ORG}/semester-config/.system/outcomes/release.now.json`]: OUTCOME,
    [`${COHORT_ORG}/${COHORT_ORG}.github.io/index.md`]: '---\nlayout: home\n---\nWelcome to **Machine Learning**.\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: GRADING,
  },
  { [`${COHORT_ORG}/${COHORT_ORG}.github.io/_announcements`]: ['2026-09-21-scikit.md'] },
  TREE,
);

const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files, now: NOW, heartbeat: { lastTick: '2026-09-23T07:48:00Z', late: false }, ...over });
const html = (v: preact.VNode) => render(v);
const text = (v: preact.VNode) => html(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#39;|&rsquo;/g, '’').replace(/\s+/g, ' ');

describe('S0 sign in', () => {
  it('without a relay, asks only for a token', () => {
    const auth = new ConsoleAuth(new PatAuth({ store: null }), null);
    const t = text(<SignInScreen auth={auth} onSignedIn={() => {}} />);
    expect(t).toContain('GitHub token');
    expect(t).toContain('The console can see and change only what your GitHub account can.');
    expect(html(<SignInScreen auth={auth} onSignedIn={() => {}} />)).not.toContain('>Sign in with GitHub</button>');
    expect(t).toMatch(/repo .*workflow/);
    expect(t).toContain('fine-grained token');
    expect(t).toContain('without a sign-in relay');
    expect(html(<SignInScreen auth={auth} onSignedIn={() => {}} />)).toContain('scopes=repo,workflow');
  });

  it('with the App, offers Sign in with GitHub first and the token second', () => {
    const app = new AppAuth({ clientId: 'Iv1.x', relayUrl: 'https://relay.example', redirectUri: 'https://c.example/', store: null });
    const auth = new ConsoleAuth(new PatAuth({ store: null }), app);
    auth.notice = 'Sign-in was cancelled on GitHub.';
    const t = text(<SignInScreen auth={auth} onSignedIn={() => {}} />);
    expect(t.indexOf('Sign in with GitHub')).toBeGreaterThan(-1);
    expect(t.indexOf('Sign in with GitHub')).toBeLessThan(t.indexOf('Use a token instead'));
    expect(t).toContain('Sign-in was cancelled on GitHub.');
  });
});

describe('S1 home', () => {
  it('lists cohorts by urgency with their problem counts and next dates, read-only ones greyed', () => {
    const ro: Course = { ...course, org: 'hertie-ids-c11', name: 'Intro to Data Science', write: false, cohorts: [{ org: 'hertie-ids-f2026', term: 'f2026', termLabel: 'Fall 2026' }] };
    const out = html(<HomeScreen courses={[course, ro]} cohortStates={{ [COHORT_ORG]: ready }} now={NOW} user={{ login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }} />);
    expect(out).toContain('Machine Learning, Fall 2026');
    expect(out).toContain('2 problems');
    expect(out).toContain('Week 3 of 15');
    expect(out).toContain('you are a course admin');
    expect(out).toContain('Session 3: Trees');
    expect(out).toMatch(/cohort-card ro[^"]*" href="\?cohort=hertie-ids-f2026/);
    expect(out.indexOf('Machine Learning, Fall 2026')).toBeLessThan(out.indexOf('Intro to Data Science, Fall 2026'));
  });
});

describe('S4 cohort overview', () => {
  const out = html(<CohortScreen {...props()} />);
  const t = text(<CohortScreen {...props()} />);
  it('leads with the header, term strip, problems, this week and assignments', () => {
    expect(html(<CohortScreen {...props()} />)).not.toContain('class="crumbs"'); // the semester banner replaces them
    expect(t).toContain('Dashboard ? What this semester has planned');
    expect(t).toContain('Exam 22 Oct. Archive 31 Jan 2027.');
    expect(t).not.toContain('Week 3 of 15');
    expect(t).toContain('Setup done, but 2 stages have a problem');
    expect(out).toContain('class="term-strip"');
    expect((out.match(/class="wk[ "]/g) ?? []).length).toBe(15);
    expect(out).toContain('wk now');
    // This week on load: the s5 problem (week 5) is counted on its cell, not listed.
    expect(t).not.toContain('Session 5 cites folder lectures/05_trees');
    expect(out).toMatch(/aria-label="Week 5, from 5 Oct[^"]*; 1 problem"/);
    expect(out).toContain('href="#template-assignment-3-f2026"');
    expect(t).toContain('(course)');
    expect(t).toContain('Assignment 2: Regression');
    expect(t).toContain('37 of 48 submitted so far.');
  });
  it('shows students beside automation, heartbeat from the run list', () => {
    expect(t).toContain('on the roster');
    expect(t).toMatch(/Checked \d+ min ago/);
    expect(t).toContain('Released Session 3: 7 files to materials.');
  });
  it('has Refresh and More in the header, with the More items live', () => {
    expect(out).toMatch(/<button class="btn" type="button">Refresh<\/button>/);
    expect(t).toContain('Preview the next automatic run');
    expect(t).toContain('Keep for future semesters');
    expect(out).not.toMatch(/role="menuitem" disabled/);
    expect(t).toContain('Pick weeks in the strip to show only those weeks.');
  });
  it('flags a stale status', () => {
    expect(text(<CohortScreen {...props({ loaded: { ...ready, stale: ['schedule.yml'] } as Loaded })} />)).toContain('schedule.yml changed since this semester was last checked');
  });
  it('shows Status not computed yet when the file is absent', () => {
    const a = html(<CohortScreen {...props({ loaded: { kind: 'absent' } })} />);
    expect(a).toContain('Status not computed yet');
    expect(a).toMatch(/<button class="btn" type="button">Refresh/);
  });
});

describe('S16 and S10 assignments', () => {
  it('lists every assignment with state and progress', () => {
    const t = text(<AssignmentsScreen {...props()} />);
    expect(t).toContain('Assignment 2: Regression');
    expect(t).toContain('open');
    expect(t).toContain('37 of 48 submitted');
  });
  it('shows the lifeline, dates and actions by state', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2' })} />);
    expect(out).toContain('class="lifeline"');
    expect(out).toMatch(/<li class="now" aria-current="step">.*?Open/);
    expect(out).toContain('How this semester runs it');
    expect(out).toContain('Late cutoff');
    expect(out).toContain('Update every copy');
    expect(out).toContain('Collect now');
    expect(out).toContain('Assignment template ready.');
  });
  it('says when an assignment is not in the status', () => {
    expect(text(<AssignmentScreen {...props({ entry: 'assignment-9' })} />)).toContain('No assignment called assignment-9');
  });
});

describe('S6 schedule and S11 release', () => {
  it('shows Type, Details as markdown and State, with events from schedule.yml', () => {
    const out = html(<ScheduleScreen {...props()} />);
    expect(out).toContain('<span>Details</span>');
    expect(out).toContain('<i>random forests</i>');
    expect(out).toContain('st-chip skip');
    expect(out).toContain('<b>Exam</b>: Midterm');
    expect(out).toContain('<b>Lecture 5</b>: Trees and ensembles');
    expect(out).toContain('<b>Assignment 2</b>: Regression');
    expect(out).toContain('today-line');
  });
  it('opens the entry a Fix link points to', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toContain('trow lec fault current');
    expect(out).toContain('class="entry"');
    expect(out).toMatch(/<option value="course-materials-f2026" selected>/);
    expect(out).toContain('id="e-d0-folder" list="folders-0" value="lectures/05_trees"');
    expect(out).toContain('Not found. The release will be skipped until the folder exists.');
    expect(out).toContain('Use the one that exists: <button');
    expect(out).toContain('lectures/05_trees_and_ensembles');
    expect(out).toContain('schedule.yml#L5');
  });
  it('pre-selects the inferred kind, from the folder the copy lands in and the repo’s own aliases', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toContain('<option value selected>Lecture (inferred)</option>');
    const aliased = new StaticFiles({ ...Object.fromEntries(['schedule.yml'].map((f) => [`${COHORT_ORG}/semester-config/${f}`, SCHEDULE])), [`${COURSE_ORG}/course-materials-f2026/materials.yml`]: 'kinds:\n  lectures: lab\n' }, {}, TREE);
    expect(html(<ScheduleScreen {...props({ entry: 's5', files: aliased })} />)).toContain('<option value selected>Lab (inferred)</option>');
  });
  it('lists the materials repos and the Other repos to release from', () => {
    const listed = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE }, {}, TREE, { [COURSE_ORG]: [{ name: '.github' }, { name: 'course-materials-f2026' }, { name: 'lecture-code-f2026' }, { name: 'assignment-3-f2026' }] });
    const out = html(<ScheduleScreen {...props({ entry: 's5', files: listed })} />);
    expect(out).toMatch(/<optgroup label="Materials repos"><option value="course-materials-f2026" selected>/);
    expect(out).toContain('<optgroup label="Other repos"><option value="lecture-code-f2026">lecture-code-f2026</option></optgroup>');
  });
  it('renders a release with its source, destination and problem', () => {
    const t = text(<ReleaseScreen {...props({ entry: 's5' })} />);
    expect(t).toContain('Lecture 5 : Trees and ensembles');
    expect(t).toContain('will be skipped');
    expect(t).toContain(`${COURSE_ORG}/course-materials-f2026/lectures/05_trees`);
    expect(t).toContain(`${COHORT_ORG}/materials/lectures/05_trees`);
    expect(t).toContain('Fix the folder');
  });
  const unstaged: Loaded = {
    kind: 'ready', sha: 's', stale: [],
    status: { ...STATUS, releases: [...(STATUS.releases ?? []), { id: 'lecture-12', when: '2026-12-10T10:00:00+01:00', kind: null, title: 'Review', state: 'planned', source: null, dest: null, show_on_site: true, tbc: false }] },
  };
  it('renders a release with no deploy block as nothing to release, with no Release early', () => {
    const out = html(<ScheduleScreen {...props({ loaded: unstaged })} />);
    expect(out).toContain('<b>Lecture 12</b>: Review');
    expect(out).toMatch(/<li class="trow term" data-entry="lecture-12"><span class="k">release<\/span>/);
    expect(out).toContain('Nothing to release yet: this entry has no deploy block');
    expect(out).toContain('href="#schedule-lecture-12"');
    expect(out).not.toContain('Release early');
  });
  it('shows the nothing-to-release page for a release with no source', () => {
    const t = text(<ReleaseScreen {...props({ loaded: unstaged, entry: 'lecture-12' })} />);
    expect(t).toContain('Nothing to release yet: this entry has no deploy block.');
    expect(t).toContain('Edit entry');
    expect(t).not.toContain('Release early');
  });
});

describe('operation outcome', () => {
  it('shows the generated text directly, and what the op touched in the details fold', () => {
    const def = generateSyllabus({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 'course-materials-f2026');
    const outcome = { schema: 'dsl.outcome/1' as const, op: def.op, run_id: 7, actor: 'a', preview: true, conclusion: 'previewed' as const, summary: 'Preview: the session list.', reasons: [{ code: 'NO_SOLUTION_REGION', text: 'solution.py\nhas no region' }], details: ['main/solution.py', 'main/README.md'], block: '## Course sessions and readings\n- Session 1: Intro' };
    const out = html(<OutcomeView result={{ outcome, people: [], leaked: [] }} def={def} />);
    expect(out).toContain('<summary>Details</summary>');
    expect(out).toContain('<ul class="outcome-list"><li>main/solution.py</li><li>main/README.md</li></ul>');
    expect(out).toContain('<pre class="outcome-details">## Course sessions and readings\n- Session 1: Intro</pre>');
    expect(out).toContain('>Copy</button>');
    // The session list is what a preview is for (finding 31): above the fold, not in it.
    expect(out.indexOf('outcome-details')).toBeLessThan(out.indexOf('<summary>Details</summary>'));
    expect(out).toContain('<td class="pre">solution.py\nhas no region');
  });
});

describe('screen error boundary', () => {
  const Boom = (): preact.VNode => {
    throw new Error('kaboom');
  };
  // The string renderer honours boundaries only when asked; the browser always does.
  const withBoundaries = (fn: () => void) => {
    const o = options as { errorBoundaries?: boolean };
    const was = o.errorBoundaries;
    o.errorBoundaries = true;
    try {
      fn();
    } finally {
      o.errorBoundaries = was;
    }
  };
  it('shows the error instead of the screen that threw', () => withBoundaries(() => {
    const t = text(<ScreenBoundary><Boom /></ScreenBoundary>);
    expect(t).toContain('This screen hit an error');
    expect(t).toContain('kaboom');
    expect(html(<ScreenBoundary><Boom /></ScreenBoundary>)).toContain('href="#dashboard"');
  }));
  it('links Home when the Dashboard itself threw, so the route key changes', () => withBoundaries(() => {
    vi.stubGlobal('location', { hash: '#dashboard' });
    try {
      const out = html(<ScreenBoundary><Boom /></ScreenBoundary>);
      expect(out).toContain('href="#">Back to Home');
      expect(out).not.toContain('href="#dashboard"');
    } finally {
      vi.unstubAllGlobals();
    }
  }));
});

describe('S8 students', () => {
  const out = html(<StudentsScreen {...props()} />);
  it('shows counts and the grid with system columns, never the enrol code', () => {
    expect(out).toContain('<span class="n">48</span>');
    expect(out).toContain('Anna Adams');
    expect(out).toContain('class="sys"');
    expect(out).toContain('Joined');
    expect(out).toContain('Code sent; not joined');
    expect(out).toContain('Not sent');
    expect(out).not.toContain('SECRETCODE');
  });
});

describe('S7 staff', () => {
  it('lists instructors and teaching assistants with access and dates', () => {
    const t = text(<InstructorsScreen {...props()} />);
    expect(t).toContain('Dr A. Example');
    expect(t).toContain('Teaching assistant');
    expect(t).toContain('Has access');
    expect(t).toContain('1 Sep to 31 Dec');
    expect(t).toContain('1 instructor and 1 teaching assistant');
  });
});

describe('S14 site and S18 operations', () => {
  it('shows the last update, the home text as students see it, and announcements', () => {
    const out = html(<SiteScreen {...props()} />);
    expect(out).toContain('Out of date.');
    expect(out).toContain('Welcome to <b>Machine Learning</b>.');
    expect(out).not.toContain('layout: home');
    expect(out).toContain('2026-09-21-scikit');
  });
  it('lists operations with the outcome sentence, details fold and the run', () => {
    const out = html(<OperationsScreen {...props()} />);
    expect(out).toContain('Released Session 3: 7 files to materials.');
    expect(out).toContain('RELEASED');
    expect(out).toContain(`https://github.com/${COURSE_ORG}/.github/actions/runs/4821`);
  });
});

describe('S2 course and S17 template', () => {
  const cp = { course, loaded: { kind: 'absent' } as Loaded, cohortStates: { [COHORT_ORG]: ready }, files, now: NOW };
  it('shows problems, templates, materials, details and cohorts', () => {
    const t = text(<CourseScreen {...cp} />);
    // C1-C3 are done; the template's problem is what holds the course back.
    expect(t).toContain('Not ready: a problem below needs fixing.');
    expect(t).not.toMatch(/\b0 (setup steps|problems)/);
    expect(t).toContain('Setup steps are the one-time things a course needs. To-dos are work started but not finished.');
    expect(t).toContain('Unfinished work is a to-do on the left, not a problem.');
    expect(t).toContain('Students get only what a semester releases or hands out');
    expect(t).toContain('Marking of Assignment 3 cannot start.');
    expect(t).toContain('assignment-3-f2026');
    expect(t).toContain('course-materials-f2026');
    expect(t).toContain('10 days at 10% a day, this course');
    expect(t).toContain('up to 5, ');
    expect(t).not.toContain('this course’s default');
    expect(t).toContain('Fall 2026');
  });
  const base = STATUS.course!;
  const withWhy = {
    ...base,
    stages: { C1: 'done', C2: 'done', C3: 'todo', C4: 'todo', C5: 'blocked', C6: 'todo' },
    stage_why: {
      C3: 'Course details have no description yet.',
      C4: 'There is no materials repo yet.',
      C5: 'Waiting for the course to be set up.',
      C6: 'There is no public website; it is optional.',
    },
    materials: [],
    templates: [],
    ready: false,
  } as typeof base;
  it('puts the verdict under the page note with a tick or a red cross, not in the page head', () => {
    const out = html(<CourseScreen {...cp} />);
    expect(out).toContain('<p class="verdict bad"><svg');
    expect(out).toContain('<span>Not ready: a problem below needs fixing.</span></p>');
    expect(out).not.toContain('class="lede"');
    expect(out.indexOf('class="page-note"')).toBeLessThan(out.indexOf('class="verdict'));
    // Above the read-only banner too: the verdict sits directly under the note.
    const ro = html(<CourseScreen {...cp} course={{ ...course, write: false }} />);
    expect(ro.indexOf('class="verdict')).toBeLessThan(ro.indexOf('class="ro-banner"'));
    const fine: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...base, ready: true } }, sha: 's', stale: [] };
    const ok = html(<CourseScreen {...cp} loaded={fine} />);
    expect(ok).toContain('<p class="verdict ok"><svg');
    expect(ok).toContain('<span>Ready for a new semester.</span>');
  });
  it('lists the to-dos under Setup & To do, each with where it is done', () => {
    const out = html(<CourseScreen {...cp} />);
    expect(out).toContain('Setup &amp; To do');
    expect(out).toContain('<h3 class="todo-head">To do</h3>');
    expect(out).toContain('<span class="slug">course-materials-f2026</span> The weekly plan has not been generated yet. <a class="textlink" href="#materials-course-materials-f2026" aria-label="Open course-materials-f2026 settings">Open settings</a>');
    const none: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...base, todo: [] } }, sha: 's', stale: [] };
    expect(text(<CourseScreen {...cp} loaded={none} />)).toContain('Nothing to do.');
    const tpl: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...base, todo: [{ id: 'template:a:brief', kind: 'template', repo: 'a', text: 'The brief (README.md) is not written yet.' }] } }, sha: 's', stale: [] };
    expect(html(<CourseScreen {...cp} loaded={tpl} />)).toContain('href="#template-a" aria-label="Open a settings">Open settings</a>');
  });
  it('shows every course fact in Course details, the institution’s in grey', () => {
    const t = text(<CourseScreen {...cp} />);
    expect(t).toContain('Description ? One paragraph about the course, shown on the public website. In dsl-course.yml. Not set');
    expect(t).toMatch(/Contact \? Who students and the lab write to about the course\. In dsl-course\.yml; the institution’s contact when unset\. \S+@\S+, from the institution/);
    expect(t).toContain(', from the institution');
    expect(t).toContain('In dsl-course.yml. a-example');
    // Every fact carries the small ?.
    expect((html(<CourseScreen {...cp} />).match(/<dt>[^<]*<span class="hint small">/g) ?? []).length).toBe(8);
    expect(t).toContain('The course’s code in the catalogue, as students know it.');
    expect(t).toContain('A semester’s instructors and TAs are set on that semester’s Instructors page.');
    expect(t).toContain('This course’s default. Each assignment can set its own.');
    const own = { ...course, meta: { ...course.meta, course_description: 'Learning from data.', contact: 'ml@example.org', licence: 'CC BY 4.0' } };
    const mine = text(<CourseScreen {...cp} course={own} />);
    expect(mine).toContain('In dsl-course.yml. Learning from data.');
    expect(mine).toContain('when unset. ml@example.org');
    expect(mine).toContain('when unset. CC BY 4.0');
    expect(mine).not.toContain(', from the institution');
  });
  it('links the live public website once it is published', () => {
    expect(html(<CourseScreen {...cp} />)).not.toContain(`href="https://${COURSE_ORG}.github.io"`);
    const pub: Loaded = { kind: 'ready', status: { ...STATUS, course: { ...base, stages: { ...base.stages, C6: 'done' } } }, sha: 's', stale: [] };
    const out = html(<CourseScreen {...cp} loaded={pub} />);
    expect(out).toContain(`href="https://${COURSE_ORG}.github.io"`);
    const t = text(<CourseScreen {...cp} loaded={pub} />);
    expect(t).toContain('Optional: an open version of your materials for anyone on the internet, updated daily.');
    expect(t).toContain('Edit website details');
    expect(t).not.toContain('Public website settings');
    expect(out).toContain('<button class="btn small outline" type="button">Republish website</button>');
    expect(html(<CourseScreen {...cp} />)).toContain('<button class="btn small outline" type="button">Publish website</button>');
  });
  it('keeps only New semester in the head; the status line, Refresh and the course on GitHub sit under it', () => {
    const out = html(<CourseScreen {...cp} />);
    const head = out.slice(out.indexOf('class="page-head"'), out.indexOf('class="actions sub-actions"'));
    expect(head).toContain('>New semester</a>');
    expect(head).not.toContain('Publish');
    expect(head).not.toContain('Refresh');
    expect(head).not.toContain('on GitHub');
    const sub = out.slice(out.indexOf('class="actions sub-actions"'), out.indexOf('class="page-note"'));
    // The course's own status is absent here (a semester's copy fills the page), so no age.
    expect(text(<CourseScreen {...cp} />)).toContain('New semester Refresh ? Reads the course and its semesters from GitHub again');
    expect(sub).toContain('<button class="textlink" type="button">Refresh</button>');
    expect(sub).toContain(`href="https://github.com/${COURSE_ORG}"`);
    const none = text(<CourseScreen {...cp} cohortStates={{}} />);
    expect(none).toContain('Not computed yet · Refresh');
    const dated = new StaticFiles({}, {}, {});
    dated.lastChange = () => new Date(NOW - 3 * 3600000).toISOString();
    const fine: Loaded = { kind: 'ready', status: STATUS, sha: 's', stale: [] };
    expect(text(<CourseScreen {...cp} loaded={fine} files={dated} />)).toContain('Updated 3 h ago · Refresh');
    // A Refresh this session that changed nothing still reads just now.
    const runs = signal([{ run_id: 1, op: 'semester.check', conclusion: 'ok', summary: '', finished: new Date(NOW).toISOString(), course: COURSE_ORG }]);
    const env = { ops: { runs, current: signal(null) }, user: { login: 'a-example' } } as unknown as Env;
    expect(text(<EnvCtx.Provider value={env}><CourseScreen {...cp} loaded={fine} files={dated} /></EnvCtx.Provider>)).toContain('Updated just now · Refresh');
    // Read only: the GitHub link stays; no age, no Refresh.
    const ro = html(<CourseScreen {...cp} course={{ ...course, write: false }} />);
    const roSub = ro.slice(ro.indexOf('class="actions sub-actions"'), ro.indexOf('class="page-note"'));
    expect(roSub).toContain('Course on GitHub');
    expect(roSub).not.toContain('Refresh');
  });
  it('lays the overview out as two columns of stacked panels', () => {
    const out = html(<CourseScreen {...cp} />);
    expect((out.match(/class="grid-2/g) ?? []).length).toBe(1);
    const cols = out.slice(out.indexOf('class="grid-2 cols"'));
    const order = ['<h2>Setup', '<h2>Semesters</h2>', '<h2>Assignment templates</h2>', '<h2>Problems', '<h2>Course details</h2>', '<h3>Public website', '<h2>Recent activity</h2>', '<h2>Handout materials</h2>'].map((h) => cols.indexOf(h));
    expect(out).not.toContain('<h2>Public website');
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    const right = cols.indexOf('id="course-problems"');
    expect(cols.lastIndexOf('<div class="stack">', right)).toBeGreaterThan(cols.indexOf('id="sec-templates"'));
  });
  it('renders the setup checklist from stage_why: ticks, the why and one link each', () => {
    const out = html(<SetupList course={withWhy} />);
    expect((out.match(/class="done"/g) ?? []).length).toBe(2);
    expect((out.match(/class="open"/g) ?? []).length).toBe(4);
    expect(out).toContain('Course details have no description yet.');
    expect(out).toContain('href="#details"');
    expect(out).toContain(`href="?course=${COURSE_ORG}#new-materials"`);
    expect(out).toContain(`href="https://github.com/${COURSE_ORG}/.github"`); // C5 is blocked on C2
    expect(out).toContain('href="#website"');
    expect((out.match(/<span class="s-need">required<\/span>/g) ?? []).length).toBe(3);
    // Unmarked is optional: the website's own sentence says so.
    expect(out).not.toContain('>optional</span>');
    expect(out).toContain('<span class="sr">: To do</span>');
    expect(out).toContain('<span class="sr">: Blocked</span>');
    // Done lines carry no link and no sentence.
    expect(out).not.toContain('Open on GitHub');
  });
  it('links a problem step to its card and a blocked step to what it waits for', () => {
    expect(stepLink('C5', base)).toEqual({ href: '#course-problems', label: 'See the problem' });
    // C5 waits for C2 (set up on GitHub); C6 for C4, itself waiting for C2.
    const waiting = { ...withWhy, stages: { ...withWhy.stages, C2: 'todo', C4: 'blocked', C6: 'blocked' } } as typeof base;
    expect(stepLink('C5', waiting).label).toBe('Open .github');
    expect(stepLink('C6', waiting).label).toBe('Open .github');
    expect(stepLink('C6', withWhy)).toEqual({ href: '#website', label: 'Set up the public website' });
    expect(stepLink('C4', { ...withWhy, materials: [{ repo: 'm', state: 'todo' }] }).label).toBe('Open handout materials');
    expect(stepLink('C5', { ...withWhy, stages: { ...withWhy.stages, C5: 'todo' }, templates: [{ repo: 't', slug: 't', state: 'todo' }] }).label).toBe('Open templates');
    expect(html(<SetupList course={base} />)).toContain('href="#course-problems">See the problem</a>');
  });
  it('says how many required steps are left, never a problem count', () => {
    expect(readyWords(withWhy)).toBe('Not ready: 1 setup step left.');
    expect(readyWords({ ...withWhy, stages: { ...withWhy.stages, C1: 'todo', C2: 'blocked' } })).toBe('Not ready: 3 setup steps left.');
    expect(readyWords({ ...withWhy, ready: true })).toBe('Ready for a new semester.');
    expect(readyWords(base)).toBe('Not ready: a problem below needs fixing.');
  });
  it('reads a semester as live, ended but not archived, or archived', () => {
    const sem = STATUS.semester!;
    expect(text(<>{semesterChip(sem)}</>).trim()).toBe('Live');
    expect(text(<>{semesterChip({ ...sem, ended: true })}</>).trim()).toBe('Ended, not archived');
    expect(text(<>{semesterChip({ ...sem, live: false, ended: false })}</>).trim()).toBe('Archived');
  });
  it('reads grading_config.yml into the tiered form and marks the bad value', () => {
    const out = html(<TemplateScreen {...cp} entry="assignment-3-f2026" />);
    expect(out).toContain('<h1>Group project <span class="hint">');
    expect(out).toContain('<span>Group project</span></div>');
    expect(out).toContain('value="Group project"');
    expect(out).toMatch(/value="group" checked/);
    expect(out).toContain('The file says “sometimes”. Choose on or off.');
    expect(out).toContain('value="proposal"');
    expect(out).toContain('<td>50</td>');
    expect(out).toContain('Advanced <span class="cnt changed">(1 changed)</span>');
    expect(out).toContain('/edit/solution/grading_config.yml');
    expect(out).toContain('Derive student version');
  });
  it('explains marking, points per question and the student version with a ?', () => {
    const out = html(<TemplateScreen {...cp} entry="assignment-3-f2026" />);
    expect(out).toMatch(/<h3>How it is marked <span class="hint"><button[^>]*aria-label="About marking"/);
    expect(out).toMatch(/Points per question <span class="hint"><button[^>]*aria-label="About points per question"/);
    expect(out).toMatch(/<h3>Student version <span class="hint"><button[^>]*aria-label="About the student version"/);
    expect(out).toContain('Derive builds the main branch, the copy students get, from the solution branch, removing the marked answers.');
    // One place for the student-version sentence: the ?, not a paragraph under it too.
    expect(out).not.toContain('Builds the student starter on main');
  });
  it('shows a question marked from another file by its points', () => {
    const tagged = new StaticFiles(
      { [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: GRADING.replace('analysis: 30', 'analysis: {points: 30, file: report.tex}') },
      {},
      TREE,
    );
    const out = html(<TemplateScreen {...cp} files={tagged} entry="assignment-3-f2026" />);
    expect(out).toContain('value="30"');
    expect(out).toContain('<td>50</td>');
    expect(out).not.toContain('[object Object]');
  });
});

describe('read only and the shell', () => {
  it('shows the read-only view for a course the user cannot change', () => {
    const t = text(<ReadonlyScreen course={{ ...course, write: false }} cohort={cohort} />);
    expect(t).toContain('Read only.');
    expect(t).toContain('No roster, no marks, no buttons.');
  });
  it('puts the switcher, nav with the problem count and the on-GitHub links in the side nav, none in the bar', () => {
    const nav = html(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="dashboard" problems={2} />);
    expect(nav).toContain('Machine Learning, Fall 2026');
    expect(nav).toContain('class="n-count"');
    expect(nav).toContain('aria-current="page"');
    expect(nav).toContain('2 problems');
    expect(nav).toContain(`https://${COHORT_ORG}.github.io`);
    expect(nav).toContain('Course on GitHub');
    const top = html(<Topbar user={{ login: 'a', id: 1, name: 'A', email: null, avatar_url: '' }} navOpen={false} onMenu={() => {}} />);
    expect(top).not.toContain('github.io');
    expect(top).not.toContain('on GitHub');
    const foot = text(<Footer course={course} cohort={cohort} />);
    expect(foot).toContain('Friedrichstraße 180');
    expect(foot).toContain('Part of the Hertie Data Science Lab.');
  });
});

describe('explicit numbers (decision 0020)', () => {
  const GUEST = `${SCHEDULE.replace('assignments:', `  guest:\n    event_datetime: 2026-09-25T10:00\n    kind: lecture\n    deploy:\n      - course_source_repo: course-materials-f2026\n        course_source_path: lectures/03_regularisation\nassignments:`)}`;
  const numbered: Loaded = {
    kind: 'ready', sha: 's', stale: [],
    status: {
      ...STATUS,
      releases: [...(STATUS.releases ?? []), { id: 'guest', when: '2026-09-25T10:00:00+02:00', kind: 'lecture', number: null, title: '', state: 'will_be_skipped', source: { repo: 'course-materials-f2026', path: 'lectures/03_regularisation' }, dest: { repo: 'materials', path: 'lectures/03_regularisation' }, show_on_site: true, tbc: false }],
      problems: [...(STATUS.problems ?? []), { id: 'number:lecture:guest', scope: 'semester', stage: 'K4', text: 'Give guest a number.', stops: 'The release on Fri 25 Sep will be skipped.', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'schedule.yml', line: 14, screen: 'schedule', entry: 'guest' }, when: '2026-09-25T10:00:00+02:00' }],
    },
  };
  const guestFiles = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: GUEST }, {}, TREE);

  it('shows an entry with no number by its kind alone, says why it is skipped, and asks for the number', () => {
    const out = html(<ScheduleScreen {...props({ loaded: numbered, files: guestFiles, entry: 'guest' })} />);
    expect(out).toContain('<b>Lecture</b>: ');
    expect(out).toContain('Give it a number first</span>');
    expect(out).toContain('Give it a number first; it cannot be released until it has one.');
    expect(out).toContain('Give guest a number.');
    expect(out).toContain('<input id="e-num" type="number" min="1" max="999" value style');
    expect(out).toContain('The number students see. Prefilled with the next one; change it if this is not the next lecture.');
  });

  it('shows a saved entry its own number, and the release detail says what it waits for', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toMatch(/<input id="e-num" type="number" min="1" max="999" value="5"/);
    const t = text(<ReleaseScreen {...props({ loaded: numbered, files: guestFiles, entry: 'guest' })} />);
    expect(t).toContain('Automation will skip this until it has a number.');
  });
});
