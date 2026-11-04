// @vitest-environment happy-dom
// The save paths clicked through against a fake GitHub: the Overview form (its schedule entry,
// dates and inline run settings in one commit), the schedule's new assignment entry, the marks
// grid, and the Students grid (students.csv, and a relink in the engine's roster).

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Assignment, Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { AssignmentScreen } from '../src/screens/Assignments';
import { MaterialsScreen } from '../src/screens/CourseEdit';
import { StudentsScreen } from '../src/screens/People';
import { ScheduleScreen } from '../src/screens/Schedule';
import type { CohortProps } from '../src/screens/types';
import { parseSearch, scheduleNewHref } from '../src/router';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';
import { rosterFiles } from './roster';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const STATUS = example as unknown as Status;
const solo = STATUS.assignments![0];
const team: Assignment = { ...solo, slug: 'assignment-3', title: 'Group project', template: 'assignment-3-f2026', state: 'marking', units: 1, submissions: 1, teams: 1, marks: { filled: 0, total: 1 } };
const ready: Loaded = { kind: 'ready', status: { ...STATUS, assignments: [solo, team] }, sha: 's', stale: [] };

const SCHEDULE = 'assignments:\n  assignment-2:\n    number: 2\n    template: assignment-2-f2026\n    handout_datetime: 2026-09-15T10:00\n    due_datetime: 2026-09-27T23:59\n';
const TEAM_SHEET = 'teams:\n  team-alpha:\n    info:\n      submitted: 2026-09-20T20:00\n      days_late: 0\n    feedback_group:\n    members:\n      anna-a:\n        adjustment_individual:\n        feedback_individual:\n        notes_not_shared_with_students:\n    score_group:\n      Q1:\n    feedback_per_question:\n      Q1:\n';

function files(over: Record<string, string> = {}) {
  return new StaticFiles({
    [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE,
    [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'type: individual\n',
    [`${COURSE_ORG}/assignment-3-f2026/grading_config.yml`]: 'type: group\nquestions:\n  Q1: 5\n',
    [`${COURSE_ORG}/assignment-4-f2026/grading_config.yml`]: 'type: individual\n',
    [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: TEAM_SHEET,
    ...over,
  });
}

/** A fake GitHub that accepts every write, except the paths `fail` names (409). */
function github(fail: string[] = []) {
  let n = 0;
  return new FakeGitHub()
    .on('PUT', /\/contents\//, (req: Seen) => (fail.some((f) => req.url.endsWith(f)) ? json({ message: 'Conflict' }, 409) : json({ content: { sha: `b${++n}` }, commit: { sha: `c${n}` } })))
    .on('GET', /\/check-runs/, { check_runs: [] });
}

function envOf(gh: FakeGitHub, f: StaticFiles): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  return { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
}

let root: HTMLElement | null = null;
/** The team row's Final cell (its Score is the calc cell before it). */
const finalOf = () => [...root!.querySelectorAll('tr.team-row td.calc')].pop()!.textContent;

afterEach(() => {
  localStorage.clear(); // the marks grid keeps unsaved marks in this browser
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(env: Env, ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
  return root;
}

const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
async function type(sel: string, value: string, event = 'input') {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event(event, { bubbles: true }));
  });
}
async function click(el: Element) {
  await act(() => (el as HTMLElement).click());
}
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
const buttons = (text: string) => [...root!.querySelectorAll('button')].filter((b) => b.textContent?.trim() === text);
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub, path: string) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith(`/contents/${path}`));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);

const props = (f: StaticFiles, over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files: f, now: NOW, ...over });

