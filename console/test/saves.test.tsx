// @vitest-environment happy-dom
// The save paths clicked through against a fake GitHub: the Overview form (schedule.yml, then
// assignments.yml), the schedule's new assignment entry (and its retry after a failed second
// commit), and the marks grid.

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
import { StudentsScreen } from '../src/screens/People';
import { ScheduleScreen } from '../src/screens/Schedule';
import type { CohortProps } from '../src/screens/types';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';

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

const SCHEDULE = 'timezone: Europe/Berlin\nassignments:\n  assignment-2:\n    course_source_repo: assignment-2-f2026\n    handout_datetime: 2026-09-15T10:00\n    due_datetime: 2026-09-27T23:59\n';
const ASSIGNMENTS = '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n  late_penalty_per_day: 5%\n';
const TEAM_SHEET = 'teams:\n  team-alpha:\n    info:\n      submitted: 2026-09-20T20:00\n      days_late: 0\n    feedback_group:\n    members:\n      anna-a:\n        adjustment_individual:\n        feedback_individual:\n        notes_not_shared_with_students:\n    score_group:\n      Q1:\n    feedback_per_question:\n      Q1:\n';

function files(over: Record<string, string> = {}) {
  return new StaticFiles({
    [`${COHORT_ORG}/semester-config/schedule.yml`]: SCHEDULE,
    [`${COHORT_ORG}/semester-config/assignments.yml`]: ASSIGNMENTS,
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
afterEach(() => {
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
  it('writes the schedule, then only the run settings set, into assignments.yml', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(buttons('Change for this assignment')[0]);
    await type('#ov-late_window_days', '3');
    await type('#ov-late_penalty_per_day', '2%');
    await click(button('Save'));
    await settle(() => puts(gh, 'assignments.yml').length > 0 && !root!.textContent!.includes('Checking'));
    const order = gh.seen.filter((s) => s.method === 'PUT').map((s) => s.url.split('/contents/')[1]);
    expect(order).toEqual(['schedule.yml', 'assignments.yml']);
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2'].due_datetime).toBe('2026-09-29T23:59');
    expect(parse(body(puts(gh, 'assignments.yml')[0])).assignments).toEqual({ 'assignment-2': { late_window_days: 3, late_penalty_per_day: '2%' } });
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
    await settle(() => puts(gh, 'assignments.yml').length > 0 && !root!.textContent!.includes('Checking'));
    expect(gh.seen.filter((x) => x.method === 'PUT').map((x) => x.url.split('/contents/')[1])).toEqual(['assignments.yml']);
    expect(body(puts(gh, 'assignments.yml')[0])).toBe(`${ASSIGNMENTS}assignments:\n  assignment-2:\n    late_window_days: 3\n    late_penalty_per_day: 2%\n`);
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
    const blank = `${SCHEDULE}    details:\nevents:\n  midterm:\n    kind: exam\n    event_datetime: 2026-10-20\n    details:\narchive:\n  event_datetime:\n`;
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: blank });
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(button('Save'));
    await settle(() => puts(gh, 'schedule.yml').length > 0 || root!.textContent!.includes('Not saved'));
    expect(root!.textContent).not.toContain('Not saved');
    expect(parse(body(puts(gh, 'schedule.yml')[0])).assignments['assignment-2'].due_datetime).toBe('2026-09-29T23:59');
  });

  it('says the dates were saved when the run settings commit fails', async () => {
    const gh = github(['assignments.yml']), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(buttons('Change for this assignment')[1]);
    await type('#ov-visibility', 'public', 'change');
    await click(button('Save'));
    await settle(() => root!.textContent!.includes('Dates saved'));
    expect(root!.textContent).toContain('Dates saved; run settings not saved:');
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
  });

  it('writes nothing when assignments.yml would not be valid', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/assignments.yml`]: 'defaults:\n  colour: red\n' });
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-2', tab: 'overview' })} />);
    await type('#ov-dueDate', '2026-09-29');
    await click(buttons('Change for this assignment')[1]);
    await type('#ov-visibility', 'public', 'change');
    await click(button('Save'));
    await settle(() => root!.textContent!.includes('Not saved'));
    expect(root!.textContent).toContain('Not saved: assignments.yml would not be valid');
    expect(gh.seen.filter((s) => s.method === 'PUT')).toEqual([]);
  });
});

describe('Save on the schedule’s new release, its kind inferred from the folder', () => {
  const sched = 'timezone: Europe/Berlin\nreleases:\n  lab-2:\n    event_datetime: 2026-09-15T10:00\n    kind: lab\n    deploy:\n      - course_source_repo: course-materials-f2026\n        course_source_path: labs/02_x\n';
  async function addRelease(gh: FakeGitHub, f: StaticFiles, folder: string, number?: string) {
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
    await click(button('Lecture'));
    await type('#e-type', '', 'change');
    await type('#e-d0-folder', folder);
    await type('#e-date', '2026-10-01');
    if (number !== undefined) await type('#e-num', number);
    await click(buttons('Save')[0]);
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    return parse(body(puts(gh, 'schedule.yml')[0])).releases;
  }

  it('prefills the next lab number and keys the entry lab-<n>, never a lecture', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    const out = await addRelease(gh, f, 'labs/03_trees');
    expect(Object.keys(out)).toEqual(['lab-2', 'lab-3']);
    expect(out['lab-3'].number).toBe(3);
    expect(out['lab-3'].kind).toBeUndefined();
  });

  it('keys a silent lecture with its number cleared `lecture`, with no number', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
    await click(button('Lecture'));
    await type('#e-d0-folder', 'lectures/01_intro');
    await type('#e-date', '2026-10-01');
    await click(root!.querySelector('#e-show')!);
    await type('#e-num', '');
    await click(buttons('Save')[0]);
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const out = parse(body(puts(gh, 'schedule.yml')[0])).releases;
    expect(Object.keys(out)).toEqual(['lab-2', 'lecture']);
    expect(out.lecture).not.toHaveProperty('number');
    expect(out.lecture.show_on_site).toBe(false);
  });

  it('drops the proposed number when a new lab loses its deploy and becomes a session, and keeps a typed one', async () => {
    const add = async (number?: string) => {
      const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
      await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
      await click(button('Lab'));
      expect(q<HTMLInputElement>('#e-num').value).toBe('3');
      if (number !== undefined) await type('#e-num', number);
      await type('#e-title', 'Clinic');
      await click(q('button[aria-label="Remove deploy 1"]'));
      await type('#e-date', '2026-10-01');
      await click(buttons('Save')[0]);
      await settle(() => puts(gh, 'schedule.yml').length > 0);
      const out = parse(body(puts(gh, 'schedule.yml')[0]));
      render(null, root!);
      return out;
    };
    const proposed = await add();
    expect(Object.keys(proposed.releases)).toEqual(['lab-2']);
    expect(Object.keys(proposed.events)).toEqual(['lab-clinic']);
    expect(proposed.events['lab-clinic']).not.toHaveProperty('number');
    const typed = await add('2');
    expect(Object.keys(typed.events)).toEqual(['lab-2-b']);
    expect(typed.events['lab-2-b'].number).toBe(2);
  });

  it('writes no number for a readings folder', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    const out = await addRelease(gh, f, 'readings/week_3');
    // No digit in the key: `readings-1` would join lecture 1 (decision 0013 rule 3).
    expect(Object.keys(out)).toEqual(['lab-2', 'readings']);
    expect(out.readings).not.toHaveProperty('number');
  });

  it('asks readings which lecture they join, prefilled with nothing, and writes the number typed', async () => {
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new' })} />);
    await click(button('Readings'));
    expect(q('#e-num').closest('.field')!.querySelector('.label')!.textContent).toMatch(/^Joins lecture \(optional\)/);
    expect(q<HTMLInputElement>('#e-num').value).toBe('');
    render(null, root!);
    root!.remove();
    const out = await addRelease(gh, f, 'readings/week_3', '3');
    expect(Object.keys(out)).toEqual(['lab-2', 'readings-3']);
    expect(out['readings-3'].number).toBe(3);
  });

  it('drops the number when a readings entry’s Joins lecture is cleared', async () => {
    const own = `${sched}  readings-week:\n    event_datetime: 2026-09-16T10:00\n    kind: readings\n    number: 2\n    deploy:\n      - course_source_repo: course-materials-f2026\n        course_source_path: readings/week_2\n`;
    const gh = github(), f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: own });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'readings-week' })} />);
    expect(q<HTMLInputElement>('#e-num').value).toBe('2');
    await type('#e-num', '');
    await click(buttons('Save')[0]);
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const out = parse(body(puts(gh, 'schedule.yml')[0])).releases;
    expect(out['readings-week']).not.toHaveProperty('number');
    expect(out['readings-week'].kind).toBe('readings');
  });
});

describe('A release line’s own time, prefilled with the default', () => {
  const sched = 'timezone: Europe/Berlin\nreleases:\n  lab-2:\n    event_datetime: 2026-09-15T10:00\n    kind: lab\n    deploy:\n      - course_source_repo: course-materials-f2026\n        course_source_path: labs/02_x\n';
  const at = () => [q<HTMLInputElement>('#e-d0-at').value, q<HTMLInputElement>('#e-d0-att').value];
  async function open(gh: FakeGitHub) {
    const f = files({ [`${COHORT_ORG}/semester-config/schedule.yml`]: sched });
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'lab-2' })} />);
  }
  async function save(gh: FakeGitHub) {
    await click(buttons('Save')[0]);
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    return parse(body(puts(gh, 'schedule.yml')[0])).releases['lab-2'].deploy[0];
  }

  it('shows an hour before the session, and follows the session while it is the default', async () => {
    const gh = github();
    await open(gh);
    expect(at()).toEqual(['2026-09-15', '09:00']);
    expect(root!.textContent).not.toContain('default: 2026');
    await type('#e-time', '00:30');
    expect(at()).toEqual(['2026-09-14', '23:30']);
  });

  it('writes no deploy_datetime when set back to the default, or the date is cleared', async () => {
    const gh = github();
    await open(gh);
    await type('#e-d0-att', '08:00');
    await type('#e-d0-att', '09:00');
    await type('#e-d0-at', '2026-09-14');
    await type('#e-d0-at', '');
    expect(at()).toEqual(['2026-09-15', '09:00']);
    await type('#e-title', 'Intro');
    expect(await save(gh)).toEqual({ course_source_repo: 'course-materials-f2026', course_source_path: 'labs/02_x' });
  });

  it('writes a changed time', async () => {
    const gh = github();
    await open(gh);
    await type('#e-d0-att', '08:00');
    expect((await save(gh)).deploy_datetime).toBe('2026-09-15T08:00');
  });
});

describe('Save on the schedule’s new assignment entry', () => {
  async function addEntry(gh: FakeGitHub, f: StaticFiles) {
    await mount(envOf(gh, f), <ScheduleScreen {...props(f, { entry: 'new', prefill: 'assignment-4-f2026' })} />);
    await type('#e-hod', '2026-10-01');
    await type('#e-due', '2026-10-10');
    await click(buttons('Change for this assignment')[0]);
    await type('#e-run-late_window_days', '3');
    await type('#e-run-late_penalty_per_day', '2%');
    await click(buttons('Save')[0]);
  }

  it('writes the entry, then its run settings under the same key', async () => {
    const gh = github(), f = files();
    await addEntry(gh, f);
    await settle(() => puts(gh, 'assignments.yml').length > 0);
    expect(Object.keys(parse(body(puts(gh, 'schedule.yml')[0])).assignments)).toEqual(['assignment-2', 'assignment-3']);
    expect(parse(body(puts(gh, 'assignments.yml')[0])).assignments).toEqual({ 'assignment-3': { late_window_days: 3, late_penalty_per_day: '2%' } });
  });

  it('keeps the saved entry when the run settings fail, and a retry adds no second entry', async () => {
    const gh = github(['assignments.yml']), f = files();
    await addEntry(gh, f);
    await settle(() => root!.textContent!.includes('Entry saved without its run settings'));
    expect(root!.textContent).toContain('Entry saved without its run settings:');
    for (const b of buttons('Save')) await click(b);
    await settle(() => false);
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
    const text = (f.file(COHORT_ORG, 'semester-config', 'schedule.yml') as { text: string }).text;
    expect(Object.keys(parse(text).assignments)).toEqual(['assignment-2', 'assignment-3']);
  });
});

describe('Save on the marks grid', () => {
  it('writes the team’s score and its feedback per question', async () => {
    const gh = github(), f = files();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await click(q('button[aria-label="Show feedback per question for team-alpha"]'));
    await type('textarea[aria-label="Feedback on Q1 for team-alpha"]', 'Clear proof.');
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
  const tick = () => q<HTMLInputElement>('.late-apply input');
  const saved = async (gh: FakeGitHub) => {
    await click(button('Save marks'));
    await settle(() => puts(gh, 'grading_sheets/assignment-3.yml').length > 0);
    return parse(body(puts(gh, 'grading_sheets/assignment-3.yml')[0])).teams['team-alpha'];
  };

  it('offers nothing on a unit that is not late', async () => {
    const f = files();
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(root!.querySelector('.late-apply')).toBeNull();
    expect(root!.querySelector(BOX)).toBeNull();
  });

  it('unticking waives it, and the total shows the mark without it', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(tick().checked).toBe(true);
    expect(q('.late-apply').textContent).toMatch(/^Apply late penalty \(.*2 days late\)$/);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await click(tick());
    expect(tick().checked).toBe(false);
    expect(q('tr.team-row td.pen').textContent).toContain('waived');
    expect(q('tr.team-row td.calc').textContent).toBe('4');
    expect((await saved(gh)).late_penalty).toBe('waive');
  });

  it('an override is the whole penalty and keeps it applied', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type('input[aria-label="Q1 (out of 5) for team-alpha"]', '4');
    await type(BOX, '25');
    expect(tick().checked).toBe(true);
    expect(q('tr.team-row td.pen').textContent).toContain('−25% (set by hand)');
    expect(q('tr.team-row td.calc').textContent).toBe('3');
    expect((await saved(gh)).late_penalty).toBe('25%');
  });

  it('clearing the override writes apply back, and unticking an override waives it', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '25');
    await type(BOX, '');
    expect(tick().checked).toBe(true);
    expect((await saved(gh)).late_penalty).toBe('apply');
    const gh2 = github(), f2 = late();
    render(null, root!);
    await mount(envOf(gh2, f2), <AssignmentScreen {...props(f2, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '25');
    await click(tick());
    expect(q<HTMLInputElement>(BOX).value).toBe('');
    expect((await saved(gh2)).late_penalty).toBe('waive');
  });

  it('an override of 0 waives it, and the tickbox follows', async () => {
    const gh = github(), f = late();
    await mount(envOf(gh, f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    await type(BOX, '0');
    expect(tick().checked).toBe(false);
    expect(q('tr.team-row td.pen').textContent).toContain('waived');
    expect((await saved(gh)).late_penalty).toBe('waive');
  });

  it('shows nothing on an on-time unit, whatever its late_penalty says', async () => {
    const f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: TEAM_SHEET.replace('    feedback_group:\n', '    late_penalty: half\n    feedback_group:\n') });
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(q('tr.team-row td.pen').textContent).toBe('');
    expect(root!.querySelector(BOX)).toBeNull();
  });

  it('says so beside a penalty nobody can apply', async () => {
    const f = files({ [`${COHORT_ORG}/semester-config/grading_sheets/assignment-3.yml`]: LATE.replace('    feedback_group:\n', '    late_penalty: half\n    feedback_group:\n') });
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { entry: 'assignment-3', tab: 'marks' })} />);
    expect(q<HTMLInputElement>(BOX).value).toBe('half');
    expect(q('tr.team-row td.pen').textContent).toContain('held until then');
  });
});

describe('Save on the Students grid', () => {
  const ROSTER = 'hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna Adams,enrolled,anna-a,101,SECRET1,2026-09-02T09:30:00Z\nben@x.org,Ben Baker,enrolled,,,SECRET2,2026-09-02T09:30:00Z\n';
  const roster = () => files({ [`${COHORT_ORG}/semester-config/students.csv`]: ROSTER });
  const HANDLE = 'input[aria-label="GitHub handle, line 2"]';

  it('re-points a joined student at their new account, checked on GitHub first', async () => {
    const gh = github().on('GET', '/users/anna-new', { login: 'anna-new' }), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(button('Edit roster'));
    // Only a joined row's handle is editable: the next sync moves the student to it.
    expect(root!.querySelector('input[aria-label="GitHub handle, line 3"]')).toBeNull();
    await type(HANDLE, 'anna-new');
    await click(button('Save 1 change'));
    await settle(() => puts(gh, 'students.csv').length > 0);
    expect(body(puts(gh, 'students.csv')[0])).toContain('anna@x.org,Anna Adams,enrolled,anna-new,101,');
  });

  it('shows inputs only after Edit roster, and keeps unsaved edits through Done editing', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    expect(root!.querySelector('input[aria-label="Email, line 2"]')).toBeNull();
    expect(root!.querySelector('.roster-table.editing')).toBeNull();
    await click(button('Edit roster'));
    expect(root!.querySelector('.roster-table.editing')).not.toBeNull();
    await type('input[aria-label="Name, line 3"]', 'Ben B. Baker');
    await click(button('Done editing'));
    expect(root!.querySelector('input[aria-label="Name, line 3"]')).toBeNull();
    expect(root!.textContent).toContain('Ben B. Baker');
    await click(button('Save 1 change'));
    await settle(() => puts(gh, 'students.csv').length > 0);
    expect(body(puts(gh, 'students.csv')[0])).toContain('ben@x.org,Ben B. Baker,enrolled,');
  });

  it('opens the grid for editing when a student is added', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(button('Add a student'));
    expect(root!.querySelector('input[aria-label="Email, line 4"]')).not.toBeNull();
    expect(button('Done editing')).toBeTruthy();
  });

  it('writes nothing for a handle no GitHub account has', async () => {
    const gh = github(), f = roster();
    await mount(envOf(gh, f), <StudentsScreen {...props(f)} />);
    await click(button('Edit roster'));
    await type(HANDLE, 'nobody-here');
    await click(button('Save 1 change'));
    await settle(() => root!.textContent!.includes('There is no GitHub account'));
    expect(root!.textContent).toContain('There is no GitHub account called nobody-here.');
    expect(puts(gh, 'students.csv')).toEqual([]);
  });
});
