// The editors: the save flow against a fake GitHub, the schedule and marks models, the
// operation panel's gate, and one render per editing screen.

import { render } from 'preact-render-to-string';
import { parse } from 'yaml';
import { describe, expect, it } from 'vitest';
import SEEDED from '../../templates/semester-config/schedule.yml?raw';
import { EnvCtx, type Env } from '../src/env';
import { CONFLICT, saveText, type SaveState } from '../src/edit/save';
import { YamlText } from '../src/edit/yamlText';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import { penaltyRate } from '../src/model/policy';
import { finalGrade, questionFile, questionPoints, questionsFromRows, readSheet, scoreTotal } from '../src/model/marks';
import { blankDraft, draftErrors, freshId, readDraft, writeDraft, type ArchiveDraft, type ReleaseDraft } from '../src/model/scheduleEdit';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { OpPanel } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { ArchiveScreen } from '../src/screens/Archive';
import { DetailsScreen, MaterialsScreen, WebsiteScreen } from '../src/screens/CourseEdit';
import { AssignmentScreen } from '../src/screens/Assignments';
import { InstructorsScreen, StudentsScreen } from '../src/screens/People';
import type { CohortProps } from '../src/screens/types';
import example from './fixtures/status.example.json';
import { FakeGitHub, fileBody, json } from './fake';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };

// ------------------------------------------------------------------ save

function saveEnv(gh: FakeGitHub): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const statuses = new StatusStore(client);
  const ops = new OpsSession(new DispatchAdapter(client, () => USER.login));
  return { client, user: USER, ops, statuses, files: new StaticFiles(), pollMs: 0 };
}

const withProblem = (problems: object[]) => ({ ...(example as object), problems });
const s5Problem = (example as unknown as Status).problems!.find((p) => p.fix?.entry === 's5')!;

describe('saving a file', () => {
  const T = { owner: COHORT_ORG, repo: 'semester-config', path: 'schedule.yml' };

  it('writes over the sha it read, as the user, then reports the checks and a cleared problem', async () => {
    let checks = 0, statusReads = 0;
    const gh = new FakeGitHub()
      .on('PUT', `/repos/${COHORT_ORG}/semester-config/contents/schedule.yml`, { content: { sha: 'new' }, commit: { sha: 'c9' } })
      .on('GET', `/repos/${COHORT_ORG}/semester-config/commits/c9/check-runs`, () => {
        checks++;
        return json({ check_runs: [{ id: 5, name: 'Validate schedule', status: checks > 1 ? 'completed' : 'in_progress', conclusion: checks > 1 ? 'success' : null, html_url: '', output: { title: null, summary: null, annotations_count: 0 } }] });
      })
      .on('GET', `/repos/${COHORT_ORG}/semester-config/contents/.system/status.json`, () => {
        statusReads++;
        return json(fileBody('.system/status.json', JSON.stringify(withProblem(statusReads > 1 ? [] : [s5Problem]))));
      })
      .on('GET', /git\/trees\/HEAD/, { sha: 't', tree: [], truncated: false });
    const env = saveEnv(gh);
    await env.statuses.reload(COHORT_ORG, 'semester-config');
    const seen: SaveState[] = [];
    const ok = await saveText(env, T, 'releases: {}\n', 'old-sha', { message: 'schedule: edit s5, from the Instructor Console', statusRepo: [COHORT_ORG, 'semester-config'] }, (s) => seen.push(s));
    expect(ok).toBe(true);
    const put = gh.seen.find((x) => x.method === 'PUT')!.body as Record<string, unknown>;
    expect(put.sha).toBe('old-sha');
    expect(put.author).toEqual({ name: 'A. Example', email: '1+a-example@users.noreply.github.com' });
    expect(seen.map((s) => s.kind === 'idle' ? '' : s.text)).toEqual(['Saving…', 'Checking…', 'Valid. Problem cleared.']);
    expect((env.files.file(COHORT_ORG, 'semester-config', 'schedule.yml') as { text: string }).text).toBe('releases: {}\n');
  });

  it('says so when the file moved on, and writes nothing', async () => {
    const gh = new FakeGitHub().on('PUT', /contents\/schedule\.yml$/, () => json({ message: 'schedule.yml does not match old-sha' }, 409));
    const seen: SaveState[] = [];
    expect(await saveText(saveEnv(gh), T, 'x: 1\n', 'old-sha', { message: 'm' }, (s) => seen.push(s))).toBe(false);
    expect(seen.pop()).toEqual({ kind: 'bad', text: CONFLICT, conflict: true });
  });

  it('reports what a failing check said', async () => {
    const gh = new FakeGitHub()
      .on('PUT', /contents\/schedule\.yml$/, { content: { sha: 'n' }, commit: { sha: 'c1' } })
      .on('GET', /commits\/c1\/check-runs/, { check_runs: [{ id: 8, name: 'Validate schedule', status: 'completed', conclusion: 'failure', html_url: '', output: { title: 'x', summary: null, annotations_count: 1 } }] })
      .on('GET', /check-runs\/8\/annotations/, [{ path: 'schedule.yml', start_line: 41, annotation_level: 'failure', title: 'SOURCE', message: 'Session 5 cites a folder that is not there.' }]);
    const seen: SaveState[] = [];
    await saveText(saveEnv(gh), T, 'x: 1\n', 's', { message: 'm' }, (s) => seen.push(s));
    expect(seen.pop()).toEqual({ kind: 'bad', text: 'Saved, but Validate schedule failed: Session 5 cites a folder that is not there.' });
  });
});