describe('Save on the Overview form', () => {
  it('writes the dates and only the run settings set, inline on the schedule entry, as one commit (decision 0038 rule 9)', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(buttons('Change for this assignment')[0]);
    await type('#ov-late_window_days', '3');
    await type('#ov-late_penalty_per_day', '2%');
    await click(button('Save'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 && !root!.textContent!.includes('Checking'));
    expect(gh.seen.filter((s) => s.method === 'PUT').map((s) => s.url.split('/contents/')[1])).toEqual(['schedule.yml']);
    const entry = parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2'];
    expect(entry).toMatchObject({ due_datetime: '2026-09-29T23:59', late_window_days: 3, late_penalty_per_day: '2%' });
    expect(entry.assignment_repo_visibility).toBeUndefined();
  });

  it('warns that a late rule changed after the due date moves the cutoff and penalties, and still writes only the pair', async () => {
    const gh = github(), f = files();
    const past: Loaded = { ...ready, status: { ...ready.status, assignments: [{ ...solo, state: 'marking' }, team] } };
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview', loaded: past })} />);
    expect(root!.textContent).not.toContain('The due date has passed');
    await click(buttons('Change for this assignment')[0]);
    await type('#ov-late_window_days', '3');
    await type('#ov-late_penalty_per_day', '2%');
    expect(root!.textContent).toContain('The due date has passed: this moves the late cutoff and the late penalties of this assignment');
    await click(button('Save'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 && !root!.textContent!.includes('Checking'));
    expect(body(puts(gh, 'schedule.yml')[0])).toBe(`${SCHEDULE}    late_window_days: 3\n    late_penalty_per_day: 2%\n`);
  });

  it('writes the visibility as the file spells it, and the repo name as repo_name', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await click(buttons('Change for this assignment')[1]);
    await type('#ov-visibility', 'public', 'change');
    await type('#ov-repo', 'assignment-2-resit');
    await click(button('Save'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 && !root!.textContent!.includes('Checking'));
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2']).toMatchObject({ assignment_repo_visibility: 'public', repo_name: 'assignment-2-resit' });
  });

  it('says nothing about moved penalties before the due date', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await click(buttons('Change for this assignment')[0]);
    await type('#ov-late_window_days', '3');
    await type('#ov-late_penalty_per_day', '2%');
    expect(root!.textContent).not.toContain('The due date has passed');
  });

  it('saves a schedule carrying blank keys: an unfilled details slot, the seeded archive date', async () => {
    const blank = `${SCHEDULE}    details:\nexams:\n  midterm:\n    event_datetime: 2026-10-20\n    details:\nsemester:\n  archive:\n    type: archive\n    event_datetime:\n`;
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: blank });
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(button('Save'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 || root!.textContent!.includes('Not saved'));
    expect(root!.textContent).not.toContain('Not saved');
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2'].due_datetime).toBe('2026-09-29T23:59');
  });

  it('writes nothing when the schedule would not be valid', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: `${SCHEDULE}colour: red\n` });
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(button('Save'));
    await settle(() => root!.textContent!.includes('Not saved'));
    expect(root!.textContent).toContain('Not saved: the schedule would not be valid');
    expect(gh.seen.filter((s) => s.method === 'PUT')).toEqual([]);
  });
});

