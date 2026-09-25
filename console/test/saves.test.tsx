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
import { StaticFiles } from '../src/model/files';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Assignment, Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { AssignmentScreen } from '../src/screens/Assignments';
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
    expect(Object.keys(parse(body(puts(gh, 'schedule.yml')[0])).assignments)).toEqual(['assignment-2', 'assignment-4']);
    expect(parse(body(puts(gh, 'assignments.yml')[0])).assignments).toEqual({ 'assignment-4': { late_window_days: 3, late_penalty_per_day: '2%' } });
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
    expect(Object.keys(parse(text).assignments)).toEqual(['assignment-2', 'assignment-4']);
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