// ------------------------------------------------------------------ schedule model

describe('the schedule entry sheet model', () => {
  it('adds a release to the seeded file and keeps every comment', () => {
    const y = new YamlText(SEEDED);
    const doc = y.toJS() as Record<string, unknown>;
    const d = { ...(blankDraft('lecture', { repo: 'course-materials-f2026' }) as ReleaseDraft), title: 'Intro', date: '2026-09-10' };
    d.deploys[0].folder = 'lectures/01_intro';
    expect(draftErrors(d, { templateUsers: () => 0 })).toEqual({});
    const id = freshId(doc, 'releases', 'lecture');
    expect(id).toBe('lecture-1');
    writeDraft(y, { ...d, id }, doc);
    const out = parse(y.text);
    expect(out.releases['lecture-1']).toEqual({ event_datetime: '2026-09-10T10:00', kind: 'lecture', title: 'Intro', deploy: [{ course_source_repo: 'course-materials-f2026', course_source_path: 'lectures/01_intro' }] });
    expect(y.text.split('\n').filter((l) => l.trim().startsWith('#'))).toEqual(SEEDED.split('\n').filter((l) => l.trim().startsWith('#')));
  });

  it('edits an entry in place, keeping the keys the sheet does not show', () => {
    const src = 'releases:\n  s5:\n    event_datetime: 2026-10-08T10:00   # moved once\n    assignment: assignment-2\n    deploy:\n      - course_source_repo: course-materials-f2026\n        course_source_path: lectures/05_trees\n';
    const y = new YamlText(src);
    const doc = y.toJS() as Record<string, unknown>;
    const d = readDraft(doc, 's5') as ReleaseDraft;
    expect(d.deploys[0].folder).toBe('lectures/05_trees');
    writeDraft(y, { ...d, deploys: [{ ...d.deploys[0], folder: 'lectures/05_trees_and_ensembles' }] }, doc);
    expect(y.text).toBe(src.replace('lectures/05_trees\n', 'lectures/05_trees_and_ensembles\n'));
  });

  it('keeps a moment as the file spells it when only the title changes', () => {
    const src =
      'releases:\n  s5:\n    event_datetime: "2026-10-08T10:00:00+01:00"\n    title: Trees\n    deploy:\n      - course_source_repo: m\n        course_source_path: l\n' +
      'assignments:\n  a2:\n    course_source_repo: assignment-2-f2026\n    handout_datetime: 2026-09-15 10:00\n    due_datetime: 2026-09-27T23:59:00+02:00\n';
    const y = new YamlText(src);
    const doc = y.toJS() as Record<string, unknown>;
    writeDraft(y, { ...(readDraft(doc, 's5') as ReleaseDraft), title: 'Trees and ensembles' }, doc);
    writeDraft(y, { ...(readDraft(doc, 'a2') as never as object), title: 'Regression' } as never, doc);
    // An assignment's title is its template's (decision 0009): never written here.
    expect(y.text).toBe(src.replace('title: Trees\n', 'title: Trees and ensembles\n'));
  });

  it('turns automatic archiving off by removing the block, and checks an assignment’s dates', () => {
    const y = new YamlText(SEEDED);
    const doc = y.toJS() as Record<string, unknown>;
    writeDraft(y, { ...(readDraft(doc, 'archive') as never as object), kind: 'archive', on: false } as never, doc);
    expect(parse(y.text)?.archive).toBeUndefined();
    const a = { ...blankDraft('handout', { repo: '' }), template: 'assignment-2-f2026', handoutDate: '2026-09-15', dueDate: '2026-09-10', solutionOn: true, solutionDate: '2026-09-14' };
    const e = draftErrors(a as never, { templateUsers: () => 2 });
    expect(e.due).toBe('Due must come after the hand out.');
    expect(e.solution).toBe('Must be after the hand out.');
    // The repo name is assignments.yml's now: nothing here asks for it.
    expect(e.semesterRepo).toBeUndefined();
  });

  it('leaves a retired key an unmigrated entry still carries exactly as it is', () => {
    const src = 'assignments:\n  a2:\n    course_source_repo: assignment-2-f2026\n    semester_dest_repo: homework-2\n    title: Regression\n    due_datetime: 2026-09-27T23:59:00+02:00\n    grading_datetime: 2026-10-07\n';
    const y = new YamlText(src);
    const doc = y.toJS() as Record<string, unknown>;
    writeDraft(y, { ...(readDraft(doc, 'a2') as never as object), details: 'Fit it.' } as never, doc);
    const a2 = (parse(y.text).assignments as Record<string, Record<string, unknown>>).a2;
    expect(a2).toMatchObject({ semester_dest_repo: 'homework-2', title: 'Regression', grading_datetime: '2026-10-07', details: 'Fit it.' });
  });

  it('edits the archive grace days, writing the key only once it differs from 60', () => {
    const y = new YamlText(SEEDED);
    const doc = y.toJS() as Record<string, unknown>;
    const d = readDraft(doc, 'archive') as ArchiveDraft;
    expect(d.graceDays).toBe(60);
    writeDraft(y, { ...d, title: 'Frozen' }, doc);
    expect(parse(y.text).archive).not.toHaveProperty('grace_days');
    writeDraft(y, { ...d, graceDays: 90 }, doc);
    expect(parse(y.text).archive.grace_days).toBe(90);
    expect(y.text.split('\n').filter((l) => l.trim().startsWith('#'))).toEqual(SEEDED.split('\n').filter((l) => l.trim().startsWith('#')));
    const y2 = new YamlText(y.text);
    const doc2 = y2.toJS() as Record<string, unknown>;
    const d2 = readDraft(doc2, 'archive') as ArchiveDraft;
    expect(d2.graceDays).toBe(90);
    writeDraft(y2, { ...d2, graceDays: '' }, doc2);
    expect(parse(y2.text).archive).not.toHaveProperty('grace_days');
    expect(draftErrors({ ...d, graceDays: -1 }, { templateUsers: () => 0 }).graceDays).toBe('A whole number of days, 0 or more.');
    expect(draftErrors({ ...d, graceDays: 1.5 }, { templateUsers: () => 0 }).graceDays).toBeDefined();
    expect(draftErrors({ ...d, graceDays: 0 }, { templateUsers: () => 0 })).toEqual({});
  });
});

