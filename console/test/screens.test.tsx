// One render per screen, with the contracts example as the status fixture.

import { options } from 'preact';
import { render } from 'preact-render-to-string';
import { describe, expect, it, vi } from 'vitest';
import { AppAuth } from '../src/auth/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import type { Files, TreeState } from '../src/model/files';
import { signal } from '@preact/signals';
import { EnvCtx, type Env } from '../src/env';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { AssignmentScreen, AssignmentsScreen } from '../src/screens/Assignments';
import { CohortScreen } from '../src/screens/Cohort';
import { CourseHeaderActions, CourseScreen, SETTLING_COLUMNS, TemplateScreen, courseItems, handoutWords, overviewHeights, semesterChip, splitColumns, stepLink, templateItems } from '../src/screens/Course';
import { ItemRow, readinessTabs, type SetupItem } from '../src/ui/SetupPanel';
import { DashboardTabs } from '../src/ui/DashboardTabs';
import { tier } from '../src/model/readiness';
import { HomeScreen, ReadonlyScreen, SignInScreen } from '../src/screens/Home';
import { InstructorsScreen, StudentsScreen } from '../src/screens/People';
import { ReleaseScreen, ScheduleScreen } from '../src/screens/Schedule';
import { parseSchedule, scheduleRows, termOf, weekGroups } from '../src/model/schedule';
import { OperationsScreen, SiteScreen, announcementFile, readAnnouncement } from '../src/screens/Site';
import type { CohortProps } from '../src/screens/types';
import { generateSyllabus } from '../src/ops/defs';
import { OutcomeView } from '../src/ops/Panel';
import { ScreenBoundary } from '../src/ui/boundary';
import { ProblemCards } from '../src/ui/bits';
import { Footer, Sidenav, Topbar } from '../src/ui/shell';
import example from './fixtures/status.example.json';
import { heldStatus } from './heldReleases';

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
const OUTCOME = JSON.stringify({ schema: 'dsl.outcome/1', op: 'release.entry', run_id: 4821, actor: 'a', preview: false, conclusion: 'done', summary: 'x', counts: { files: 7 }, reasons: [{ code: 'RELEASED', text: 'lectures/03 copied' }] });