describe('the schedule editor’s forms, clicked through (forms mock-up v3)', () => {
  const sched = 'lectures:\n  lecture-1:\n    session: 1\n    event_datetime: 2026-09-08T10:00\nlabs:\n  lab-2:\n    session: 2\n    event_datetime: 2026-09-15T10:00\n    files:\n      - from_course_repo: course-materials-f2026\n        from_path: labs/02_x\n';
  const saved = async (gh: FakeGitHub) => {
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    return parse(body(puts(gh, 'schedule.yml')[0]));
  };

  it('adds a lecture from the Add menu: the next session prefilled, the key generated, its readings twin beside it', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
    expect(q('button[data-kind="lecture"]').textContent).toBe('Lecturesession 2 next');
    await click(q('button[data-kind="lecture"]'));
    expect(q<HTMLInputElement>('#e-session').value).toBe('2');
    expect(button('Give the date, or turn on Date TBC.').disabled).toBe(true);
    await type('#e-title', 'Trees');
    await type('#e-date0', '2026-09-15');
    await type('#e-time0', '10:00');
    await type('#r-guide', '- Breiman (2001).');
    await click(button('Add lecture'));
    const out = await saved(gh);
    expect(out.lectures).toEqual({ 'lecture-1': { session: 1, event_datetime: '2026-09-08T10:00' }, 'lecture-2': { session: 2, title: 'Trees', event_datetime: '2026-09-15T10:00' } });
    expect(out.readings).toEqual({ 'readings-2': { session: 2, event_datetime: '2026-09-15T10:00', reading_guide: '- Breiman (2001).' } });
  });

  it('renumbers a saved lab: the key follows the session', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'lab-2' })} />);
    await type('#e-session', '3');
    await click(button('Save'));
    expect(Object.keys((await saved(gh)).labs)).toEqual(['lab-3']);
  });

  it('flags a duplicate session live, on the field and the Save button', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
    await click(q('button[data-kind="lecture"]'));
    await type('#e-session', '1');
    await type('#e-date0', '2026-09-15');
    expect(root!.textContent).toContain('Lecture 1 is already in the schedule (lecture-1).');
    expect(button('Lecture 1 is already in the schedule (lecture-1).').disabled).toBe(true);
  });

  it('deletes an entry with Delete this entry, the block left a mapping', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'lab-2' })} />);
    await click(button('Delete this entry'));
    await click(buttons('Save').pop()!);
    expect((await saved(gh)).labs).toEqual({});
  });

  it('adds an assignment with its changed run settings inline, in one commit', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new', prefill: { kind: 'template', repo: 'assignment-4-f2026' } })} />);
    await type('#e-hod', '2026-10-01');
    await type('#e-due', '2026-10-10');
    await type('#e-lw', '3');
    await click(button('Add assignment'));
    const out = await saved(gh);
    expect(out.assignments['assignment-3']).toEqual({ number: 3, template: 'assignment-4-f2026', handout_datetime: '2026-10-01T14:00', due_datetime: '2026-10-10T23:59', late_window_days: 3 });
    await settle(() => false);
    expect(gh.seen.filter((s) => s.method === 'PUT').map((s) => s.url.split('/contents/')[1])).toEqual(['schedule.yml']);
  });

  it('opens a new release with the Files panel’s picks and destinations prefilled (Schedule on the calendar)', async () => {
    const href = scheduleNewHref(COHORT_ORG, [
      { repo: 'course-materials-f2026', path: 'labs/week01', toRepo: 'materials', toPath: 'labs/week01' },
      { repo: 'lecture-code-f2026', path: 'demo.py', toRepo: 'lecture-code', toPath: 'code/demo.py' },
    ]);
    expect(href.endsWith('#schedule-new')).toBe(true);
    const prefill = parseSearch(href.split('#')[0]).prefill;
    expect(prefill).toEqual({ kind: 'files', rows: [
      { repo: 'course-materials-f2026', path: 'labs/week01', toRepo: 'materials', toPath: 'labs/week01' },
      { repo: 'lecture-code-f2026', path: 'demo.py', toRepo: 'lecture-code', toPath: 'code/demo.py' },
    ] });
    const gh = github(), f = files();
    // The semester's default repo and the source's own path are left blank in the entry.
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new', prefill })} />);
    expect(root!.textContent).toContain('Release supporting files');
    await type('#e-title', 'Week 1 lab');
    await type('#e-date0', '2026-10-01');
    await type('#e-time0', '09:00');
    await click(button('Add'));
    const out = await saved(gh);
    expect(Object.values(out.assets)).toEqual([expect.objectContaining({
      title: 'Week 1 lab',
      files: [
        { from_course_repo: 'course-materials-f2026', from_path: 'labs/week01' },
        { from_course_repo: 'lecture-code-f2026', from_path: 'demo.py', to_semester_repo: 'lecture-code', to_path: 'code/demo.py' },
      ],
    })]);
  });
});

// assignment-3, saved with no hand out date.
const MANUAL = `${SCHEDULE}  assignment-3:\n    number: 3\n    template: assignment-4-f2026\n    due_datetime: 2026-10-10T23:59\n`;
const asked = () => root!.querySelector('.modal')?.textContent ?? '';
// The Schedule editor's hand-out choice: `hand` (By hand, later) or `time`.
const handOut = (how: 'hand' | 'time') => type('#e-how', how, 'change');
// assignment-2 not out yet (scheduled 15 Sep in the fixture schedule, before its tick).
const declared: Loaded = { ...ready, status: { ...ready.status, assignments: [{ ...solo, state: 'declared' }, team] } };

