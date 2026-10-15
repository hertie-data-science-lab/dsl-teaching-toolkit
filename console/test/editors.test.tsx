// The editors: the save flow against a fake GitHub, the schedule and marks models, the
// operation panel's gate, and one render per editing screen.

import { render } from 'preact-render-to-string';
import { parse } from 'yaml';
import { describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { CONFLICT, saveText, type SaveState } from '../src/edit/save';
import { YamlText, obj } from '../src/edit/yamlText';
import { fieldErrors } from '../src/forms/Form';
import { COURSE_FACTS } from '../src/tiers/course';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { rosterFiles } from './roster';
import { CONTACTS, POLICY, penaltyRate } from '../src/model/policy';
import { effectivePenalty, finalGrade, lateChoice, overrideText, overrideValue, penaltyText, questionFile, questionPoints, questionsFromRows, readSheet, scoreTotal } from '../src/model/marks';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { OpPanel } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { cohortFlags } from '../src/ui/shell';
import { DetailsScreen, MaterialsScreen, WebsiteScreen, courseFileAfter, detailsOf, missingAdmin } from '../src/screens/CourseEdit';
import { AssignmentScreen } from '../src/screens/Assignments';
import { ScheduleScreen } from '../src/screens/Schedule';
import { lateReturn } from '../src/screens/Marking';
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
    const ok = await saveText(env, T, 'releases: {}\n', 'old-sha', { message: 'schedule: edit s5, from the DSL Teaching Console', statusRepo: [COHORT_ORG, 'semester-config'] }, (s) => seen.push(s));
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

// ------------------------------------------------------------------ marks model

describe('marks arithmetic, as grades.py does it', () => {
  it('totals questions, applies the late penalty and the adjustment, and floors at zero', () => {
    expect(scoreTotal({ Q1: 14, Q2: 13, Q3: null }, { Q1: 15, Q2: 15, Q3: 10 })).toBe(27);
    expect(scoreTotal({ Q1: 14, Q9: 10 }, { Q1: 15 })).toBe(14);
    expect(scoreTotal({ Q1: 'pass' })).toBeNull();
    expect(penaltyRate('10%')).toBe(0.1);
    expect(penaltyRate('0.1')).toBe(0.1);
    expect(penaltyRate('10')).toBeNull();
    expect(finalGrade(50, effectivePenalty(0.1, 2), 4)).toBeCloseTo(44);
    expect(finalGrade(10, effectivePenalty(0.5, 3), -2)).toBe(0);
    expect(finalGrade(null, effectivePenalty(0.1, 1), 5)).toBeNull();
  });

  it('takes the late penalty the grader chose, as grades.effective_penalty does', () => {
    expect(effectivePenalty(0.1, 2).fraction).toBeCloseTo(0.2);
    expect(effectivePenalty(0.1, 2, 'apply').fraction).toBeCloseTo(0.2);
    expect(effectivePenalty(0.1, 2, 'waive').fraction).toBe(0);
    expect(effectivePenalty(0.1, 2, '15%').fraction).toBeCloseTo(0.15);
    expect(effectivePenalty(0.1, 2, 0.15).fraction).toBeCloseTo(0.15);
    expect(effectivePenalty(0.1, 0, '15%').fraction).toBe(0);
    // No late rule: the engine ignores late_penalty, so nothing comes off whatever it says.
    expect(effectivePenalty(null, 2, '15%').fraction).toBe(0);
    // Nobody can apply these: the late rule's arithmetic, and the engine holds the unit.
    expect(effectivePenalty(0.1, 2, 'half')).toMatchObject({ mode: 'bad', fraction: 0.2 });
    expect(effectivePenalty(0.1, 2, '15').mode).toBe('bad');
    expect(lateChoice(' Waive ')).toEqual({ mode: 'waive' });
    expect(lateChoice('15%')).toEqual({ mode: 'set', set: 0.15 });
    expect(lateChoice(null)).toEqual({ mode: 'apply' });
    expect(finalGrade(20, effectivePenalty(0.1, 2, 'waive'), null)).toBe(20);
    expect(finalGrade(20, effectivePenalty(0.1, 2, '5%'), 1)).toBe(20);
    expect(finalGrade(20, effectivePenalty(0.1, 2, 'half'), null)).toBeCloseTo(16);
    expect(penaltyText(effectivePenalty(0.1, 2), 0.1)).toBe('−20%');
    expect(penaltyText(effectivePenalty(0.1, 2, 'waive'), 0.1)).toBe('waived');
    expect(penaltyText(effectivePenalty(0.1, 2, '15%'), 0.1)).toBe('−15% (set by hand)');
    expect(penaltyText(effectivePenalty(0.1, 0, 'waive'), 0.1)).toBe('');
    expect(penaltyText(effectivePenalty(null, 2), null)).toBe('');
    expect(overrideValue('15')).toBe('15%');
    expect(overrideValue(' 12.5% ')).toBe('12.5%');
    expect(overrideValue('')).toBe('apply');
    expect(overrideValue('half')).toBe('half');
    // Not a penalty anybody can apply: written as typed, and held.
    expect(overrideValue('150')).toBe('150');
    expect(overrideValue('-5')).toBe('-5');
    // A set penalty of nothing is a waiver: written as one, and an existing `0%` reads as one.
    expect(overrideValue('0')).toBe('waive');
    expect(overrideValue('0%')).toBe('waive');
    expect(lateChoice('0%')).toEqual({ mode: 'waive' });
    expect(penaltyText(effectivePenalty(0.1, 2, '0%'), 0.1)).toBe('waived');
    expect(overrideText('0%')).toBe('');
    expect(overrideText('15%')).toBe('15');
    expect(overrideText(0.15)).toBe('15');
    expect(overrideText('apply')).toBe('');
    expect(overrideText('waive')).toBe('');
    expect(overrideText(' half ')).toBe('half');
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
  it('opens with Preview filled and the gated verb live, and names the op, scope and intro', () => {
    const gh = new FakeGitHub();
    const env = saveEnv(gh);
    env.ops.open(defs.sendCodes({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 6));
    const out = render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>);
    expect(out).toContain('Send new codes, Fall 2026');
    expect(out).toContain('Their old codes stop working.');
    expect(out).toContain('<button class="btn" type="button">Preview</button>');
    expect(out).toMatch(/<button class="btn outline" type="button">Send 6 new codes<\/button>/);
    expect(out).not.toContain('unlocks when the preview finishes');
    env.ops.minimise();
    expect(render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>)).toContain('class="opbar"');
  });

  it('names the weekly plan’s preview Copy in the panel too, never Preview', () => {
    const env = saveEnv(new FakeGitHub());
    env.ops.open(defs.generateSyllabus({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, 'course-materials-f2026', 'SYLLABUS.md'));
    const out = render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>);
    expect(out).toContain('<button class="btn" type="button">Copy</button>');
    expect(out).not.toContain('>Preview</button>');
    expect(out).toContain('>Write the weekly plan</button>');
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

  it('says late work can still change marks only while the late cutoff has not passed (decision 0037)', () => {
    const a = (example as unknown as Status).assignments![0]; // late cutoff Wed 7 Oct 23:59, Berlin
    const ref = { slug: 'assignment-2', title: 'Assignment 2: Regression', template: 'assignment-2-f2026', units: 48, group: false, when: 'Marking' };
    const dialog = (now: string) => {
      const env = saveEnv(new FakeGitHub());
      env.ops.open(defs.returnMarks({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, ref, 40, 'assignment-2', lateReturn(a, Date.parse(now), 'Europe/Berlin', 2026)));
      return render(<EnvCtx.Provider value={env}><OpPanel /></EnvCtx.Provider>);
    };
    expect(dialog('2026-10-01T10:00:00+02:00')).toContain('<p class="op-confirm">The deadline was Sun 27 Sep 23:59, but 48 students can still submit late until Wed 7 Oct 23:59 (the late cutoff: due date plus the late window). A late submission updates that student’s penalty (10% of the total per day started) and final grade. Return marks again afterwards: only students whose marks changed are updated and emailed.</p>');
    const after = dialog('2026-10-08T10:00:00+02:00');
    expect(after).not.toContain('op-confirm');
    expect(after).not.toContain('can still submit late');
  });
});

// ------------------------------------------------------------------ screens

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = {
  org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: 'A course.', write: true, admins: ['a-example'], cohorts: [cohort],
  meta: { defaults: { late_window_days: 10, late_penalty_per_day: '10%', max_team_size: 5 } },
};
const groupStatus: Status = {
  ...STATUS,
  assignments: [...(STATUS.assignments ?? []), { slug: 'assignment-3', number: 3, title: 'Group project', template: 'assignment-3-f2026', state: 'teams_forming', handout: '2026-09-20T10:00:00+02:00', due: '2026-10-20T23:59:00+02:00', grading_cutoff_datetime: '2026-10-30T23:59:00+02:00', solution_shown: null, units: 12, submissions: 0, teams: 1, marks: { filled: 0, total: 12 }, returned: false, problem: true, settings: { max_team_size: { value: 3, source: 'entry' }, team_formation: { value: 'self_select', source: 'institution' } } }],
};
const ready: Loaded = { kind: 'ready', status: groupStatus, sha: 's', stale: [] };
const SITE = 'open_course_site:\n  enabled: true   # false: nothing is published\n  source_repo: course-materials-f2026\n  publish:\n    kinds: [lectures, labs]\n    exclude: ["labs/"]\n';
const COURSE_YML = `# INSTRUCTOR-OWNED\ncourse:\n  name: Machine Learning\n  code: E1234\npeople:\n  course_admins:\n    - github_handle: a-example\n      email: a@staff.example.org\ndefaults:\n  late_window_days: 10\n  late_penalty_per_day: 10%\n${SITE}`;
const FILES: Record<string, string> = {
    ...rosterFiles(COHORT_ORG, 'email,name,role,team:3,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna Adams,enrolled,team-alpha,anna-a,101,SECRET1,2026-09-02T09:30:00Z\nben@x.org,Ben Baker,enrolled,team-alpha,ben-b,102,SECRET2,2026-09-02T09:30:00Z\ncarla@x.org,Carla Cohen,,,carla-c,103,SECRET3,2026-09-02T09:30:00Z\n'),
    [`${COHORT_ORG}/semester-config/semester.yml`]: 'people:\n  - github_handle: a-example\n    role: instructor\n    email: a@staff.example.org\n    name: Dr A. Example\n',
    [`${COHORT_ORG}/semester-config/grading_sheets/assignment-2.yml`]: '# GRADING SHEET\n# Status: OPEN\nsubmissions:\n  anna-a:\n    info:\n      submitted: 2026-09-27T20:00\n      days_late: 2\n    score_individual:\n      Q1: 14\n      Q2: 20\n    adjustment_individual: 1\n    feedback_individual: |\n      Good.\n    notes_not_shared_with_students:\n',
    [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'questions:\n  Q1: 15\n  Q2: 25\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'type: group\n',
    [`${COURSE_ORG}/.github/course.yml`]: COURSE_YML,
    [`${COURSE_ORG}/course-materials-f2026/.releaseignore`]: 'solutions/\n',
};
const TREES = { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/01/slides.html', 'labs/01/solutions/a.py'] };
const files = new StaticFiles(FILES, {}, TREES);
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files, now: NOW, ...over });
const html = (v: preact.VNode) => render(v);

describe('editing screens', () => {
  it('the roster grid reads until Edit roster, and keeps the code out of the page', () => {
    const out = html(<StudentsScreen {...props()} />);
    expect(out).toContain('anna@x.org');
    expect(out).not.toContain('value="anna@x.org"');
    expect(out).toContain('aria-label="Edit roster"');
    expect(out).toContain('Add a student');
    expect(out).toContain('Replace from CSV');
    expect(out).toContain('>Send codes<');
    expect(out).toContain('>Sync now<');
    expect(out).not.toContain('SECRET');
  });
  it('staff has Add a person and Check instructor access', () => {
    const out = html(<InstructorsScreen {...props()} />);
    expect(out).toContain('Add a person');
    expect(out).toContain('Check instructor access');
    expect(out).toContain('>Edit<');
  });
  it('points every link to a file that does not exist yet at GitHub’s new-file page, never a 404', () => {
    const none = new StaticFiles();
    for (const [screen, path] of [[<StudentsScreen {...props({ files: none })} />, 'students.csv'], [<InstructorsScreen {...props({ files: none })} />, 'semester.yml']] as const) {
      const out = html(screen);
      expect(out).toContain(`/new/main?filename=${path}`);
      expect(out).not.toContain(`/edit/main/${path}`);
      expect(out).not.toContain(`/blob/main/${path}`);
    }
  });
  it('teams shows the window, the team size status states and who has no team', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-3', tab: 'teams' })} />);
    expect(out).toContain('<h2 class="h1">Assignment 3: Group project <span class="hint-wrap">');
    expect(out).toContain('2 of 3 joined students in 1 teams; 1 without a team.');
    expect(out).toContain('team-alpha<span>2 of 3</span>');
    expect(out).toContain('Carla Cohen');
    expect(out).toContain('Email 1 without a team');
  });
  it('marks computes the total with the penalty and adjustment', () => {
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks' })} />);
    expect(out).toContain('<h2 class="h1">Assignment 2: Regression <span class="hint-wrap">');
    expect(out).toContain('Final / 40');
    expect(out).toContain('<span class="val">−20%</span>');
    expect(out).toContain('<td class="calc">28.2</td>');
    expect(out).toContain('aria-label="Apply the late penalty for anna-a"');
    // Marked, not held: the one student gets marks, and the pop-up says so.
    expect(out).toContain('Return marks to 1 student<');
  });
  it('marks reads a question marked from another file by its points', () => {
    const tagged = new StaticFiles(
      { ...FILES, [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'questions:\n  Q1: 15\n  Q2: {points: 25, file: report.tex}\n' },
      {},
      TREES,
    );
    const out = html(<AssignmentScreen {...props({ entry: 'assignment-2', tab: 'marks', files: tagged })} />);
    expect(out).toContain('Final / 40');
    expect(out).toContain('/ 25 · <code>report.tex</code>');
    expect(out).not.toContain('[object Object]');
  });
  it('the schedule’s archive entry explains archiving and offers Preview and the verb', () => {
    const withSched = new StaticFiles({ ...FILES, [`${COHORT_ORG}/semester-config/schedule.yml`]: 'semester:\n  archive: {type: archive, event_datetime: 2027-02-16}\n' }, {}, TREES);
    const out = html(<ScheduleScreen {...props({ entry: 'archive', files: withSched })} />);
    expect(out).toContain('aria-label="About archiving"');
    expect(out).toContain('Students and instructors keep read access; nothing is deleted');
    expect(out).toContain('Archive Fall 2026');
    expect(out).not.toContain('Preview first');
  });
  it('a semester archived before it had a status counts as archived', () => {
    expect(cohortFlags({ kind: 'absent', archived: true })).toEqual({ problems: null, archived: true });
    expect(cohortFlags({ kind: 'absent' })).toEqual({ problems: null, archived: false });
  });
  const cp = { course, loaded: { kind: 'absent' } as Loaded, cohortStates: { [COHORT_ORG]: ready }, files, now: NOW };
  it('course details edits admins with emails and the defaults', () => {
    const out = html(<DetailsScreen {...cp} />);
    expect(out).toContain(`<div class="actions"><a class="btn quiet" href="https://github.com/${COURSE_ORG}/.github/edit/main/course.yml" target="_blank" rel="noopener" title="${COURSE_ORG}/.github/course.yml">Edit the file directly`);
    expect(out).not.toContain('New semester');
    // On .github's own default branch; GitHub's new-file page while course.yml does not exist.
    const trunk = new StaticFiles({}, {}, {}, { [COURSE_ORG]: [{ name: '.github', default_branch: 'trunk' }] });
    expect(html(<DetailsScreen {...cp} files={trunk} />)).toContain(`href="https://github.com/${COURSE_ORG}/.github/new/trunk?filename=course.yml"`);
    expect(out).toContain('value="Machine Learning"');
    expect(out).toContain('value="a@staff.example.org"');
    expect(out).toMatch(/<details class="fold"><summary>Advanced<\/summary>[^]*Defaults for this course’s semesters[^]*<\/details>/);
    expect(out).not.toContain('Student site links');
    expect(out).toContain('id="cdx-late_window_days" value="10"');
  });
  it('course details asks for the contact and licence, with the institution’s in grey', () => {
    const out = html(<DetailsScreen {...cp} />);
    expect(out).toContain(`placeholder="${CONTACTS[0]}"`);
    expect(out).toContain(`institution default: ${POLICY.licences[0].name}`);
    expect(out).toContain(`<option value="${POLICY.licences[1].name}"`);
    // The website's on/off is a switch here, beside the link to its own tab.
    expect(out).toMatch(/<input type="checkbox" role="switch" id="cd-web" aria-describedby="cd-web-state" checked\/><span>Public OpenCourse website <span class="chip">Beta<\/span><\/span><\/label><span class="footnote" id="cd-web-state" aria-live="polite">On: updates daily<\/span> <a class="textlink" href="#website">Manage<\/a>/);
    const off = new StaticFiles({ ...FILES, [`${COURSE_ORG}/.github/course.yml`]: COURSE_YML.replace(SITE, 'open_course_site:\n  enabled: false\n') }, {}, TREES);
    expect(html(<DetailsScreen {...cp} files={off} />)).toContain('aria-describedby="cd-web-state"/><span>Public OpenCourse website <span class="chip">Beta</span></span></label><span class="footnote" id="cd-web-state" aria-live="polite">Off</span>');
    const none = new StaticFiles({ ...FILES, [`${COURSE_ORG}/.github/course.yml`]: COURSE_YML.replace(SITE, '') }, {}, TREES);
    expect(html(<DetailsScreen {...cp} files={none} />)).toContain('aria-live="polite">Off</span>');
  });
  it('skips the account check when only a course admin handle’s case changes', async () => {
    const src = 'course:\n  name: ML\npeople:\n  course_admins:\n    - github_handle: octo\n      email: o@x.edu\n';
    const meta = obj(new YamlText(src).toJS());
    const before = detailsOf(meta);
    const after = { ...before, admins: [{ ...before.admins[0], github_handle: 'Octo' }] };
    const out = courseFileAfter(src, before, after, meta);
    expect('text' in out && out.text).toContain('github_handle: Octo');
    const looked: string[] = [];
    const env = { client: { userExists: async (h: string) => (looked.push(h), false) } } as unknown as Env;
    expect(await missingAdmin(env, before.admins, after.admins)).toBeNull();
    expect(looked).toEqual([]);
  });
  it('writes a contact into open_course_site and a licence into course: in course.yml, and a file without them still saves', () => {
    const src = 'course:\n  name: ML\n  code: E1\n';
    const meta = obj(new YamlText(src).toJS());
    const before = detailsOf(meta);
    const out = courseFileAfter(src, before, { ...before, about: { ...before.about, contact: 'ml@x.edu', licence: 'CC BY 4.0' } }, meta);
    expect('text' in out && parse(out.text)).toEqual({ course: { name: 'ML', code: 'E1', licence: 'CC BY 4.0' }, open_course_site: { contact: 'ml@x.edu' } });
    const same = courseFileAfter(src, before, { ...before, about: { ...before.about, course_code: 'E2' } }, meta);
    expect('text' in same && same.text).toBe('course:\n  name: ML\n  code: E2\n');
    // Clearing the contact drops the block it emptied, which the schema would refuse as null.
    const withContact = 'course:\n  name: ML\nopen_course_site:\n  contact: ml@x.edu\n';
    const m2 = obj(new YamlText(withContact).toJS());
    const b2 = detailsOf(m2);
    const cleared = courseFileAfter(withContact, b2, { ...b2, about: { ...b2.about, contact: undefined } }, m2);
    expect('text' in cleared && cleared.text).toBe('course:\n  name: ML\n');
    expect(fieldErrors(null, COURSE_FACTS, { contact: 'not-an-email' }).contact).toBe('Write an email address.');
  });
  it('materials settings previews what is withheld, and nothing about the public website', () => {
    const out = html(<MaterialsScreen {...cp} entry="course-materials-f2026" />);
    expect(out).toMatch(/<span class="ft-name">slides.html<\/span><span class="chip ">released to students<\/span>/);
    // solutions/ is the institution denylist's too (decision 0040): a folder release leaves it behind; only naming it ships it.
    expect(out).toMatch(/<span class="ft-name">a.py<\/span><span class="chip amber">only if named<\/span>/);
    expect(out).toContain('<span class="ft-name">solutions/</span><span class="chip amber">only if named</span>');
    expect(out).not.toContain('aria-label="Withhold labs/01/solutions/"');
    // The panel's last line: a solution is not copied unless named, so it counts there, not as withheld.
    expect(out).toContain('<p class="footnote">1 never copied (by default) · 0 withheld · 2 released</p>');
    expect(out).toContain('Weekly plan for the syllabus');
    // Copy opens the plan to copy; Write puts it into the syllabus (decision 0031 rule 9).
    expect(out).toContain('>Copy</button>');
    expect(out).toContain('>Write</button>');
    expect(out).not.toContain('>Preview</button>');
    expect(out).toContain('aria-label="About Write"');
    // A syllabus that is not Markdown cannot be written into: Copy alone, and why.
    const pdf = new StaticFiles({ ...FILES, [`${COHORT_ORG}/semester-config/semester.yml`]: 'student_site:\n  syllabus: E1282.pdf\n' }, {}, TREES);
    const onlyCopy = html(<MaterialsScreen {...cp} files={pdf} entry="course-materials-f2026" />);
    expect(onlyCopy).toContain('>Copy</button>');
    expect(onlyCopy).not.toContain('>Write</button>');
    expect(onlyCopy).toContain('E1282.pdf is not Markdown, so the plan cannot be written into it: copy it and paste it in.');
    // A Markdown syllabus that is not there yet: nothing to write into either.
    const absent = new StaticFiles({ ...FILES, [`${COHORT_ORG}/semester-config/semester.yml`]: 'student_site:\n  syllabus: E1282.md\n' }, {}, TREES);
    const noFile = html(<MaterialsScreen {...cp} files={absent} entry="course-materials-f2026" />);
    expect(noFile).not.toContain('>Write</button>');
    expect(noFile).toContain('There is no E1282.md yet: write the syllabus first, or copy the plan.');
    // No public-website setting here: that list is the Public website tab's.
    expect(out).not.toContain('kept off');
    expect(out).not.toContain('open_course_site');
  });
  it('materials settings releases every file when nothing is withheld', () => {
    const none = new StaticFiles({}, {}, { [`${COURSE_ORG}/course-materials-f2026`]: ['SYLLABUS.md', 'lectures/01/slides.html'] });
    const out = html(<MaterialsScreen {...cp} files={none} entry="course-materials-f2026" />);
    expect(out).toMatch(/<span class="ft-name">SYLLABUS.md<\/span><span class="chip ">released to students<\/span>/);
    expect(out).toContain('aria-label="Withhold SYLLABUS.md"');
    expect(out).not.toContain('class="file-list"');
    expect(out).toContain('0 never copied (by default) · 0 withheld · 2 released');
  });
  it('the public website edits course.yml and asks for the confirmation the engine’s missing preview needs', () => {
    const out = html(<WebsiteScreen {...cp} />);
    expect(out).toContain('Public OpenCourse website <span class="chip">Beta</span>');
    expect(out).toContain('Beta: leave this off for now - work in progress.');
    expect(out).toContain('<button class="btn" type="button">Publish website</button>');
    expect(out).toContain(`<a class="btn quiet" href="https://github.com/${COURSE_ORG}/.github/edit/main/course.yml" target="_blank" rel="noopener" title="${COURSE_ORG}/.github/course.yml">Edit the file directly`);
    expect(out).not.toContain('New semester');
    // No course.yml yet: Create the file on GitHub opens GitHub's new-file page with the name filled in.
    expect(html(<WebsiteScreen {...cp} files={new StaticFiles({}, {}, {})} />)).toContain(`href="https://github.com/${COURSE_ORG}/.github/new/main?filename=course.yml"`);
    expect(out).toContain('Source materials');
    // One selection editor: the kind chips, and the tree's lines leave a folder out.
    expect(out).toMatch(/<span class="ft-name">labs\/<\/span><span class="chip amber">left out<\/span>/);
    expect(out).toMatch(/Lecture: In · 1/);
    expect(out).toContain('Reading lists');
    expect(out).toContain('Keep the website updated');
    expect(out).not.toContain('Lives in');
    expect(out).toContain('href="#details"');
  });
});