const TREE = { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/05_trees_and_ensembles/slides.html', 'lectures/05_trees_and_ensembles/notes.pdf', 'lectures/03_regularisation/slides.html'] };

const files = new StaticFiles(
  {
    [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE,
    [`${COHORT_ORG}/semester-config/students.csv`]: STUDENTS,
    [`${COHORT_ORG}/semester-config/instructors.yml`]: PEOPLE,
    [`${COHORT_ORG}/semester-config/.system/outcomes/release.entry.json`]: OUTCOME,
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
    // Counted now or soon only (decision 0034): s5 on 8 Oct is more than 7 days off, so it waits.
    expect(out).toContain('1 problem');
    expect(out).toContain('Week 3 of 15');
    expect(out).toContain('you are a course admin');
    expect(out).toContain('Session 3: Trees');
    expect(out).toMatch(/cohort-card ro[^"]*" href="\?cohort=hertie-ids-f2026/);
    expect(out.indexOf('Machine Learning, Fall 2026')).toBeLessThan(out.indexOf('Intro to Data Science, Fall 2026'));
    const at = (semester: Partial<Status['semester']>) =>
      html(<HomeScreen courses={[course]} cohortStates={{ [COHORT_ORG]: { ...ready, status: { ...STATUS, semester: { ...STATUS.semester!, ...semester } } } as Loaded }} now={NOW} user={{ login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }} />);
    expect(at({ week: 0 })).toContain('<span class="cc-week">Starts Mon 7 Sep</span>');
    expect(at({ week: 0, start: null })).toContain('<span class="cc-week">Before week 1</span>');
    expect(at({ week: null, weeks: null, start: null, end: null })).toContain('<span class="cc-week"></span>');
  });
});

describe('S4 cohort overview', () => {
  const out = html(<CohortScreen {...props()} />);
  const t = text(<CohortScreen {...props()} />);
  it('leads with the header, term strip, problems and this week; the assignments are on their tab', () => {
    expect(html(<CohortScreen {...props()} />)).not.toContain('class="crumbs"'); // the semester banner replaces them
    expect(t).toContain('Dashboard ? What this semester has planned');
    expect(t).not.toContain('Archive 31 Jan 2027.');
    expect(t).not.toContain('Exam 22 Oct.');
    expect(t).not.toContain('Week 3 of 15');
    // The verdict replaces the setup sentence, and is the page's one problem count (no badge in the head).
    expect(t).toContain('Needs fixing: 1 problem in the next 7 days 1 coming up');
    expect(t).not.toContain('Setup complete.');
    expect(out).not.toContain('class="probs');
    expect(out).toContain('class="term-strip"');
    expect((out.match(/class="wk[ "]/g) ?? []).length).toBe(15);
    expect(out).toContain('wk now');
    // A status with no horizon reads the next 7 days (contract C, amended): s5 (8 Oct) is coming up, not counted.
    expect(out).not.toMatch(/aria-label="Week 5, from 5 Oct[^"]*; 1 problem"/);
    expect(out).toContain('<div class="ptab ptab-coming" role="tabpanel" id="dash-panel-coming" aria-labelledby="dash-tab-coming" hidden>');
    // Only the current tab's body renders: s5 waits in Coming up, unrendered until picked.
    expect(t).not.toContain('Session 5 cites folder lectures/05_trees');
    expect(out).toContain('href="#template-assignment-3-f2026"');
    expect(t).toContain('(course)');
    expect(out).not.toContain('<h2>Assignments</h2>');
    expect(out).not.toContain('All assignments');
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
    // Its template is not in the course's status: no state words are invented, only the link.
    expect(out).not.toContain('Assignment template ready.');
    expect(out).toContain('Assignment template settings');
  });
  it('says when an assignment is not in the status', () => {
    expect(text(<AssignmentScreen {...props({ entry: 'assignment-9' })} />)).toContain('No assignment called assignment-9');
  });
});

describe('S6 schedule and S11 release', () => {
  it('lists an undated (`event_datetime: tbc`) release last, as No date yet, on the Schedule and the Dashboard', () => {
    const tbc = { ...STATUS.releases![0], id: 'lab9', kind: 'lab', number: 9, title: 'Attention', when: null, state: 'planned' as const, tbc: true };
    const status: Status = { ...STATUS, releases: [...STATUS.releases!, tbc] };
    const loaded: Loaded = { ...ready, status };
    const t = text(<ScheduleScreen {...props({ loaded })} />);
    expect(t).toMatch(/No date yet Lab 9 : Attention/);
    expect(t.indexOf('Lab 9')).toBeGreaterThan(t.indexOf('Lecture 5'));
    // The Dashboard's All weeks and timeline group it the same way.
    const rows = scheduleRows(status, parseSchedule(SCHEDULE), NOW, 'Europe/Berlin');
    const groups = weekGroups(rows, (r) => r.when, termOf(status, parseSchedule(SCHEDULE), '2026-09-23'), 'Europe/Berlin', 'all');
    expect(groups.at(-1)).toMatchObject({ label: 'No date yet', rows: [{ entry: 'lab9' }] });
  });
  it('shows Type, Details as markdown and State, with events from schedule.yml', () => {
    const out = html(<ScheduleScreen {...props()} />);
    expect(out).toContain('<span>Details</span>');
    expect(out).toContain('<i>random forests</i>');
    // s5 is beyond the horizon: not ready yet, dotted, with its Fix.
    expect(out).toContain('<span class="st-chip later">not ready yet</span><a class="textlink" href="#schedule-s5">Fix</a>');
    expect(out).not.toContain('st-chip skip');
    expect(out).toContain('<b>Exam</b>: Midterm');
    expect(out).toContain('<b>Lecture 5</b>: Trees and ensembles');
    expect(out).toContain('<b>Assignment 2</b>: Regression');
    expect(out).toContain('today-line');
  });
  it('opens the entry a Fix link points to', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toContain('trow lec later current');
    expect(out).toContain('class="entry"');
    expect(out).toMatch(/<option value="course-materials-f2026" selected>/);
    expect(out).toContain('id="e-d0-folder" list="folders-0" value="lectures/05_trees"');
    expect(out).toContain('Not found. The release will be skipped until the folder exists.');
    expect(out).toContain('Use the one that exists: <button');
    expect(out).toContain('lectures/05_trees_and_ensembles');
    expect(out).toContain('schedule.yml#L5');
  });
  it('offers the kinds of entry, Assignment among them, with a ? saying what each does', () => {
    const out = html(<ScheduleScreen {...props({ entry: 'new' })} />);
    expect([...out.matchAll(/<button type="button" class="trow [a-z]+"[^>]*>([^<]+)<\/button>/g)].map((m) => m[1])).toEqual(['Lecture', 'Lab', 'Readings', 'Assignment', 'Exam', 'Event']);
    expect(out).toContain('aria-label="About the kinds of entry"');
    const t = text(<ScheduleScreen {...props({ entry: 'new' })} />);
    for (const k of ['Lecture, Lab, Readings : a session’s materials.', 'Assignment : work students hand in.', 'Exam : a dated row', 'Event : any other dated row', 'Archive (under Archive settings)']) expect(t).toContain(k);
    expect(t).toContain('none when work is handed in outside GitHub');
    expect(out).toContain('docs/07-schedule-releases.md');
    expect(t).not.toContain('A hand out is an assignment entry');
  });
  it('words each row’s kind and an assignment’s due row exactly as the student site does', () => {
    const out = html(<ScheduleScreen {...props()} />);
    const words = [...out.matchAll(/<span class="k">([^<]+)<\/span>/g)].map((m) => m[1]);
    expect(words).toEqual(['Semester', 'Assignment out', 'Assignment due', 'Lecture', 'Exam', 'Event', 'Semester', 'Archive']);
    expect(out).toContain('Submit via <code>assignment-2-&lt;your-handle></code>');
    const shaped = (submit_shape: string, extra: Partial<NonNullable<Status['assignments']>[number]> = {}): Loaded => ({
      kind: 'ready', sha: 's', stale: [],
      status: { ...STATUS, assignments: (STATUS.assignments ?? []).map((a) => ({ ...a, submit_shape, ...extra })) },
    });
    const external = text(<ScheduleScreen {...props({ loaded: shaped('external') })} />);
    expect(external).toContain('Handed in outside GitHub - see the brief');
    expect(external).not.toContain('Submit via');
    // Once handed out, the address the assignment names is linked by its host.
    const linked = html(<ScheduleScreen {...props({ loaded: shaped('external', { settings: { submit_url: { value: 'https://moodle.example.org/mod/assign/1', source: 'assignment' } } }) })} />);
    expect(linked).toContain('Handed in outside GitHub - <a href="https://moodle.example.org/mod/assign/1" target="_blank" rel="noopener">moodle.example.org</a>');
    expect(html(<ScheduleScreen {...props({ loaded: shaped('shared-dropbox-repo') })} />)).toContain('Submit via <code>assignment-2-submissions</code> (&lt;your-handle>/ folder)');
    expect(html(<ScheduleScreen {...props({ loaded: shaped('assignment-repo-public', { teams: 4 }) })} />)).toContain('Submit via <code>assignment-2-&lt;your-team></code> (public)');
    expect(html(<ScheduleScreen {...props({ loaded: shaped('assignment-repo-student-choice') })} />)).toContain(' (yours to publish)');
  });

  it('mutes an entry the student site does not show, with a Not shown badge that says why', () => {
    const HIDDEN = SCHEDULE.replace('    details: Room 2.61, closed book.\n', '    details: Room 2.61, closed book.\n    show_on_site: false\n');
    const hiddenFiles = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: HIDDEN }, {}, TREE);
    const out = html(<ScheduleScreen {...props({ files: hiddenFiles })} />);
    const rows = [...out.matchAll(/<li class="trow ([^"]+)" data-entry="([^"]+)">(.*?)<\/li>/g)];
    const midterm = rows.find((m) => m[2] === 'midterm')!;
    expect(midterm[1]).toContain('off-site');
    expect(midterm[3]).toContain('<span class="prop">Not shown</span>');
    expect(midterm[3]).toContain('This entry is not displayed on the student site or in the student console. The engine still acts on it (a hidden release still ships at its time). Edit the entry to show it.');
    for (const m of rows.filter((x) => x[2] !== 'midterm')) {
      expect(m[1]).not.toContain('off-site');
      expect(m[3]).not.toContain('Not shown');
    }
  });

  it('names both ends of a line From and To, explains what to release, and offers one more line', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    for (const l of ['From: repo', 'From: path', 'To: repo', 'To: path', 'Release at']) expect(out).toContain(`>${l}`);
    const t = text(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(t).toContain('Name the repo and path where the files will be, even if they are not written yet. At release time the engine copies whatever is at that path.');
    expect(t).toContain('Ships the files at this moment, before or after the session. Leave blank to ship when the session starts.');
    // One line, its own time blank: the default is the entry's.
    expect(out.match(/class="deploy"/g)).toHaveLength(1);
    expect(out).toContain('id="e-d0-at" placeholder="2026-10-08" value/>');
    expect(out).toContain('>Add another</button>');
  });
  it('never says a folder is missing when the repo could not be read, or was listed only in part', () => {
    const withTree = (tree: TreeState): Files => Object.assign(Object.create(files), { tree: () => tree });
    const failed = html(<ScheduleScreen {...props({ entry: 's5', files: withTree({ kind: 'error', message: 'HTTP 502' }) })} />);
    expect(failed).toContain('Could not read course-materials-f2026 to check the folder; it is tried again shortly.');
    expect(failed).not.toContain('was not found in the course');
    const part = html(<ScheduleScreen {...props({ entry: 's5', files: withTree({ kind: 'ready', paths: [{ path: 'lectures', dir: true }], truncated: true }) })} />);
    expect(part).toContain('course-materials-f2026 is too large to check the folder here.');
    expect(part).not.toContain('Not found. The release will be skipped');
    // The repo list failed: the From repo select says so rather than offering no other repos.
    expect(failed).toContain('Could not list the course’s other repos; it is tried again shortly.');
  });
  it('pre-selects the inferred kind: the engine’s for a saved entry, else from where the copy lands and the repo’s own aliases', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toContain('<option value selected>Lecture (inferred)</option>');
    const withKind = (kind: string | null): Loaded => ({ kind: 'ready', sha: 's', stale: [], status: { ...STATUS, releases: (STATUS.releases ?? []).flatMap((r) => (r.id !== 's5' ? [r] : kind === null ? [] : [{ ...r, kind }])) } });
    expect(html(<ScheduleScreen {...props({ entry: 's5', loaded: withKind('lab') })} />)).toContain('<option value selected>Lab (inferred)</option>');
    const aliased = new StaticFiles({ ...Object.fromEntries(['schedule.yml'].map((f) => [`${COHORT_ORG}/semester-config/${f}`, SCHEDULE])), [`${COURSE_ORG}/course-materials-f2026/materials.yml`]: 'kinds:\n  lectures: lab\n' }, {}, TREE);
    expect(html(<ScheduleScreen {...props({ entry: 's5', files: aliased, loaded: withKind(null) })} />)).toContain('<option value selected>Lab (inferred)</option>');
  });
  it('lists the materials repos and the Other repos to release from', () => {
    const listed = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE }, {}, TREE, { [COURSE_ORG]: [{ name: '.github' }, { name: 'course-materials-f2026' }, { name: 'lecture-code-f2026' }, { name: 'assignment-3-f2026' }] });
    const out = html(<ScheduleScreen {...props({ entry: 's5', files: listed })} />);
    expect(out).toMatch(/<optgroup label="Course materials repos"><option value="course-materials-f2026" selected>/);
    expect(out).toContain('<optgroup label="Other repos"><option value="lecture-code-f2026">lecture-code-f2026</option></optgroup>');
  });
  it('renders a release with its source, destination and problem', () => {
    const t = text(<ReleaseScreen {...props({ entry: 's5' })} />);
    expect(t).toContain('Lecture 5 : Trees and ensembles');
    // Beyond the horizon: the chip says not ready yet, dotted, not red.
    expect(html(<ReleaseScreen {...props({ entry: 's5' })} />)).toContain('<span class="chip suggest">not ready yet</span>');
    expect(t).toContain(`${COURSE_ORG}/course-materials-f2026/lectures/05_trees`);
    expect(t).toContain(`${COHORT_ORG}/materials/lectures/05_trees`);
    // Said once: the chip and the problem card; the hint explains the page, the actions do not repeat the fix.
    expect(t).toContain('Fix it in the schedule entry');
    expect(t).not.toContain('Fix the folder');
  });
  const unstaged: Loaded = {
    kind: 'ready', sha: 's', stale: [],
    status: { ...STATUS, releases: [...(STATUS.releases ?? []), { id: 'lecture-12', when: '2026-12-10T10:00:00+01:00', kind: 'lecture', title: 'Review', state: 'planned', source: null, dest: null, show_on_site: true, tbc: false }] },
  };
  it('renders a release with no deploy block as nothing to release, with no Release early', () => {
    const out = html(<ScheduleScreen {...props({ loaded: unstaged })} />);
    expect(out).toContain('<b>Lecture 12</b>: Review');
    expect(out).toMatch(/<li class="trow lec" data-entry="lecture-12"><span class="k">Lecture<\/span>/);
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
    const def = generateSyllabus({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 'course-materials-f2026', 'SYLLABUS.md');
    const outcome = { schema: 'dsl.outcome/1' as const, op: def.op, run_id: 7, actor: 'a', preview: true, conclusion: 'previewed' as const, summary: 'Preview: the weekly plan.', reasons: [{ code: 'NO_SOLUTION_REGION', text: 'solution.py\nhas no region' }], details: ['main/solution.py', 'main/README.md'], block: '## Course sessions and readings\n- Session 1: Intro' };
    const out = html(<OutcomeView result={{ outcome }} def={def} />);
    expect(out).toContain('<summary>Details</summary>');
    expect(out).toContain('<ul class="outcome-list"><li>main/solution.py</li><li>main/README.md</li></ul>');
    expect(out).toContain('<pre class="outcome-details">## Course sessions and readings\n- Session 1: Intro</pre>');
    expect(out).toContain('>Copy</button>');
    // The weekly plan is what a preview is for (finding 31): above the fold, not in it.
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

describe('S8 students, a fault on a row after a blank line', () => {
  it('marks the row the engine names, by its line in the file', () => {
    const csv = 'hertie_email,name,role\nanna@x.org,Anna Adams,enrolled\n\nben@x.org,Ben Baker,enroled\n';
    const fault = { id: 'role', scope: 'semester' as const, stage: 'K5', text: 'Row 4 has a role nobody can act on.', stops: '', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'students.csv', screen: 'students', line: 4 } };
    const out = html(<StudentsScreen {...props({ files: new StaticFiles({ [`${COHORT_ORG}/semester-config/students.csv`]: csv }), loaded: { ...ready, status: { ...STATUS, problems: [fault] } } as Loaded })} />);
    const row = (line: number) => out.match(new RegExp(`<tr class(?:="([^"]*)")? id="line-${line}">`))?.[1] ?? '';
    expect(out).toContain('id="line-2"');
    expect(row(2)).toBe('');
    expect(row(4)).toBe('fault');
    expect(out).not.toContain('id="line-3"');
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
  it('fills the site’s Liquid in the home text preview with the semester’s values, leaving an unknown tag visible', () => {
    const liquid = new StaticFiles({
      [`${COHORT_ORG}/${COHORT_ORG}.github.io/index.md`]: '---\nlayout: home\n---\nWelcome to **{{ site.course_name }}**, {{ site.course_semester }}, in [{{site.github_org}}]({{ site.dsl_org_url }}). {{ site.mystery }}\n',
      [`${COHORT_ORG}/${COHORT_ORG}.github.io/_config.yml`]: 'course_name: "Course Name (Code)"\ndsl_org_url: "https://github.com/hertie-data-science-lab"\n',
    });
    const out = html(<SiteScreen {...props({ files: liquid })} />);
    expect(out).toContain(`Welcome to <b>Machine Learning</b>, Fall 2026, in <a href="https://github.com/hertie-data-science-lab" target="_blank" rel="noopener">${COHORT_ORG}</a>. {{ site.mystery }}`);
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
    expect(t).toContain('Needs fixing: 1 problem');
    expect(t).not.toMatch(/\b0 (setup steps|problems)/);
    expect(t).toContain('Students get only what a semester releases or hands out');
    expect(t).toContain('Marking of Assignment 3 cannot start.');
    expect(t).toContain('assignment-3-f2026');
    expect(t).toContain('course-materials-f2026');
    expect(t).toContain('10 days at 10% a day, this course');
    expect(t).toContain('up to 5, ');
    expect(t).not.toContain('this course’s default');
    expect(t).toContain('Fall 2026');
    // A semester's problems are on its own Dashboard: no roll-up here (decision 0034).
    expect(t).not.toContain('Session 5 cites');
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
  /** The course panel with one tab picked, as the Dashboard builds it: only that tab's body renders. */
  const panel = (c: typeof base, key: string, write = true) => {
    const items = courseItems(c, tier({ ...STATUS, course: c }, 'course'), null, NOW);
    return html(<DashboardTabs selected={key} tabs={readinessTabs({ items, scope: 'course', onCircle: write ? () => {} : undefined })} />);
  };
  it('puts the verdict under the page note: a red ! while a problem stands, a tick once ready', () => {
    const out = html(<CourseScreen {...cp} />);
    expect(out).toContain('<p class="verdict bad"><span class="ex" aria-hidden="true">!</span><button type="button" class="verdict-btn" title="Show the problems">Needs fixing: 1 problem</button>');
    expect(out).not.toContain('class="lede"');
    expect(out.indexOf('class="page-note"')).toBeLessThan(out.indexOf('class="verdict'));
    // Above the read-only banner too: the verdict sits directly under the note.
    const ro = html(<CourseScreen {...cp} course={{ ...course, write: false }} />);
    expect(ro.indexOf('class="verdict')).toBeLessThan(ro.indexOf('class="ro-banner"'));
    const fine: Loaded = { kind: 'ready', status: { ...STATUS, problems: [], course: { ...base, stages: { ...base.stages, C5: 'done' }, verdict: { state: 'ready', problems: 0, missing: null, suggestions: 2 } } }, sha: 's', stale: [] };
    const ok = html(<CourseScreen {...cp} loaded={fine} />);
    expect(ok).toContain('<p class="verdict ok"><svg');
    // C6 and the weekly plan are open suggestions: the only tag.
    expect(text(<CourseScreen {...cp} loaded={fine} />)).toContain('Ready for a new semester 2 suggestions');
    // A status with no verdict (the previous engine's, one tick at most): no verdict line.
    const { verdict: _v, ...noVerdict } = base;
    expect(html(<CourseScreen {...cp} loaded={{ ...fine, status: { ...STATUS, course: noVerdict } }} />)).not.toContain('class="verdict');
  });
  it('opens on the Setup tab while the course is not ready, its verdict pointing there too', () => {
    const notReady: Loaded = { kind: 'ready', status: { ...STATUS, problems: [], course: { ...base, stages: { ...base.stages, C3: 'todo', C5: 'done' }, verdict: { state: 'not_ready', problems: 0, missing: 'Course details have no description yet.', suggestions: 0 } } }, sha: 's', stale: [] };
    const out = html(<CourseScreen {...cp} loaded={notReady} />);
    expect(out).toContain('data-key="setup" aria-selected="true"');
    expect(out).toContain('data-key="problems" aria-selected="false"');
  });
  it('opens on the Problems tab, then Suggestions, Set aside and Setup', () => {
    const out = html(<CourseScreen {...cp} />);
    expect(out).toContain('<section class="panel section dash-tabs" id="dash-problems" tabindex="-1"><div class="ptabs"><div class="ptabs-list" role="tablist">');
    expect(out).toContain('aria-selected="true" aria-controls="dash-panel-problems" style="order:0;">Problems<span class="n bad">1</span>');
    expect(out).toContain('<span class="spacer" aria-hidden="true" style="order:6;"></span><button type="button" role="tab" id="dash-tab-setup" data-key="setup" aria-selected="false" aria-controls="dash-panel-setup" style="order:8;">Setup<span class="n">4 of 5</span>');
    expect(out).not.toContain('Coming up');
    // The open tab's `?` sits outside the tablist (only tabs belong in one), ordered right after that tab.
    expect(out).toContain('</div><span class="ptabs-hint" style="order:1;"><span class="hint-wrap"><button class="hint-btn" type="button" aria-label="About Problems"');
    expect(out.slice(out.indexOf('role="tablist"'), out.indexOf('class="ptabs-hint"'))).not.toContain('hint-wrap');
  });
  it('lists suggestions on their tab and a needed to-do only on its repo’s row', () => {
    const sug = panel(base, 'suggestions');
    // A suggestion's circle is a button that asks whether to set it aside (decision 0032); the syllabus and its plan are one row.
    expect(sug).toContain('<li class="open suggested"><button class="s-mark s-circle" type="button" title="Set aside…" aria-label="Set aside Syllabus…" aria-haspopup="dialog"></button>');
    expect(sug).toContain('Syllabus<span class="s-need slug">course-materials-f2026</span><span class="s-opt">optional</span>');
    expect(sug).toContain('One file, SYLLABUS.md, two parts');
    expect(sug).toContain('<span class="s-why">The weekly plan (written from the schedule) is not in SYLLABUS.md yet. <a class="textlink" href="#materials-course-materials-f2026" aria-label="Open course-materials-f2026 settings">Open settings</a></span>');
    // What a tab is for is in the `?` after its label, so the body starts with the rows.
    expect(sug).toMatch(/aria-label="About Suggestions"[^]*?<span class="hint-pop"[^>]*>Optional: the course works without these\. If you would rather skip one, click the circle before it to set it aside\.<\/span>/);
    expect(sug).toContain('<div class="ptab ptab-suggestions" role="tabpanel" id="dash-panel-suggestions" aria-labelledby="dash-tab-suggestions"><ul class="setup">');
    expect(panel(base, 'setup')).toMatch(/aria-label="About Setup"[^]*?<span class="hint-pop"[^>]*>One-time setup: what the course needs before a semester can start\. Done stays done\.<\/span>/);
    // Only the picked tab's body renders.
    expect(sug).not.toContain('One-time setup');
    const tpl: Loaded = { kind: 'ready', status: { ...STATUS, problems: [], course: { ...base, templates: [{ repo: 'a', state: 'todo' }], todo: [{ id: 'template:a:brief', kind: 'template', repo: 'a', text: 'The brief (README.md) is not written yet.', need: 'needed' }] } }, sha: 's', stale: [] };
    const t = html(<CourseScreen {...cp} loaded={tpl} />);
    expect(t).not.toContain('Brief written');
    expect(t).toContain('<span class="chip notready">Not ready</span>');
    expect(text(<CourseScreen {...cp} loaded={tpl} />)).toContain('The brief (README.md) is not written yet. a');
    // The repo row shows its state only: its suggestions are on the Suggestions tab.
    expect(t).not.toContain('chip suggest');
  });
  it('ticks Setup once every needed step is done, and says No problems on the course', () => {
    const done = { ...base, stages: { ...base.stages, C5: 'done' } } as typeof base;
    const out = html(<CourseScreen {...cp} loaded={{ kind: 'ready', status: { ...STATUS, problems: [], course: done }, sha: 's', stale: [] }} />);
    expect(out).toContain('aria-controls="dash-panel-setup" style="order:8;">Setup<span class="n ok" aria-label="done">');
    expect(out).toContain('aria-controls="dash-panel-problems" style="order:0;">Problems<span class="n ok" aria-label="done">');
    expect(out).toContain('<div class="no-problems"><svg');
    expect(out).toContain('No problems on the course.');
  });
  it('shows every course fact in Course details, the institution’s in grey', () => {
    const t = text(<CourseScreen {...cp} />);
    expect(t).toContain('Description ? One paragraph about the course, shown on the public website. In dsl-course.yml. Not set');
    expect(t).toMatch(/Contact \? Who students and the lab write to about the course\. In dsl-course\.yml; the institution’s contact when unset\. \S+@\S+, from the institution/);
    expect(t).toContain(', from the institution');
    expect(t).toContain('In dsl-course.yml. a-example');
    // Every fact carries the small ?.
    expect((html(<CourseScreen {...cp} />).match(/<dt>[^<]*<span class="hint-wrap small">/g) ?? []).length).toBe(8);
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
    expect(t).toContain('Optional: an open version of this course and its materials, available to anyone on the internet. Beta: leave this off for now - work in progress.');
    expect(t).toContain('Edit website details');
    expect(t).not.toContain('Public website settings');
    expect(out).toContain('<button class="btn small outline" type="button">Republish website</button>');
    expect(html(<CourseScreen {...cp} />)).toContain('<button class="btn small outline" type="button">Publish website</button>');
  });
  it('keeps only New semester in the head; the status line, Refresh and the course on GitHub sit under it', () => {
    const out = html(<CourseScreen {...cp} />);
    // The head is the course banner's right side (decision 0031 rule 11); the screen starts under it with its title, Dashboard.
    expect(out).toContain('<div class="page-head"><div><h2 class="h1">Dashboard <span class="hint-wrap">');
    expect(out).not.toContain('<h1');
    const head = html(<CourseHeaderActions course={course} />);
    // Always the black primary button, ready or not.
    expect(head).toContain(`<a class="btn" href="?course=${COURSE_ORG}#new-semester-1">New semester</a>`);
    expect(head).not.toContain('Publish');
    expect(head).not.toContain('Refresh');
    expect(head).not.toContain('on GitHub');
    const sub = out.slice(out.indexOf('class="actions sub-actions"'), out.indexOf('class="page-note"'));
    // The course's own status is absent here (a semester's copy fills the page), so no age.
    expect(text(<CourseScreen {...cp} />)).toContain('Refresh ? Reads the course and its semesters from GitHub again');
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
  it('lays the dashboard out as the tabbed panel, then two columns: materials and templates head the left, Semesters the right', () => {
    const out = html(<CourseScreen {...cp} />);
    expect((out.match(/class="grid-2/g) ?? []).length).toBe(1);
    expect(out.indexOf('id="dash-problems"')).toBeLessThan(out.indexOf('class="grid-2 cols"'));
    expect(out).not.toContain('<h2>Public website');
    const cols = out.slice(out.indexOf('class="grid-2 cols"'));
    const second = cols.indexOf('<div class="stack">', 1 + cols.indexOf('<div class="stack">'));
    const [left, right] = [cols.slice(0, second), cols.slice(second)];
    expect(left.indexOf('<section class="panel section" id="sec-materials"')).toBe(left.indexOf('<section'));
    expect(right.indexOf('<h2>Semesters</h2>')).toBeGreaterThan(-1);
    for (const h of ['<h2>Semesters</h2>', '<h2>Course details</h2>', '<h2>Recent activity</h2>', '<h2>Course materials</h2>']) expect(cols).toContain(h);
    // Course materials and Assignment templates sit one after the other.
    expect(left.indexOf('id="sec-templates"')).toBeGreaterThan(left.indexOf('id="sec-materials"'));
    expect(left.slice(left.indexOf('id="sec-materials"'), left.indexOf('id="sec-templates"')).match(/<section/g)).toHaveLength(1);
  });
  it('keeps the placement fixed while statuses load, and splits once every one has arrived', () => {
    const columns = (out: string) => {
      const cols = out.slice(out.indexOf('class="grid-2 cols"'));
      const second = cols.indexOf('<div class="stack">', 1 + cols.indexOf('<div class="stack">'));
      const ids = (h: string) => ['id="sec-materials"', '<h2>Semesters</h2>', '<h2>Course details</h2>', '<h2>Recent activity</h2>'].filter((x) => h.includes(x));
      return [ids(cols.slice(0, second)), ids(cols.slice(second))];
    };
    const settling = ['id="sec-materials"', '<h2>Course details</h2>'];
    const loading = { ...cp, loaded: { kind: 'loading' } as Loaded };
    expect(columns(html(<CourseScreen {...loading} />))[0]).toEqual(settling);
    expect(columns(html(<CourseScreen {...cp} cohortStates={{ [COHORT_ORG]: { kind: 'loading' } }} />))[0]).toEqual(settling);
    // A semester not yet asked for counts as loading too.
    expect(columns(html(<CourseScreen {...cp} cohortStates={{}} />))[0]).toEqual(settling);
    expect(SETTLING_COLUMNS[0]).toEqual(['handouts', 'details']);
    // Once all have arrived the split is the estimate's, and the same however late each came.
    const settled = columns(html(<CourseScreen {...cp} />));
    expect(columns(html(<CourseScreen {...cp} cohortStates={{ ...cp.cohortStates }} />))).toEqual(settled);
  });
  it('splits the panels so the two columns come out about even, materials and templates as one block', () => {
    const b = (key: string, h: number) => ({ key, h });
    // Heights whose sums are all distinct: the one even split.
    expect(splitColumns([b('handouts', 11), b('semesters', 10), b('details', 2), b('activity', 1)])).toEqual([['handouts', 'activity'], ['semesters', 'details']]);
    // A long first block pulls everything else right; order within a column is kept.
    expect(splitColumns([b('handouts', 40), b('semesters', 6), b('details', 10), b('activity', 6)])).toEqual([['handouts'], ['semesters', 'details', 'activity']]);
    const hs = overviewHeights({ course: base, semesters: 2, description: '', activity: 1 });
    expect(hs.map((x) => x.key)).toEqual(['handouts', 'semesters', 'details', 'activity']);
    // More semesters make that panel taller.
    expect(overviewHeights({ course: base, semesters: 5, description: '', activity: 1 })[1].h).toBeGreaterThan(hs[1].h);
  });
  const rowsOf = (items: SetupItem[]) => html(<ul class="setup">{items.map((it) => <ItemRow it={it} />)}</ul>);
  it('renders the setup checklist from stage_why: ticks, the why and one link each', () => {
    const items = courseItems(withWhy, [], []);
    const out = rowsOf(items);
    expect((out.match(/class="done"/g) ?? []).length).toBe(2);
    // C3 is needed and open: a hollow circle with its why and link. C4-C6 are suggestions (decision 0034).
    expect(out).toContain('<li class="open"><span class="s-mark" aria-hidden="true"></span><span class="s-name">Course details filled in<span class="sr">: Not done yet</span>');
    expect(out).toContain('Course details have no description yet.');
    expect(out).toContain('href="#details"');
    expect(out).toContain(`href="?course=${COURSE_ORG}#new-materials"`);
    expect(out).toContain('href="#website"');
    // A waiting step: a dash and what it waits for, no link.
    expect(out).toContain('<li class="waiting suggested"><span class="s-mark" aria-hidden="true">–</span><span class="s-name">Assignment template<span class="s-opt">optional</span>');
    expect(out).toContain('Waiting for the course to be set up.');
    expect(out).not.toContain('required');
    // Done lines carry no link and no sentence.
    expect(out).not.toContain('Open on GitHub');
  });
  it('gives a step with a problem a red ! and its Fix, and every open step one link', () => {
    const out = rowsOf(courseItems(base, [{ p: STATUS.problems![1], b: 'now' }], []));
    expect(out).toContain('<li class="problem"><span class="s-mark problem" aria-hidden="true">!</span><span class="s-name">Assignment template<span class="sr">: Has a problem</span>');
    expect(out).toContain('<span class="s-fix"><a class="btn small" href="#template-assignment-3-f2026">Fix</a></span>');
    // A problem beyond the horizon flags no stage (the engine's `_stage_states`): the step is open, not red.
    expect(rowsOf(courseItems({ ...base, stages: { ...base.stages, C5: 'todo' } }, [{ p: STATUS.problems![1], b: 'later' }], []))).not.toContain('class="problem"');
    expect(stepLink('C6', withWhy)).toEqual({ href: '#website', label: 'Set up the public website' });
    expect(stepLink('C4', { ...withWhy, materials: [{ repo: 'm', state: 'todo' }] }).label).toBe('Open course materials');
    expect(stepLink('C5', { ...withWhy, templates: [{ repo: 't', state: 'todo' }] }).label).toBe('Open templates');
  });
  it('reads a semester as live, ended but not archived, or archived', () => {
    const sem = STATUS.semester!;
    expect(text(<>{semesterChip(sem)}</>).trim()).toBe('Live');
    expect(text(<>{semesterChip({ ...sem, ended: true })}</>).trim()).toBe('Ended, not archived');
    expect(text(<>{semesterChip({ ...sem, live: false, ended: false })}</>).trim()).toBe('Archived');
  });
  it('heads the template settings with its state and a needed checklist: never Ready while the brief is not written', () => {
    const todo = { id: 'template:assignment-3-f2026:brief', kind: 'template' as const, check: 'brief', repo: 'assignment-3-f2026', text: 'The brief (README.md) is not written yet.', need: 'needed' as const };
    const st: Loaded = { kind: 'ready', sha: 's', stale: [], status: { ...STATUS, problems: [], course: { ...STATUS.course!, templates: [{ repo: 'assignment-3-f2026', state: 'todo' }], todo: [todo] } } };
    const out = html(<TemplateScreen {...cp} loaded={st} entry="assignment-3-f2026" />);
    expect(out).toContain('<span class="chip notready">Not ready</span>');
    expect(out).not.toContain('<span class="chip ok">Ready</span>');
    expect(out).toContain('<p class="sub-h">Needed</p>');
    expect(out).toContain('<li class="open"><span class="s-mark" aria-hidden="true"></span><span class="s-name">Brief written');
  });
  it('shows a template that lists no formats as No starter file, as it runs, with no default claimed', () => {
    const bare = new StaticFiles({ [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: GRADING.replace('formats: [ipynb]\n', '') });
    const out = html(<TemplateScreen {...cp} files={bare} entry="assignment-3-f2026" />);
    expect(out).toMatch(/id="g-fmt-fmt-none" checked/);
    expect(out).not.toMatch(/id="g-fmt-fmt-ipynb" checked/);
    expect(out).not.toContain('institution default');
    // The starter choice stays, greyed and disabled, with why.
    expect(out).toContain('<div class="starter-off">');
    expect(out).toMatch(/value="handwritten" checked disabled/);
    expect(out).toContain('No starter file: students get none, so there is nothing to derive or write.');
    expect(out).toContain('Starter files');
  });
  it('greys the starter controls of a template handed in outside GitHub, as it hands out none', () => {
    const ext = new StaticFiles({ [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: GRADING.replace('submit_via: assignment_repo', 'submit_via: external') });
    const out = html(<TemplateScreen {...cp} files={ext} entry="assignment-3-f2026" />);
    expect(out).toContain('<div class="starter-off">');
    expect(out).not.toContain('Derive student version');
  });
  it('reads grading_config.yml into the tiered form and marks the bad value', () => {
    const out = html(<TemplateScreen {...cp} entry="assignment-3-f2026" />);
    expect(out).toContain('<h2 class="h1">Group project <span class="hint-wrap">');
    // The crumbs are the course banner's (decision 0031 rule 11), not the screen's.
    expect(out).not.toContain('class="crumbs"');
    expect(out).toContain('value="Group project"');
    expect(out).toMatch(/value="group" checked/);
    expect(out).toContain('The file says “sometimes”. Choose on or off.');
    expect(out).toContain('value="proposal"');
    expect(out).toContain('<td>50</td>');
    expect(out).toContain('Advanced <span class="cnt changed">(1 changed)</span>');
    expect(out).toContain('/edit/solution/grading_config.yml');
    // No `starter:` and no tests: written by hand, so there is nothing to derive.
    expect(out).toMatch(/value="handwritten" checked/);
    // No Student version panel for a hand-written starter (decision 0028 rule 4).
    expect(out).not.toContain('<h3>Student version');
    expect(out).not.toContain('Derive student version');
  });
  const derivedFiles = () => new StaticFiles({ [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: `${GRADING}starter: derived\n` }, {}, TREE);
  it('offers Derive only for a derived starter, and the engine’s reading stands in for a missing key', () => {
    const out = html(<TemplateScreen {...cp} files={derivedFiles()} entry="assignment-3-f2026" />);
    expect(out).toMatch(/value="derived" checked/);
    expect(out).toContain('Derive student version');
    expect(out).toContain('<h3>Student version');
    const st = { ...STATUS, course: { ...STATUS.course!, templates: [{ repo: 'assignment-3-f2026', state: 'ready', starter: 'derived' as const }] } };
    const read = html(<TemplateScreen {...cp} loaded={{ ...ready, status: st } as Loaded} entry="assignment-3-f2026" />);
    expect(read).toMatch(/value="derived" checked/);
  });
  it('explains marking, points per question and the student version with a ?', () => {
    const out = html(<TemplateScreen {...cp} files={derivedFiles()} entry="assignment-3-f2026" />);
    expect(out).toMatch(/<h3>How it is marked <span class="hint-wrap"><button[^>]*aria-label="About marking"/);
    expect(out).toMatch(/Points per question <span class="hint-wrap"><button[^>]*aria-label="About points per question"/);
    expect(out).toMatch(/<h3>Student version <span class="hint-wrap"><button[^>]*aria-label="About the student version"/);
    expect(out).toContain('Write the solution once and mark the answers; Derive builds the starter students get by blanking them. Run it after every change to the solution.');
    expect(out).toContain('assignment-starter.md');
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

describe('a template’s checklist', () => {
  it('lists the starter only where the engine says one is handed out', () => {
    const todo = [{ id: 'template:a1:starter', kind: 'template' as const, check: 'starter', repo: 'a1', text: 'Derive has not been run yet.', need: 'needed' as const }];
    const ids = (tpl: { starter?: 'derived' | 'handwritten' | 'none' }) => templateItems('a1', todo, tpl, [], 0).map((i) => `${i.id}:${i.state}`);
    expect(ids({ starter: 'derived' })).toEqual(['brief:done', 'starter:open']);
    expect(ids({})).toEqual(['brief:done', 'starter:open']);
    // `formats: [none]` or `submit_via: external`: no starter row at all, not a tick.
    expect(ids({ starter: 'none' })).toEqual(['brief:done']);
  });
});

describe('a template to-do’s hand-out words', () => {
  it('reads the day off the moment’s own offset and the horizon off the status', () => {
    // A New York semester's hand-out, 23:30 on Wed 4 Nov there (05:30 Thu in Berlin).
    const brief = { ...STATUS.problems![0], id: 'template:a1:brief', scope: 'course' as const, stage: 'C5', kind: 'BRIEF', when: '2026-11-04T23:30:00-05:00', fix: { repo: 'o/a1', path: '', line: null, screen: 'template', entry: 'a1' } };
    const now = Date.parse('2026-10-01T12:00:00Z');
    const tiered = [{ p: brief, b: 'later' as const }];
    expect(handoutWords(tiered, 'a1', 'brief', now, { days: 14 })).toBe('Hands out Wed 4 Nov; a problem from 21 Oct.');
    expect(handoutWords(tiered, 'a1', 'brief', now)).toBe('Hands out Wed 4 Nov; a problem from 28 Oct.');
  });
});

describe('read only and the shell', () => {
  it('shows the read-only view for a course the user cannot change', () => {
    const t = text(<ReadonlyScreen course={{ ...course, write: false }} cohort={cohort} />);
    expect(t).toContain('Read only.');
    expect(t).toContain('No roster, no marks, no buttons.');
  });
  it('puts the course tree, nav with the problem count and the on-GitHub links in the side nav, none in the bar', () => {
    const nav = html(<Sidenav courses={[course]} course={course} cohort={cohort} site={cohort} cohortStates={{ [COHORT_ORG]: ready }} current="dashboard" />);
    expect(nav).toContain(`<a class="nav-anchor" href="?course=${COURSE_ORG}#course">Machine Learning</a>`);
    expect(nav).toContain('Fall 2026');
    expect(nav).toContain('class="n-count"');
    expect(nav).toContain('aria-current="page"');
    expect(nav).toContain('aria-label="1 problem"');
    expect(nav).toContain(`https://${COHORT_ORG}.github.io`);
    expect(nav).toContain('Course on GitHub');
    const top = html(<Topbar user={{ login: 'a', id: 1, name: 'A', email: null, avatar_url: '' }} navOpen={false} onMenu={() => {}} />);
    expect(top).not.toContain('github.io');
    expect(top).not.toContain('on GitHub');
    const foot = text(<Footer title={course.name} sub={cohort.termLabel} />);
    expect(foot).toContain('Machine Learning Fall 2026');
    expect(text(<Footer sub="Your semesters" />)).toContain('DSL Teaching Console Your semesters');
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
      problems: [...(STATUS.problems ?? []), { id: 'number:lecture:guest', scope: 'semester', stage: 'K4', kind: 'NOT_NUMBERED', release: 'guest', text: 'Give guest a number.', stops: 'The release on Fri 25 Sep will be skipped.', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'schedule.yml', line: 14, screen: 'schedule', entry: 'guest' }, when: '2026-09-25T10:00:00+02:00', bites: 'soon' }],
    },
  };
  const guestFiles = new StaticFiles({ [`${COHORT_ORG}/semester-config/schedule.yml`]: GUEST }, {}, TREE);

  it('shows an entry with no number by its kind alone, says why it is skipped, and asks for the number', () => {
    const out = html(<ScheduleScreen {...props({ loaded: numbered, files: guestFiles, entry: 'guest' })} />);
    expect(out).toContain('<b>Lecture</b>: ');
    // Fri 25 Sep is inside the horizon: a red `!`, will be skipped, and Fix to the entry.
    expect(out).toContain('<span class="ex" aria-hidden="true">!</span><span class="st-chip skip">will be skipped</span><a class="btn small" href="#schedule-guest">Fix</a>');
    expect(out).toContain('Give it a number first; it cannot be released until it has one.');
    // Said once in the sheet: no problem card repeating it.
    expect(out).not.toContain('Give guest a number.');
    expect(out).toContain('<input id="e-num" type="number" min="1" max="999" value style');
    expect(out).toContain('The number students see. Prefilled with the next one; change it if this is not the next lecture.');
  });

  it('shows a saved entry its own number, and the release detail says what it waits for', () => {
    const out = html(<ScheduleScreen {...props({ entry: 's5' })} />);
    expect(out).toMatch(/<input id="e-num" type="number" min="1" max="999" value="5"/);
    const t = text(<ReleaseScreen {...props({ loaded: numbered, files: guestFiles, entry: 'guest' })} />);
    expect(t).toContain('Give guest a number.');
  });
});

describe('an announcement file', () => {
  it('reads back what it writes, and reads a CRLF file the same way', () => {
    const f = announcementFile('2026-10-05', 'Room change: B2.01');
    expect(readAnnouncement(f.content)).toEqual({ date: '2026-10-05', text: 'Room change: B2.01' });
    const titled = announcementFile('2026-10-05', 'We meet in B2.01.', 'Room change');
    expect(titled.path).toBe('_announcements/2026-10-05-room-change.md');
    expect(readAnnouncement(titled.content)).toEqual({ title: 'Room change', date: '2026-10-05', text: 'We meet in B2.01.' });
    expect(readAnnouncement('---\r\ndate: 2026-10-05\r\n---\r\nNo class today.\r\n')).toEqual({ date: '2026-10-05', text: 'No class today.' });
  });
});

describe('schedule chips by time (decision 0034 §6)', () => {
  it('says was skipped, will be skipped, not ready yet and late, with the lede counting only the red', () => {
    const loaded: Loaded = { kind: 'ready', sha: 's', stale: [], status: heldStatus(STATUS, COHORT_ORG) };
    const out = html(<ScheduleScreen {...props({ loaded })} />);
    const st = (id: string) => out.split(`data-entry="${id}"`)[1].split('</li>')[0].split('<span class="st">')[1];
    expect(st('s2')).toBe('<span class="ex" aria-hidden="true">!</span><span class="st-chip skip">was skipped</span><a class="btn small" href="#schedule-s2">Fix</a></span>');
    expect(st('s4')).toBe('<span class="ex" aria-hidden="true">!</span><span class="st-chip skip">will be skipped</span><a class="btn small" href="#schedule-s4">Fix</a></span>');
    expect(st('s5')).toBe('<span class="st-chip later">not ready yet</span><a class="textlink" href="#schedule-s5">Fix</a></span>');
    expect(st('s3')).toBe('<span class="ex" aria-hidden="true">!</span><span class="st-chip skip">late</span><a class="textlink" href="#release-s3">Details</a></span>');
    expect(out).toContain('<li class="trow lec later" data-entry="s5">');
    expect(text(<ScheduleScreen {...props({ loaded })} />)).toContain('One release was skipped. One release will be skipped as it stands. One release is not ready yet.');
    // The entry sheet and the release page say it once, in the same word.
    expect(html(<ScheduleScreen {...props({ loaded, entry: 's5' })} />)).toContain('<span class="chip suggest">not ready yet</span>');
    expect(html(<ReleaseScreen {...props({ loaded, entry: 's2' })} />)).toContain('<span class="chip bad">was skipped</span>');
  });
});

describe('S14 site lede', () => {
  it('says the page is deployed and mostly auto-populated', () => {
    const t = text(<SiteScreen {...props()} />);
    expect(t).toContain('Deployed semester page for the semester. Most of it is auto-populated from the schedule, instructors and materials.');
    expect(t).not.toContain('single page');
  });
});

describe('no problems', () => {
  it('says only that', () => {
    expect(text(<ProblemCards list={[]} />).trim()).toBe('No problems.');
  });
});

describe('schedule lede', () => {
  it('counts the entries and, with nothing held, says every planned release has its folder', () => {
    const t = text(<ScheduleScreen {...props({ loaded: { ...ready, status: { ...STATUS, releases: [] } } })} />);
    expect(t).toMatch(/\d+ entries\. Every planned release has its associated folder\./);
    expect(t).not.toContain('Every release has its folder.');
  });
});

describe('schedule Check hint', () => {
  it('says what Check reads and links the Check semester setup docs', () => {
    const out = html(<ScheduleScreen {...props()} />);
    expect(out).toContain('Check re-reads the schedule, the course repos and the templates and reports anything that would stop a release, hand-out, collection or return from running: entries that do not parse, dates out of order, a release whose folder is missing, a template whose grading config is invalid. It changes nothing and sends nothing.');
    expect(out).toContain('reference/actions-reference.md');
  });
});