describe('Save on an assignment entry handed out by hand', () => {
  async function addManual(gh: FakeGitHub, f: StaticFiles) {
    const env = envOf(gh, f);
    await mount(env, <ScheduleScreen {...props(f, { entry: 'new', prefill: { kind: 'template', repo: 'assignment-4-f2026' } })} />);
    await handOut('hand');
    await type('#e-due', '2026-10-10');
    await click(button('Add assignment'));
    return env;
  }
  const dispatched = (gh: FakeGitHub) => gh.seen.filter((x) => x.method === 'POST');

  it('asks before saving a new entry with no hand out date, and Cancel writes nothing', async () => {
    const gh = github(), f = files();
    await addManual(gh, f);
    expect(asked()).toContain('Hand out now?');
    expect(asked()).toContain('You chose to hand out by hand. Until it is handed out students see no brief and there is no mark sheet.');
    expect(buttons('Save and hand out now')).toHaveLength(1);
    await click(button('Cancel'));
    await settle(() => false);
    expect(root!.querySelector('.modal')).toBeNull();
    expect(gh.seen.filter((x) => x.method === 'PUT')).toEqual([]);
  });

  it('Save, hand out later writes the entry with no handout_datetime and hands nothing out', async () => {
    const gh = github(), f = files();
    const env = await addManual(gh, f);
    await click(button('Save, hand out later'));
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const entry = parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-3'];
    expect(entry.due_datetime).toBe('2026-10-10T23:59');
    expect(entry).not.toHaveProperty('handout_datetime');
    expect(env.ops.current.value).toBeNull();
  });

  it('Save and hand out now saves, then opens Hand out on the saved entry without running it', async () => {
    const gh = github(), f = files();
    const env = await addManual(gh, f);
    await click(button('Save and hand out now'));
    await settle(() => env.ops.current.value !== null);
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
    const cur = env.ops.current.value!;
    expect([cur.def.op, cur.def.key, cur.def.args, cur.phase]).toEqual(['assignment.handout_now', 'assignment-3', { course_source_repo: 'assignment-4-f2026', assignment: 'assignment-3' }, 'ready']);
    expect(cur.def.verb).toBe('Hand out to every student');
    expect(dispatched(gh)).toEqual([]);
  });

  it('opens no hand out when the save fails', async () => {
    const gh = github(['schedule.yml']), f = files();
    const env = await addManual(gh, f);
    await click(button('Save and hand out now'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 && !root!.textContent!.includes('Saving'));
    expect(root!.querySelector('#e-due')).not.toBeNull(); // the draft is kept
    expect(env.ops.current.value).toBeNull();
  });

  it('offers only Save, hand out later when two entries newly have no date', async () => {
    const gh = github(), f = files();
    const env = envOf(gh, f);
    await mount(env, <ScheduleScreen {...props(f, { entry: 'assignment-2', loaded: declared })} />);
    await handOut('hand');
    await act(() => render(<EnvCtx.Provider value={env}><ScheduleScreen {...props(f, { entry: 'new', prefill: { kind: 'template', repo: 'assignment-4-f2026' }, loaded: declared })} /></EnvCtx.Provider>, root!));
    await handOut('hand');
    await type('#e-due', '2026-10-10');
    await click(button('Add assignment'));
    expect(asked()).toContain('Hand out now?');
    expect(buttons('Save and hand out now')).toHaveLength(0);
    expect(buttons('Save, hand out later')).toHaveLength(1);
    expect(asked()).toContain('Until they are handed out');
  });

  it('asks when an edit drops the hand out date of an assignment not out yet, and only then', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'assignment-2', loaded: declared })} />);
    await handOut('hand');
    await click(buttons('Save')[0]);
    expect(asked()).toContain('Hand out now?');
    expect(asked()).toContain('Until it is handed out');
    render(null, root!);
    // Already out (the fixture's assignment-2 is open): its copies exist, so nothing to ask.
    await mount(envOf(github(), f), <ScheduleScreen {...props(f, { entry: 'assignment-2' })} />);
    await handOut('hand');
    await click(buttons('Save')[0]);
    expect(root!.querySelector('.modal')).toBeNull();
  });

  it('does not ask again for an entry already handed out by hand', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: MANUAL });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'assignment-3' })} />);
    await type('#e-due', '2026-10-12');
    await click(buttons('Save')[0]);
    expect(root!.querySelector('.modal')).toBeNull();
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-3'].due_datetime).toBe('2026-10-12T23:59');
  });
});