// ------------------------------------------------------------------ marks model

describe('marks arithmetic, as grades.py does it', () => {
  it('totals questions, applies the late penalty and the adjustment, and floors at zero', () => {
    expect(scoreTotal({ Q1: 14, Q2: 13, Q3: null }, { Q1: 15, Q2: 15, Q3: 10 })).toBe(27);
    expect(scoreTotal({ Q1: 14, Q9: 10 }, { Q1: 15 })).toBe(14);
    expect(scoreTotal({ Q1: 'pass' })).toBeNull();
    expect(penaltyRate('10%')).toBe(0.1);
    expect(penaltyRate('0.1')).toBe(0.1);
    expect(penaltyRate('10')).toBeNull();
    expect(finalGrade(50, 0.1, 2, 4)).toBeCloseTo(44);
    expect(finalGrade(10, 0.5, 3, -2)).toBe(0);
    expect(finalGrade(null, 0.1, 1, 5)).toBeNull();
  });

  it('reads a question given as {points, file} and keeps file: on a save', () => {
    expect(questionPoints({ points: 5, file: 'report.tex' })).toBe(5);
    expect(questionPoints(10)).toBe(10);
    expect(questionFile({ points: 5, file: 'report.tex' })).toBe('report.tex');
    expect(questionFile(10)).toBeNull();
    const was = { Q1: 10, Q2: { points: 5, file: 'report.tex' } };
    expect(questionsFromRows([['Q1', '12'], ['Q2', '6'], ['Q3', '']], was)).toEqual({
      Q1: 12,
      Q2: { points: 6, file: 'report.tex' },
      Q3: null,
    });
    expect(questionsFromRows([[' ', '1']], was)).toBeUndefined();
  });

  it('reads a team sheet into units and people with their paths', () => {
    const text = 'teams:\n  team-alpha:\n    info:\n      days_late: 1\n    score_group:\n      Q1: 14\n    feedback_group: Good\n    members:\n      anna-a:\n        adjustment_individual: 4\n';
    const s = readSheet(text, parse(text));
    expect(s.group).toBe(true);
    expect(s.units[0].people[0]).toMatchObject({ handle: 'anna-a', adjustment: 4, base: ['teams', 'team-alpha', 'members', 'anna-a'] });
  });
});

