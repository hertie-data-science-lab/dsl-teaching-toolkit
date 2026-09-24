// One render per screen, with the contracts example as the status fixture.

import { options } from 'preact';
import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import { PatAuth } from '../src/auth/pat';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { AssignmentScreen, AssignmentsScreen } from '../src/screens/Assignments';
import { CohortScreen } from '../src/screens/Cohort';
import { CourseScreen, TemplateScreen } from '../src/screens/Course';
import { HomeScreen, ReadonlyScreen, SignInScreen } from '../src/screens/Home';
import { StaffScreen, StudentsScreen } from '../src/screens/People';
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
    type: exam
    title: Midterm
    details: Room 2.61, closed book.
    event_datetime: 2026-10-22T10:00
  reading:
    title: Reading week
    event_datetime: 2026-10-26
`;
const STUDENTS = '﻿hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@students.example.org,Anna Adams,enrolled,anna-a,101,SECRETCODE1,2026-09-02T09:30:00Z\nben@example,Ben Baker,enrolled,,,,\ncarla@students.example.org,Carla Cohen,auditor,,,SECRETCODE3,2026-09-02T09:30:00Z\n';
const PEOPLE = 'people:\n  instructors:\n    - github_handle: a-example\n      email: a@staff.example.org\n      name: Dr A. Example\n      photo: images/a.jpg\n  teaching_assistants:\n    - github_handle: b-sample\n      email: b@staff.example.org\n      name: B. Sample\n      start: "2026-09-01"\n      end: "2026-12-31"\n';
const GRADING = 'title: Group project\ntype: group\nteam_formation: self_select\nmax_team_size: 4\nsubmit_via: assignment_repo\nvisibility: private\nformat: ipynb\nautograde: sometimes\ncompletion_check: true\ngrader_pdf: false\nquestions:\n  proposal: 20\n  analysis: 30\n';
const OUTCOME = JSON.stringify({ schema: 'dsl.outcome/1', op: 'release.now', run_id: 4821, actor: 'a', preview: false, conclusion: 'done', summary: 'x', counts: { files: 7 }, reasons: [{ code: 'RELEASED', text: 'lectures/03 copied' }] });

const TREE = { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/05_trees_and_ensembles/slides.html', 'lectures/05_trees_and_ensembles/notes.pdf', 'lectures/03_regularisation/slides.html'] };

const files = new StaticFiles(
  {
    [`${COHORT_ORG}/classroom-config/schedule.yml`]: SCHEDULE,
    [`${COHORT_ORG}/classroom-config/students.csv`]: STUDENTS,
    [`${COHORT_ORG}/classroom-config/people.yml`]: PEOPLE,
    [`${COHORT_ORG}/classroom-config/.dsl/outcomes/release.now.json`]: OUTCOME,
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
  it('asks for a classic token with repo and workflow', () => {
    const t = text(<SignInScreen auth={new PatAuth({ store: null })} onSignedIn={() => {}} />);
    expect(t).toContain('GitHub token');
    expect(t).toMatch(/repo .*workflow/);
    expect(html(<SignInScreen auth={new PatAuth({ store: null })} onSignedIn={() => {}} />)).toContain('scopes=repo,workflow');
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
    expect(t).toContain('Machine Learning, Fall 2026');
    expect(t).toContain('Week 3 of 15.');
    expect(t).toContain('Setup done, but 2 stages have a problem');
    expect(out).toContain('class="term-strip"');
    expect((out.match(/class="wk[ "]/g) ?? []).length).toBe(15);
    expect(out).toContain('wk now');
    expect(t).toContain('Session 5 cites folder lectures/05_trees');
    expect(t).toContain('The release on Thu 8 Oct will be skipped.');
    expect(out).toContain('href="#schedule-s5"');
    expect(out).toContain('href="#template-assignment-3"');
    expect(t).toContain('(course)');
    expect(t).toContain('Assignment 2: Regression');
    expect(t).toContain('37 of 48 submitted so far.');
  });
  it('shows students beside automation, heartbeat from the run list', () => {
    expect(t).toContain('on the roster');
    expect(t).toMatch(/Checked \d+ min ago/);
    expect(t).toContain('Released Session 3: 7 files to materials.');
  });
  it('has Check now and More in the header, with the More items live', () => {
    expect(out).toMatch(/<button class="btn" type="button">Check now<\/button>/);
    expect(t).toContain('Preview the next automatic run');
    expect(t).toContain('Keep cohort edits for future terms');
    expect(out).not.toMatch(/role="menuitem" disabled/);
    expect(t).toContain('What happens here');
  });
  it('flags a stale status', () => {
    expect(text(<CohortScreen {...props({ loaded: { ...ready, stale: ['schedule.yml'] } as Loaded })} />)).toContain('schedule.yml changed since this cohort was last checked');
  });
  it('shows Status not computed yet when the file is absent', () => {
    const a = html(<CohortScreen {...props({ loaded: { kind: 'absent' } })} />);
    expect(a).toContain('Status not computed yet');
    expect(a).toMatch(/<button class="btn" type="button">Check now/);
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
    expect(out).toContain('Late work until');
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
    expect(out).toContain('<b>Session 5</b>: Trees and ensembles');
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
  it('renders a release with its source, destination and problem', () => {
    const t = text(<ReleaseScreen {...props({ entry: 's5' })} />);
    expect(t).toContain('Session 5 : Trees and ensembles');
    expect(t).toContain('will be skipped');
    expect(t).toContain(`${COURSE_ORG}/course-materials-f2026/lectures/05_trees`);
    expect(t).toContain(`${COHORT_ORG}/materials/lectures/05_trees`);
    expect(t).toContain('Fix the folder');
  });
  const unstaged: Loaded = {
    kind: 'ready', sha: 's', stale: [],
    status: { ...STATUS, releases: [...(STATUS.releases ?? []), { id: 'lecture-12', when: '2026-12-10T10:00:00+01:00', type: null, title: 'Review', state: 'planned', source: null, dest: null, show_on_site: true, tbc: false }] },
  };
  it('renders a release with no deploy block as nothing staged, with no Release early', () => {
    const out = html(<ScheduleScreen {...props({ loaded: unstaged })} />);
    expect(out).toContain('<b>Session 12</b>: Review');
    expect(out).toMatch(/<li class="trow term" data-entry="lecture-12"><span class="k">release<\/span>/);
    expect(out).toContain('Nothing staged yet: this entry has no deploy block');
    expect(out).toContain('href="#schedule-lecture-12"');
    expect(out).not.toContain('Release early');
  });
  it('shows the nothing-staged page for a release with no source', () => {
    const t = text(<ReleaseScreen {...props({ loaded: unstaged, entry: 'lecture-12' })} />);
    expect(t).toContain('Nothing staged yet: this entry has no deploy block.');
    expect(t).toContain('Edit entry');
    expect(t).not.toContain('Release early');
  });
});

describe('operation outcome', () => {
  it('shows what the op produced in the details fold, preformatted', () => {
    const def = generateSyllabus({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 'course-materials-f2026');
    const outcome = { schema: 'dsl.outcome/1' as const, op: def.op, run_id: 7, actor: 'a', preview: true, conclusion: 'previewed' as const, summary: 'Preview: the session list.', reasons: [{ code: 'NO_SOLUTION_REGION', text: 'solution.py\nhas no region' }], details: ['main/solution.py', 'main/README.md'], block: '## Course sessions and readings\n- Session 1: Intro' };
    const out = html(<OutcomeView result={{ outcome, people: [], leaked: [] }} def={def} />);
    expect(out).toContain('<summary>Details</summary>');
    expect(out).toContain('<ul class="outcome-list"><li>main/solution.py</li><li>main/README.md</li></ul>');
    expect(out).toContain('<pre class="outcome-details">## Course sessions and readings\n- Session 1: Intro</pre>');
    expect(out).toContain('>Copy</button>');
    expect(out.indexOf('outcome-list')).toBeLessThan(out.indexOf('outcome-details'));
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
    expect(html(<ScreenBoundary><Boom /></ScreenBoundary>)).toContain('href="#cohort"');
  }));
  it('links Home when This week itself threw, so the route key changes', () => withBoundaries(() => {
    vi.stubGlobal('location', { hash: '#cohort' });
    try {
      const out = html(<ScreenBoundary><Boom /></ScreenBoundary>);
      expect(out).toContain('href="#">Back to Home');
      expect(out).not.toContain('href="#cohort"');
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
    const t = text(<StaffScreen {...props()} />);
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
    expect(t).toContain('Not ready for a new cohort');
    expect(t).toContain('Marking of Assignment 3 cannot start.');
    expect(t).toContain('assignment-3-f2026');
    expect(t).toContain('course-materials-f2026');
    expect(t).toContain('10% per day, up to 10 days');
    expect(t).toContain('Fall 2026');
  });
  it('reads grading_config.yml into the tiered form and marks the bad value', () => {
    const out = html(<TemplateScreen {...cp} entry="assignment-3" />);
    expect(out).toContain('value="Group project"');
    expect(out).toMatch(/value="group" checked/);
    expect(out).toContain('The file says “sometimes”. Choose on or off.');
    expect(out).toContain('value="proposal"');
    expect(out).toContain('<td>50</td>');
    expect(out).toContain('Advanced <span class="cnt changed">(1 changed)</span>');
    expect(out).toContain('/edit/solution/grading_config.yml');
    expect(out).toContain('Derive student version');
  });
});

describe('read only and the shell', () => {
  it('shows the read-only view for a course the user cannot change', () => {
    const t = text(<ReadonlyScreen course={{ ...course, write: false }} cohort={cohort} />);
    expect(t).toContain('Read only.');
    expect(t).toContain('No roster, no marks, no buttons.');
  });
  it('puts the switcher, nav with the problem count and header links in the frame', () => {
    const nav = html(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="week" problems={2} />);
    expect(nav).toContain('Machine Learning, Fall 2026');
    expect(nav).toContain('New cohort of Machine Learning');
    expect(nav).toContain('class="n-count"');
    expect(nav).toContain('aria-current="page"');
    expect(nav).toContain('2 problems');
    const top = html(<Topbar user={{ login: 'a', id: 1, name: 'A', email: null, avatar_url: '' }} course={{ ...course, write: false }} cohort={cohort} navOpen={false} onMenu={() => {}} />);
    expect(top).toContain('read only');
    expect(top).toContain(`https://${COHORT_ORG}.github.io`);
    expect(top).toContain('Cohort on GitHub');
    expect(top).toContain('Course on GitHub');
    const foot = text(<Footer course={course} cohort={cohort} />);
    expect(foot).toContain('Friedrichstraße 180');
    expect(foot).toContain('Part of the Hertie Data Science Lab.');
  });
});