describe('Save on the Overview form, switched to hand out by hand', () => {
  async function switchToManual(gh: FakeGitHub, f: StaticFiles) {
    const env = envOf(gh, f);
    await mount(env, <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview', loaded: declared })} />);
    await click(root!.querySelectorAll('input[name="ov-ho"]')[1]);
    await click(button('Save'));
    return env;
  }

  it('asks the Schedule’s question, and Cancel writes nothing', async () => {
    const gh = github(), f = files();
    await switchToManual(gh, f);
    expect(asked()).toContain('Hand out now?');
    expect(asked()).toContain('You chose to hand out by hand. Until it is handed out students see no brief and there is no mark sheet.');
    await click(button('Cancel'));
    await settle(() => false);
    expect(gh.seen.filter((x) => x.method === 'PUT')).toEqual([]);
  });

  it('Save and hand out now drops the date, then opens Hand out on this entry', async () => {
    const gh = github(), f = files();
    const env = await switchToManual(gh, f);
    await click(button('Save and hand out now'));
    await settle(() => env.ops.current.value !== null);
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2']).not.toHaveProperty('handout_datetime');
    const cur = env.ops.current.value!;
    expect([cur.def.op, cur.def.key, cur.def.args, cur.phase]).toEqual(['assignment.handout_now', 'assignment-2', { course_source_repo: 'assignment-2-f2026', assignment: 'assignment-2' }, 'ready']);
    expect(gh.seen.filter((x) => x.method === 'POST')).toEqual([]);
  });

  it('Save, hand out later only saves; an edit that keeps a date asks nothing', async () => {
    const gh = github(), f = files();
    const env = await switchToManual(gh, f);
    await click(button('Save, hand out later'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 && !root!.textContent!.includes('Checking'));
    expect(env.ops.current.value).toBeNull();
    render(null, root!);
    const gh2 = github(), f2 = files();
    await mount(envOf(gh2, f2), <AssignmentScreen {...props(f2, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(button('Save'));
    expect(root!.querySelector('.modal')).toBeNull();
  });
});

describe('Hand out now beside a saved entry handed out by hand', () => {
  const waiting: Assignment = { ...solo, slug: 'assignment-3', title: 'Quiz', template: 'assignment-4-f2026', state: 'declared', handout: null, teams: null };
  const loaded = (a: Assignment): Loaded => ({ ...ready, status: { ...ready.status, assignments: [solo, a] } });
  const handOutNow = () => buttons('Hand out now').length;

  it('shows on the Schedule entry and the Overview form while the engine reports it declared', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: MANUAL });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'assignment-3', loaded: loaded(waiting) })} />);
    expect(handOutNow()).toBe(1);
    // Its schedule row is tagged too.
    expect(q('li[data-entry="assignment-3"]').textContent).toContain('Manual hand-out: not handed out yet');
    // A draft that leaves the manual choice is not what schedule.yml says: no button.
    await handOut('time');
    expect(handOutNow()).toBe(0);
    render(null, root!);
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'overview', loaded: loaded(waiting) })} />);
    expect(q('#run-settings').textContent).toContain('Hand out now');
  });

  it('does not show once handed out, nor for a new entry', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: MANUAL });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'assignment-3', loaded: loaded({ ...waiting, state: 'open', handout: '2026-09-20T10:00:00+02:00' }) })} />);
    expect(handOutNow()).toBe(0);
    render(null, root!);
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new', prefill: { kind: 'template', repo: 'assignment-4-f2026' }, loaded: loaded(waiting) })} />);
    await handOut('hand');
    expect(handOutNow()).toBe(0);
  });
});

describe('Save on the marks grid', () => {
  it('writes the team’s score and its feedback per question', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await click(q('button[aria-label="Open team feedback for team-alpha"]'));
    await type('textarea[aria-label="Feedback on Q1 for team-alpha"]', 'Clear proof.');
    await click(q('button[aria-label="Show the members of team-alpha"]'));
    await type('input[aria-label="Adjustment for anna-a"]', '1');
    await click(button('Save marks'));
    await settle(() => puts(gh, 'grading_sheets/assignment-3.yml').length > 0);
    const sheet = parse(body(puts(gh, 'grading_sheets/assignment-3.yml')[0]));
    expect(sheet.teams['team-alpha'].score_group).toEqual({ Q1: 4 });
    expect(sheet.teams['team-alpha'].feedback_per_question).toEqual({ Q1: 'Clear proof.' });
    expect(sheet.teams['team-alpha'].members['anna-a'].adjustment_individual).toBe(1);
  });
});