// ------------------------------------------------------------------ the panel

describe('the operation panel', () => {
  it('opens with Preview filled and the gated verb shut, and names the op, scope and intro', () => {
    const gh = new FakeGitHub();
    const env = saveEnv(gh);
    env.ops.open(defs.sendCodes({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 6));
    const out = render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>);
    expect(out).toContain('Send new codes, Fall 2026');
    expect(out).toContain('Their old codes stop working.');
    expect(out).toContain('<button class="btn" type="button">Preview</button>');
    expect(out).toMatch(/<button class="btn outline" type="button" disabled>Send 6 new codes<\/button>/);
    expect(out).toContain('Send 6 new codes unlocks when the preview finishes.');
    env.ops.minimise();
    expect(render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>)).toContain('class="opbar"');
  });

  it('shows the return-marks channels, the always-on ones locked', () => {
    const env = saveEnv(new FakeGitHub());
    env.ops.open(defs.returnMarks({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, { slug: 'assignment-2', title: 'Assignment 2: Regression', template: 'assignment-2-f2026', units: 48, group: false, when: 'Marking' }, 40, 'assignment-2'));
    const out = render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>);
    expect(out).toContain('Update each student’s marks repo<span class="always">always</span>');
    expect(out).toContain('Email students whose marks changed');
    expect(out).toContain('Post a note on each Submission receipts issue');
    expect(out).toContain('Include the feedback text in the email');
  });
});

// ------------------------------------------------------------------ screens

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = {
  org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: 'A course.', write: true, admins: ['a-example'], cohorts: [cohort],
  meta: { assignment_defaults: { late_window_days: 10, late_penalty_per_day: '10%', max_team_size: 5 } },
};
const groupStatus: Status = {
  ...STATUS,
  assignments: [...(STATUS.assignments ?? []), { slug: 'assignment-3', title: 'Group project', template: 'assignment-3-f2026', state: 'teams_forming', handout: '2026-09-20T10:00:00+02:00', due: '2026-10-20T23:59:00+02:00', grading_cutoff_datetime: '2026-10-30T23:59:00+02:00', solution_shown: null, units: 12, submissions: 0, teams: 1, marks: { filled: 0, total: 12 }, returned: false, problem: true }],
};
const ready: Loaded = { kind: 'ready', status: groupStatus, sha: 's', stale: [] };
const FILES: Record<string, string> = {
    [`${COHORT_ORG}/semester-config/students.csv`]: 'hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna Adams,enrolled,anna-a,101,SECRET1,2026-09-02T09:30:00Z\nben@x.org,Ben Baker,enrolled,ben-b,102,SECRET2,2026-09-02T09:30:00Z\ncarla@x.org,Carla Cohen,,carla-c,103,SECRET3,2026-09-02T09:30:00Z\n',
    [`${COHORT_ORG}/semester-config/instructors.yml`]: 'instructors:\n  - github_handle: a-example\n    role: instructor\n    email: a@staff.example.org\n    name: Dr A. Example\n',
    [`${COHORT_ORG}/semester-config/teams.csv`]: 'assignment,team,github_handle\nassignment-3,team-alpha,anna-a\nassignment-3,team-alpha,ben-b\n',
    [`${COHORT_ORG}/semester-config/grading_sheets/assignment-2.yml`]: '# GRADING SHEET\n# Status: OPEN\nsubmissions:\n  anna-a:\n    info:\n      submitted: 2026-09-27T20:00\n      days_late: 2\n    score_individual:\n      Q1: 14\n      Q2: 20\n    adjustment_individual: 1\n    feedback_individual: |\n      Good.\n    notes_not_shared_with_students:\n',
    [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'questions:\n  Q1: 15\n  Q2: 25\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'type: group\n',
    [`${COHORT_ORG}/semester-config/assignments.yml`]: 'assignments:\n  assignment-3:\n    max_team_size: 3\n',
    [`${COURSE_ORG}/.github/dsl-course.yml`]: '# INSTRUCTOR-OWNED\norg: hertie-dsl-demo-course-e1234\ncourse_name: Machine Learning\ncourse_code: E1234\npeople:\n  course_admins:\n    - github_handle: a-example\n      email: a@staff.example.org\nassignment_defaults:\n  late_window_days: 10\n  late_penalty_per_day: 10%\n',
    [`${COURSE_ORG}/course-materials-f2026/publish.yml`]: 'public:\n  - "lectures/**/*.html"\n',
    [`${COURSE_ORG}/course-materials-f2026/.releaseignore`]: 'solutions/\n',
};
const TREES = { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/01/slides.html', 'labs/01/solutions/a.py'] };
const files = new StaticFiles(FILES, {}, TREES);
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files, now: NOW, ...over });
const html = (v: preact.VNode) => render(v);