describe('the late penalty on the marks grid', () => {
  const LATE = TEAM_SHEET.replace('days_late: 0', 'days_late: 2');
  const late = () => files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: LATE });
  const BOX = 'input[aria-label="Late penalty override for team-alpha, as a percentage"]';
  const tick = () => q<HTMLInputElement>('input.late-switch');
  const saved = async (gh: FakeGitHub) => {
    await click(button('Save marks'));
    await settle(() => puts(gh, 'grading_sheets/assignment-3.yml').length > 0);
    return parse(body(puts(gh, 'grading_sheets/assignment-3.yml')[0])).teams['team-alpha'];
  };

  it('offers nothing on a unit that is not late', async () => {
    const f = files();
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(root!.querySelector('.late-switch')).toBeNull();
    expect(root!.querySelector(BOX)).toBeNull();
  });

  it('unticking waives it, and the total shows the mark without it', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(tick().checked).toBe(true);
    expect(tick().getAttribute('role')).toBe('switch');
    expect(tick().title).toBe('Apply the late penalty');
    expect(q('tr.team-row td.pen .val').textContent).toBe('−20%');
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await click(tick());
    expect(tick().checked).toBe(false);
    // Waived: the figure forgiven stays, struck through, and the override box is off.
    expect(q('tr.team-row td.pen .val.off').textContent).toBe('−20%');
    expect(q<HTMLInputElement>(BOX).disabled).toBe(true);
    expect(finalOf()).toBe('4');
    expect((await saved(gh)).late_penalty).toBe('waive');
  });

  it('an override is the whole penalty and keeps it applied', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await type(BOX, '25');
    expect(tick().checked).toBe(true);
    expect(q('tr.team-row td.pen .val').textContent).toBe('−25%');
    expect(q(BOX).classList.contains('set')).toBe(true);
    expect(finalOf()).toBe('3');
    expect((await saved(gh)).late_penalty).toBe('25%');
  });

  it('clearing the override or re-ticking on a sheet with no late_penalty writes nothing', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '25');
    await type(BOX, '');
    expect(tick().checked).toBe(true);
    expect(root!.textContent).toContain('No unsaved changes');
    await click(tick());
    await click(tick());
    expect(tick().checked).toBe(true);
    expect(root!.textContent).toContain('No unsaved changes');
  });

  it('ticking a unit that carries a late_penalty writes apply', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: LATE.replace('    feedback_group:\n', '    late_penalty: waive\n    feedback_group:\n') });
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(tick().checked).toBe(false);
    await click(tick());
    expect((await saved(gh)).late_penalty).toBe('apply');
  });

  it('unticking an override waives it', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '25');
    await click(tick());
    expect(q<HTMLInputElement>(BOX).value).toBe('');
    expect((await saved(gh)).late_penalty).toBe('waive');
  });

  it('an override of 0 waives it, and the tickbox follows', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '0');
    expect(tick().checked).toBe(false);
    expect(q('tr.team-row td.pen .val.off')).not.toBeNull();
    expect((await saved(gh)).late_penalty).toBe('waive');
  });

  it('shows nothing on an on-time unit, whatever its late_penalty says', async () => {
    const f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: TEAM_SHEET.replace('    feedback_group:\n', '    late_penalty: half\n    feedback_group:\n') });
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(q('tr.team-row td.pen').textContent).toBe('');
    expect(root!.querySelector(BOX)).toBeNull();
  });

  it('offers no penalty controls when the assignment has no late rule', async () => {
    const f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: LATE.replace('    feedback_group:\n', '    late_penalty: 15%\n    feedback_group:\n') });
    const noRule: Loaded = { ...ready, status: { ...ready.status!, assignments: [solo, { ...team, settings: {} }] } } as Loaded;
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks', loaded: noRule })} />);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    expect(root!.querySelector('.late-switch')).toBeNull();
    expect(root!.querySelector(BOX)).toBeNull();
    // The engine ignores the key: nothing comes off.
    expect(finalOf()).toBe('4');
  });

  it('says so beside a penalty nobody can apply', async () => {
    const f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: LATE.replace('    feedback_group:\n', '    late_penalty: half\n    feedback_group:\n') });
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(q<HTMLInputElement>(BOX).value).toBe('half');
    expect(q('tr.team-row td.pen').textContent).toContain('held until then');
  });
});

describe('Save on the Students grid', () => {
  const ROSTER = 'email,name,role,team:4,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna Adams,enrolled,geneva,anna-a,101,SECRET1,2026-09-02T09:30:00Z\nben@x.org,Ben Baker,enrolled,,,,SECRET2,2026-09-02T09:30:00Z\n';
  const roster = () => files(rosterFiles(COHORT_ORG, ROSTER));
  const HANDLE = 'input[aria-label="GitHub handle, line 2"]';

  it('re-points a joined student at their new account in the engine’s roster, checked on GitHub first, students.csv untouched', async () => {
    const gh = github().on('GET', '/users/anna-new', { login: 'anna-new' }), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(q('button[aria-label="Edit roster"]'));
    // Only a joined row's handle is editable: the next sync moves the student to it.
    expect(root!.querySelector('input[aria-label="GitHub handle, line 3"]')).toBeNull();
    await type(HANDLE, 'anna-new');
    await click(button('Save 1 change'));
    await settle(() => puts(gh, '.system/roster.csv').length > 0);
    expect(puts(gh, 'students.csv')).toEqual([]);
    expect(body(puts(gh, '.system/roster.csv')[0])).toBe('email,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,anna-new,101,SECRET1,2026-09-02T09:30:00Z\nben@x.org,,,SECRET2,2026-09-02T09:30:00Z\n');
  });

  it('shows inputs only after Edit roster, and keeps unsaved edits through Done editing', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    expect(root!.querySelector('input[aria-label="Email, line 2"]')).toBeNull();
    expect(root!.querySelector('.roster-table.editing')).toBeNull();
    await click(q('button[aria-label="Edit roster"]'));
    expect(root!.querySelector('.roster-table.editing')).not.toBeNull();
    await type('input[aria-label="Name, line 3"]', 'Ben B. Baker');
    await click(q('button[aria-label="Done editing"]'));
    expect(root!.querySelector('input[aria-label="Name, line 3"]')).toBeNull();
    expect(root!.textContent).toContain('Ben B. Baker');
    await click(button('Save 1 change'));
    await settle(() => puts(gh, 'students.csv').length > 0);
    expect(body(puts(gh, 'students.csv')[0])).toBe('email,name,role,team:4\nanna@x.org,Anna Adams,enrolled,geneva\nben@x.org,Ben B. Baker,enrolled,\n');
  });

  it('edits a team column like the instructor’s other cells (decision 0039 rule 4)', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    expect(root!.textContent).toContain('yours: team:4');
    await click(q('button[aria-label="Edit roster"]'));
    const cell = [...root!.querySelectorAll<HTMLInputElement>('.roster-table input')].find((i) => i.getAttribute('aria-label')?.startsWith('Team') && i.getAttribute('aria-label')?.endsWith('line 3'))!;
    await act(() => { cell.value = 'geneva'; cell.dispatchEvent(new Event('input', { bubbles: true })); });
    await click(button('Save 1 change'));
    await settle(() => puts(gh, 'students.csv').length > 0);
    expect(body(puts(gh, 'students.csv')[0])).toBe('email,name,role,team:4\nanna@x.org,Anna Adams,enrolled,geneva\nben@x.org,Ben Baker,enrolled,geneva\n');
  });

  it('opens the grid for editing when a student is added', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(button('Add a student'));
    expect(root!.querySelector('input[aria-label="Email, line 4"]')).not.toBeNull();
    expect(q('button[aria-label="Done editing"]')).toBeTruthy();
  });

  it('writes nothing for a handle no GitHub account has', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(q('button[aria-label="Edit roster"]'));
    await type(HANDLE, 'nobody-here');
    await click(button('Save 1 change'));
    await settle(() => root!.textContent!.includes('There is no GitHub account'));
    expect(root!.textContent).toContain('There is no GitHub account called nobody-here.');
    expect(gh.seen.filter((s) => s.method === 'PUT')).toEqual([]);
  });
});