describe('editing screens', () => {
  it('the roster grid edits your columns only and keeps the code out of the page', () => {
    const out = html(<StudentsScreen {...props()} />);
    expect(out).toContain('value="anna@x.org"');
    expect(out).toContain('Add a student');
    expect(out).toContain('Replace from CSV');
    expect(out).toContain('Send new codes to students who have not joined');
    expect(out).not.toContain('SECRET');
  });
  it('staff has Add a person and Check instructor access', () => {
    const out = html(<InstructorsScreen {...props()} />);
    expect(out).toContain('Add a person');
    expect(out).toContain('Check instructor access');
    expect(out).toContain('>Edit<');
  });
  it('teams shows the window, the team size from assignments.yml and who has no team', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams' })} />);
    expect(out).toContain('<h1>Assignment 3: Group project</h1>');
    expect(out).toContain('2 of 3 joined students in 1 teams; 1 without a team.');
    expect(out).toContain('team-alpha<span>2 of 3</span>');
    expect(out).toContain('Carla Cohen');
    expect(out).toContain('Email 1 without a team');
  });
  it('marks computes the total with the penalty and adjustment', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks' })} />);
    expect(out).toContain('<h1>Assignment 2: Regression</h1>');
    expect(out).toContain('Total / 40');
    expect(out).toContain('−20%');
    expect(out).toContain('<td class="calc">28.2</td>');
    expect(out).toContain('Return marks to 0 students');
  });
  it('marks reads a question marked from another file by its points', () => {
    const tagged = new StaticFiles(
      { ...FILES, [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'questions:\n  Q1: 15\n  Q2: {points: 25, file: report.tex}\n' },
      {},
      TREES,
    );
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks', files: tagged })} />);
    expect(out).toContain('Total / 40');
    expect(out).toContain('/ 25 · <code>report.tex</code>');
    expect(out).not.toContain('[object Object]');
  });
  it('archive offers Preview and the gated verb', () => {
    const out = html(<ArchiveScreen {...props()} />);
    expect(out).toContain('Archive Fall 2026');
    expect(out).toMatch(/disabled title="Preview first">Archive Fall 2026/);
  });
  const cp = { course, loaded: { kind: 'absent' } as Loaded, cohortStates: { [COHORT_ORG]: ready }, files, now: NOW };
  it('course details edits admins with emails and the defaults', () => {
    const out = html(<DetailsScreen {...cp} />);
    expect(out).toContain('value="Machine Learning"');
    expect(out).toContain('value="a@staff.example.org"');
    expect(out).toContain('Defaults for this course’s assignments');
    expect(out).not.toContain('Semester defaults'); // the engine reads none from dsl-course.yml
  });
  it('materials settings previews what is public and what is withheld', () => {
    const out = html(<MaterialsScreen {...cp} entry="course-materials-f2026" />);
    expect(out).toMatch(/<span class="ft-name">slides.html<\/span><span class="chip ok">published openly<\/span>/);
    expect(out).toMatch(/<span class="ft-name">a.py<\/span><span class="chip amber">withheld<\/span>/);
    expect(out).toContain('Write the session list');
  });
  it('materials settings says when no file matches', () => {
    const none = new StaticFiles({ [`${COURSE_ORG}/course-materials-f2026/publish.yml`]: 'public: []\n' }, {}, { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/01/slides.html'] });
    const out = html(<MaterialsScreen {...cp} files={none} entry="course-materials-f2026" />);
    expect(out).toMatch(/<span class="ft-name">SYLLABUS.md<\/span><span class="chip ">released to students<\/span>/);
    expect(out).not.toContain('published openly</span><a');
    expect(out).not.toContain('class="file-list"');
  });
  it('the public website asks for the confirmation the engine’s missing preview needs', () => {
    const out = html(<WebsiteScreen {...cp} />);
    expect(out).toContain('Publish public website');
    expect(out).toContain('Source materials');
  });
});