describe('Save on a materials repo’s settings starts Refresh', () => {
  const MAT = 'course-materials-f2026';
  const DISPATCH = `/repos/${COURSE_ORG}/.github/actions/workflows/console.yml/dispatches`;
  const mat = () => new StaticFiles({ [`${COURSE_ORG}/${MAT}/.releaseignore`]: '# seeded, every line a comment\n' }, {}, { [`${COURSE_ORG}/${MAT}`]: ['SYLLABUS.md', 'labs/01/lab.pdf'] });
  const cp = (f: StaticFiles, c: Course = course) => ({ course: c, loaded: { kind: 'absent' } as Loaded, cohortStates: {}, files: f, now: NOW, entry: MAT });
  // The Files panel, whose Withhold view holds the .releaseignore Save.
  const withheldSave = () => root!.querySelector<HTMLElement>('section.files-panel')!;
  const dispatches = (gh: FakeGitHub) => gh.seen.filter((s) => s.method === 'POST' && s.url.endsWith(DISPATCH));

  async function saveWithheld(gh: FakeGitHub, c: Course = course) {
    const env = envOf(gh, mat());
    await mount(env, <MaterialsScreen {...cp(env.files as StaticFiles, c)} />);
    await click(withheldSave().querySelector<HTMLButtonElement>('#files-tab-withhold')!);
    const save = [...withheldSave().querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Save')!;
    await click(save);
    await settle(() => /checklist updates/.test(withheldSave().textContent ?? ''));
    return env;
  }

  it('saves the review, then dispatches the Refresh op once, and says the checklist catches up', async () => {
    const gh = github().on('POST', DISPATCH, { workflow_run_id: 77, run_url: 'u', html_url: 'h' });
    const env = await saveWithheld(gh);
    expect(body(puts(gh, '.releaseignore')[0])).toContain('# Reviewed: nothing withheld.');
    expect(dispatches(gh)).toHaveLength(1);
    const req = JSON.parse((dispatches(gh)[0].body as { inputs: { request: string } }).inputs.request);
    expect(req).toMatchObject({ op: 'semester.check', course_org: COURSE_ORG, semester_org: COHORT_ORG, preview: false });
    expect(withheldSave().textContent).toContain('Saved. The checklist updates in about a minute.');
    // The Refresh runs in the bar, not over the screen.
    expect(env.ops.current.value).toMatchObject({ phase: 'running', min: true });
    env.ops.reset();
  });

  it('still saves when the Refresh is refused, and leaves it to the next Refresh', async () => {
    const gh = github().on('POST', DISPATCH, () => json({ message: 'Must have admin rights' }, 403));
    const env = await saveWithheld(gh);
    expect(puts(gh, '.releaseignore')).toHaveLength(1);
    expect(withheldSave().textContent).toContain('Saved. The checklist updates at the next Refresh.');
    expect(env.ops.current.value).toBeNull();
  });

  it('starts nothing for a course with no semester', async () => {
    const gh = github();
    await saveWithheld(gh, { ...course, cohorts: [] });
    expect(dispatches(gh)).toEqual([]);
    expect(withheldSave().textContent).toContain('Saved. The checklist updates at the next Refresh.');
  });

  it('starts nothing after the syllabus choice: the course’s checklist reads the repo, not semester.yml', async () => {
    const gh = github().on('POST', DISPATCH, { workflow_run_id: 78, run_url: 'u', html_url: 'h' });
    const f = new StaticFiles({ [`${COHORT_ORG}/semester-config/semester.yml`]: 'student_site:\n  syllabus: SYLLABUS.md\n' }, {}, { [`${COURSE_ORG}/${MAT}`]: ['SYLLABUS.md', 'PLAN.md'] });
    await mount(envOf(gh, f), <MaterialsScreen {...cp(f)} />);
    const sylSave = () => [...root!.querySelectorAll('section')].find((s) => s.querySelector('h2')?.textContent?.startsWith('Syllabus'))!;
    await type('#m-syl', 'PLAN.md', 'change');
    await click([...sylSave().querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Save')!);
    await settle(() => puts(gh, 'semester.yml').length > 0 && /Saved|Valid/.test(sylSave().textContent ?? ''));
    expect(body(puts(gh, 'semester.yml')[0])).toContain('syllabus: PLAN.md');
    expect(dispatches(gh)).toEqual([]);
    expect(sylSave().textContent).not.toContain('checklist updates');
  });
});
