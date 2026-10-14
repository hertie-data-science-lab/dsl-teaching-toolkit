// @vitest-environment happy-dom
// The marking screen (WP-M1): the engine's hold rules, the Return marks figures and preview
// emails, the draft kept in the browser, the conflict-safe save, and the grid's toolbar and keys.

import { readFileSync } from 'node:fs';
import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { holdReasons, insertSnippet, lateRule, markerLines, needsHandDecision, readSnippets, rowsChanged, spread, studentMarks, type HoldUnit } from '../src/model/marks';
import { marksDraft, saveMarksDraft } from '../src/model/prefs';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Assignment, Status } from '../src/model/types';
import { returnMarks } from '../src/ops/defs';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpPanel } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { parseHash } from '../src/router';
import { AssignmentScreen } from '../src/screens/Assignments';
import { PreviewEmails, previewEmails, templateChecks } from '../src/screens/Marking';
import type { CohortProps } from '../src/screens/types';
import example from './fixtures/status.example.json';
import { FakeGitHub, fileBody, json, type Seen } from './fake';
import { StaticFiles } from './staticFiles';
import { rosterFiles } from './roster';

// ------------------------------------------------------------------ the hold rules, golden vectors from the engine's tests

const Q = { Q1: 10, Q2: 10 };
const unit = (score: unknown, people: [string, unknown][], daysLate: unknown = null, latePenalty: unknown = null): HoldUnit => ({
  score, daysLate, latePenalty, people: people.map(([handle, adjustment]) => ({ handle, adjustment })),
});

describe('the hold rules (grades.sheet_hold_reasons, grades.needs_hand_decision)', () => {
  it('holds a word for a score only where a penalty takes something off (test_grading_view)', () => {
    expect(needsHandDecision(unit('pass', [], 2), null, 0.1)).toBe(true);
    expect(needsHandDecision(unit('pass', [], 0), null, 0.1)).toBe(false); // nothing to apply
    expect(needsHandDecision(unit(20, [], 2), null, 0.1)).toBe(false); // a number: arithmetic
    expect(needsHandDecision(unit(null, [], 2), null, 0.1)).toBe(false); // late and not marked yet
    expect(needsHandDecision(unit('pass', [], 2, 'waive'), null, 0.1)).toBe(false);
    expect(needsHandDecision(unit('pass', [], 2, '5%'), null, 0.1)).toBe(true);
    for (const lp of ['waive', '15%', 'half']) expect(needsHandDecision(unit('pass', [], 2, lp), null, null)).toBe(false);
  });

  it('holds every member of a team whose mark is not a number, as one reason (test_a_teams_bad_mark_is_one_fault)', () => {
    expect(holdReasons([unit({ Q1: 'pass' }, [['ada-l', null], ['ben-k', null]])], Q, 0.1)).toEqual({ 'ada-l': 'score', 'ben-k': 'score' });
  });

  it('holds a late unit whose late_penalty nobody can apply, and no on-time one (test_a_late_penalty_nobody_can_apply…)', () => {
    expect(holdReasons([unit({ Q1: 12 }, [['ada-l', null], ['ben-k', null]], 2, 'half'), unit({ Q1: 9 }, [['cy-d', null]], null, 'waive')], Q, 0.1)).toEqual({ 'ada-l': 'late_penalty', 'ben-k': 'late_penalty' });
    const sheet = [unit({ Q1: 12 }, [['ada-l', null]], 0, 'half'), unit({ Q1: 9 }, [['ben-k', null]], null, 'half'), unit({ Q1: 9 }, [['cy-d', null]], 1, 'half')];
    expect(holdReasons(sheet, Q, 0.1)).toEqual({ 'cy-d': 'late_penalty' });
    expect(holdReasons(sheet, Q, null)).toEqual({});
  });

  it('holds a question the assignment never declared, a word processor’s minus, and a handle in two teams', () => {
    expect(holdReasons([unit({ Q5: 3 }, [['ada-l', null]])], Q, 0.1)).toEqual({ 'ada-l': 'question' });
    expect(holdReasons([unit({ Q1: 3 }, [['ada-l', '−3']])], Q, 0.1)).toEqual({ 'ada-l': 'adjustment' });
    expect(holdReasons([unit({ Q1: 3 }, [['Alice', null]]), unit({ Q1: 4 }, [['alice', null]])], Q, 0.1)).toEqual({ alice: 'duplicate' });
    // No late rule: a late_penalty of any kind holds nobody (test_no_late_rule_ignores_any_late_penalty).
    for (const lp of ['waive', '15%', 'half']) expect(holdReasons([unit(20, [['ada-l', null]], 2, lp)], null, null)).toEqual({});
  });

  it('lets what a grader typed outrank the arithmetic', () => {
    expect(holdReasons([unit('pass', [['ada-l', 'x']], 2)], null, 0.1)).toEqual({ 'ada-l': 'adjustment' });
    expect(holdReasons([unit('pass', [['ada-l', null]], 2)], null, 0.1)).toEqual({ 'ada-l': 'penalty' });
  });
});

describe('the figures the marking screen shows', () => {
  it('words the late rule once', () => {
    expect(lateRule(0.1, 10)).toBe('10% of the total per day started, up to 10 days');
    expect(lateRule(0.05, null)).toBe('5% of the total per day started');
    expect(lateRule(null, 10)).toBeNull();
  });

  it('puts the final grades in one line', () => {
    expect(spread([22, 38, 47], 2)).toBe('Final: median 38, range 22–47, 2 held');
    expect(spread([10, 20], 0)).toBe('Final: median 15, range 10–20');
    expect(spread([], 1)).toBe('Final: nothing marked yet, 1 held');
  });

  it('gives a student the whole breakdown: marks per question, score, penalty, adjustment where set, final', () => {
    const base = { team: 'geneva', questions: Q, max: 20, rate: 0.1, daysLate: 0, latePenalty: null, adjustment: 3, feedback: 'Well done.', teamFeedback: 'Clean.', questionFeedback: { Q1: 'Good', Q2: '' } };
    const member = studentMarks({ ...base, group: true, score: { Q1: 8, Q2: 9 } });
    expect(member).toMatchObject({ final: '20', score: { Q1: 8, Q2: 9 }, total: '17', adjustment: '+3', team: 'geneva', teamFeedback: 'Clean.', questionFeedback: [['Q1', 'Good']] });
    const solo = studentMarks({ ...base, group: false, score: { Q1: 8, Q2: null }, daysLate: 2, adjustment: null });
    expect(solo).toMatchObject({ final: '6.4', score: { Q1: 8 }, total: '8', adjustment: '', team: null, teamFeedback: '', penalty: '−20%' });
  });

  it('puts a snippet at the cursor, a space before it where it would run into a word', () => {
    expect(insertSnippet('', 0, 0, 'Label your axes.')).toEqual({ text: 'Label your axes.', caret: 16 });
    expect(insertSnippet('Good.', 5, 5, 'Label your axes.')).toEqual({ text: 'Good. Label your axes.', caret: 22 });
    expect(insertSnippet('Good.\n', 6, 6, 'X')).toEqual({ text: 'Good.\nX', caret: 7 });
    expect(insertSnippet('Good. Bad.', 6, 10, 'Fine.')).toEqual({ text: 'Good. Fine.', caret: 11 });
    expect(readSnippets({ snippets: ['A', '', 3, null, { x: 1 }] })).toEqual(['A', '3']);
    expect(readSnippets({ snippets: 'A' })).toEqual([]);
  });

  it('gives each marker a count, a mean and a median, the unrecorded last', () => {
    const lines = markerLines([
      { marker: 'jan', score: { Q1: 8, Q2: 9 } },
      { marker: 'jan', score: { Q1: 4, Q2: null } },
      { marker: 'kim', score: { Q1: 'pass' } },
      { marker: null, score: { Q1: 6 } },
      { marker: 'kim', score: { Q1: null, Q2: '' } }, // not marked: not counted
    ], Q);
    expect(lines).toEqual([
      { marker: 'jan', count: 2, mean: 10.5, median: 10.5 },
      { marker: 'kim', count: 1, mean: null, median: null },
      { marker: '', count: 1, mean: 6, median: 6 },
    ]);
  });

  it('counts the rows someone else changed', () => {
    const a = 'submissions:\n  ada-l:\n    score_individual: 3\n  ben-k:\n    score_individual: 4\n';
    expect(rowsChanged(a, a)).toBe(0);
    expect(rowsChanged(a, a.replace('4', '5'))).toBe(1);
    expect(rowsChanged(a, `${a}  cy-d:\n    score_individual: 1\n`)).toBe(1);
  });

  it('reads who a preview would email from the preview issue', () => {
    const issue = [
      '### Grades that would change (2)',
      '- **assignment-2** · `ana-lopez` (Ana Lopez) · 40 (was 36)',
      '- **assignment-1** · `ben-okafor` · 12 (new)',
      '',
      '### Students who would be emailed (3)',
      '- `ana-lopez` (Ana Lopez) - a grade changed.',
      '  Their gradebook shows: assignment-2 40',
      '- `ben-okafor` - feedback changed.',
      '  Their gradebook shows: assignment-1 12',
      '- `cy-d` - their first grades email.',
    ].join('\n');
    expect(previewEmails(issue, 'assignment-2')).toEqual(['ana-lopez (mark 36 -> 40)', 'ben-okafor (feedback changed)', 'cy-d (first marks email)']);
    expect(previewEmails('### Students who would be emailed (0)\nNothing - nobody has a new mark to be told about.', 'a')).toEqual([]);
    expect(previewEmails('no sections', 'a')).toBeNull();
  });

  it('keeps a draft with its sha until it is cleared', () => {
    const m = new Map<string, string>();
    const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
    expect(marksDraft('Org', 'grading_sheets/a.yml', store)).toBeNull();
    saveMarksDraft('Org', 'grading_sheets/a.yml', { sha: 's1', at: 5, edits: { '["submissions","ada-l","score_individual"]': 3 } }, store);
    expect(marksDraft('org', 'grading_sheets/a.yml', store)).toEqual({ sha: 's1', at: 5, edits: { '["submissions","ada-l","score_individual"]': 3 } });
    saveMarksDraft('Org', 'grading_sheets/a.yml', null, store);
    expect(marksDraft('Org', 'grading_sheets/a.yml', store)).toBeNull();
  });
});

// ------------------------------------------------------------------ the screen

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const NOW = Date.parse('2026-10-09T10:00:00+02:00'); // after the fixture's late cutoff
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const STATUS = example as unknown as Status;
const solo: Assignment = { ...STATUS.assignments![0], state: 'marking', settings: { late_penalty_per_day: { value: '10%', source: 'institution' }, late_window_days: { value: 10, source: 'institution' } } };
const ready: Loaded = { kind: 'ready', status: { ...STATUS, assignments: [solo] }, sha: 's', stale: [] };
const SHEET_PATH = `${COHORT_ORG}/semester-config/grading_sheets/assignment-2.yml`;
const SHEET = [
  'submissions:',
  '  ada-l:',
  '    info:',
  '      submitted: 2026-09-27T20:00',
  '      days_late: 0',
  '      autograde: 8/10',
  '    score_individual:',
  '      Q1: 8',
  '      Q2: 9',
  '    adjustment_individual:',
  '    feedback_individual: Good.',
  '    notes_not_shared_with_students: Check the plot.',
  '  ben-k:',
  '    info:',
  '      submitted: 2026-09-29T20:00',
  '      days_late: 2',
  '      autograde: 5/10',
  '    score_individual:',
  '      Q1: pass',
  '      Q2:',
  '    adjustment_individual:',
  '    feedback_individual:',
  '    notes_not_shared_with_students:',
  '  cy-d:',
  '    info:',
  '      submitted:',
  '      days_late:',
  '      autograde:',
  '    score_individual:',
  '      Q1:',
  '      Q2:',
  '    adjustment_individual:',
  '    feedback_individual:',
  '    notes_not_shared_with_students:',
  '',
].join('\n');

const CONFIG = 'type: individual\nautograde: true\nformats: [py]\nquestions:\n  Q1: 10\n  Q2: 10\n';
function files(sheet = SHEET, config = CONFIG) {
  return new StaticFiles({
    [`${COHORT_ORG}/semester-config/schedule.yml`]: 'timezone: Europe/Berlin\nassignments:\n  assignment-2:\n    number: 2\n    template: assignment-2-f2026\n',
    [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: config,
    [SHEET_PATH]: sheet,
  });
}

function github(conflictOnce = false, fresh = SHEET) {
  let n = 0, refused = !conflictOnce;
  return new FakeGitHub()
    .on('PUT', /\/contents\//, () => {
      if (!refused) {
        refused = true;
        return json({ message: 'Conflict' }, 409);
      }
      return json({ content: { sha: `b${++n}` }, commit: { sha: `c${n}` } });
    })
    .on('GET', /\/contents\/grading_sheets/, fileBody('grading_sheets/assignment-2.yml', fresh, 'fresh-sha'))
    .on('GET', /\/check-runs/, { check_runs: [] });
}

function envOf(gh: FakeGitHub, f: StaticFiles): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  return { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
}

let root: HTMLElement | null = null;
/** The window's width; the grid's tests run in a wide one unless they say otherwise. */
const windowWidth = (w: number) => Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true });
beforeEach(() => windowWidth(1440));
afterEach(() => {
  // Unmounted first: the screen keeps its unsaved marks as it goes.
  if (root) render(null, root);
  root?.remove();
  root = null;
  localStorage.clear();
});

async function mount(env: Env, ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
}
const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
async function type(sel: string, value: string) {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
const click = (el: Element) => act(() => (el as HTMLElement).click());
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub) => gh.seen.filter((s: Seen) => s.method === 'PUT');
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);
const props = (f: StaticFiles, over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: ready, files: f, now: NOW, entry: 'assignment-2', tab: 'marks', ...over });
const marks = (f = files(), gh = github()) => mount(envOf(gh, f), <AssignmentScreen {...props(f)} />);
const filter = (word: string) => [...root!.querySelectorAll<HTMLButtonElement>('.chip.filter')].find((b) => b.textContent?.startsWith(word))!;

describe('what a template runs at the cutoff (grades.GradingSpec)', () => {
  it('runs the tests only with autograde on, and the completion check as declared, else for a notebook', () => {
    expect(templateChecks({})).toEqual({ tests: false, completion: false });
    expect(templateChecks({ autograde: true, formats: ['py'] })).toEqual({ tests: true, completion: false });
    expect(templateChecks({ formats: ['ipynb', 'pdf'] })).toEqual({ tests: false, completion: true });
    expect(templateChecks({ formats: 'pdf, ipynb' })).toEqual({ tests: false, completion: false });
    expect(templateChecks({ formats: ['ipynb'], completion_check: false })).toEqual({ tests: false, completion: false });
    expect(templateChecks({ formats: ['py'], completion_check: true })).toEqual({ tests: false, completion: true });
    expect(templateChecks({ submit_via: 'shared_dropbox_repo', autograde: true, formats: ['ipynb'] })).toEqual({ tests: false, completion: false });
  });
});

describe('the column help', () => {
  it('words Penalty, Adjustment, Feedback and Completion as approved', async () => {
    await marks(files(SHEET.replace('      autograde: 8/10\n', '      autograde: 8/10\n      completion: Completed\n'), CONFIG.replace('formats: [py]', 'formats: [ipynb]')));
    const pop = (col: string) => q(`button[aria-label="About ${col}"]`).nextElementSibling!.textContent;
    expect(pop('Penalty')).toBe('10% of the total per day started, up to 10 days. Switch it off to waive it, or type your preferred penalty as a percentage to override it.');
    expect(pop('Feedback')).toBe('Students see this with their marks.');
    expect(pop('Completion')).toBe('At the late cutoff the toolkit runs the submitted notebook top to bottom in a clean sandbox and records whether it reached the end. “Completed” means every cell ran without an error; otherwise the first error is named. Information for you, never a mark: a notebook can run through and still be wrong, or stop on a missing package and still be right. “—” where the template has no check.');
    await click(q('button.adj-chip'));
    expect(pop('Adjustment')).toBe('Added to this person’s final grade (plus or minus points).');
  });
});

describe('the marking screen', () => {
  it('counts All, Unmarked, Late and Held, and marks a held row with its reason', async () => {
    await marks();
    expect(['All', 'Unmarked', 'Late', 'Held'].map((w) => filter(w).textContent)).toEqual(['All 3', 'Unmarked 1', 'Late 1', 'Held 1']);
    const ben = [...root!.querySelectorAll('tr.unit-row')].find((r) => r.textContent?.includes('ben-k'))!;
    expect(ben.querySelector('.held-chip')?.textContent).toBe('held');
    expect(ben.textContent).toContain('a question mark that is not a number');
    await click(filter('Held'));
    expect([...root!.querySelectorAll('tr.unit-row b')].map((b) => b.textContent)).toEqual(['ben-k']);
    // Fixing the mark clears the hold as you type.
    await click(filter('All'));
    await type('input[aria-label="Q1 (out of 10) for ben-k"]', '7');
    expect(filter('Held').textContent).toBe('Held 0');
  });

  it('prints “—” for a check the template does not run, even where the sheet still carries its key', async () => {
    // Every unit has `completion:`, but the template is not a notebook and does not declare the check.
    const withKey = SHEET.replace(/( {6}autograde:.*\n)/g, '$1      completion:\n');
    await marks(files(withKey));
    const col = [...q('thead').querySelectorAll('th')].map((h) => h.firstChild?.textContent?.trim());
    expect(col).toContain('Completion');
    const cells = [...root!.querySelectorAll('tr.unit-row')].map((r) => r.querySelectorAll('td.sys')[3].textContent);
    expect(cells).toEqual(['—', '—', '—']);
    root!.remove();
    // Tests the template no longer runs: a dash too, and no tint.
    await marks(files(SHEET, CONFIG.replace('autograde: true', 'autograde: false')));
    expect([...root!.querySelectorAll('tr.unit-row')].map((r) => r.querySelectorAll('td.sys')[2].textContent)).toEqual(['—', '—', '—']);
    expect(root!.querySelector('td.sys.auto')).toBeNull();
    root!.remove();
    // A notebook template runs the check: blank until the cutoff fills it.
    await marks(files(withKey, CONFIG.replace('formats: [py]', 'formats: [ipynb]')));
    expect([...root!.querySelectorAll('tr.unit-row')].map((r) => r.querySelectorAll('td.sys')[3].textContent)).toEqual(['', '', '']);
  });

  it('keeps Tests with a light tint where the template runs them, and hides both columns where nothing does', async () => {
    await marks();
    expect(root!.querySelector('th.sys')!.parentElement!.parentElement!.textContent).toContain('Tests');
    expect(q('td.sys.auto').textContent).toBe('8/10');
    expect(root!.textContent).not.toContain('Completion');
    root!.remove();
    await marks(files(SHEET.replace(/ {6}autograde:.*\n/g, '')));
    expect(q('thead').textContent).not.toContain('Tests');
  });

  it('hides the submission columns for work handed in outside GitHub, and says so', async () => {
    const external = SHEET.replace(/ {4}info:\n( {6}.*\n)+/g, '');
    await marks(files(external));
    expect(root!.textContent).toContain('Submitted outside GitHub: no dates, no late penalty.');
    expect(q('thead').textContent).not.toContain('From the submission');
    expect(button('From the submission ▾')).toBeUndefined();
  });

  it('folds the submission columns to one chip per row', async () => {
    await marks();
    await click(button('From the submission ▾'));
    const ben = [...root!.querySelectorAll('tr.unit-row')].find((r) => r.textContent?.includes('ben-k'))!;
    expect(ben.querySelector('td.sys .chip')!.textContent).toBe('2 days late · 5/10 tests');
  });

  it('moves down a column with Enter in a number box, and the side panel to the next row with Ctrl+Enter in a text box', async () => {
    await marks();
    const box = q<HTMLInputElement>('input[aria-label="Q1 (out of 10) for ada-l"]');
    box.focus();
    await act(() => void box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Q1 (out of 10) for ben-k');
    await click(q('button[aria-label="Open private notes for ada-l"]'));
    const pn = q<HTMLTextAreaElement>('textarea[aria-label="Private notes for ada-l"]');
    expect(document.activeElement).toBe(pn);
    await act(() => void pn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(document.activeElement).toBe(pn);
    await act(() => void pn.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true })));
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Private notes for ben-k');
    expect(q('#marks-drawer-title').textContent).toBe('ben-k');
  });

  it('opens full screen from the corner icon as an overlay, and Escape or the icon closes it, focus back on the icon', async () => {
    await marks();
    const icon = () => q<HTMLButtonElement>('button.full-toggle');
    expect([icon().getAttribute('aria-label'), icon().title]).toEqual(['Full screen', 'Full screen']);
    await click(icon());
    expect(q('.marking').classList.contains('full')).toBe(true);
    expect(q('.marking').getAttribute('role')).toBe('dialog');
    expect(icon().getAttribute('aria-label')).toBe('Exit full screen');
    expect(q('.marking .marks-bar')).not.toBeNull(); // the toolbar stays inside
    await act(() => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(q('.marking').classList.contains('full')).toBe(false);
    expect(document.activeElement).toBe(icon());
    await click(icon());
    await click(icon());
    expect(q('.marking').classList.contains('full')).toBe(false);
  });

  it('leaves full screen on when Escape closes a dialog or a ? popover inside it', async () => {
    await marks();
    const esc = () => act(() => void (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    const isFull = () => q('.marking').classList.contains('full');
    await click(q('button.full-toggle'));
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    await esc();
    expect(root!.querySelector('aside.marks-drawer')).toBeNull();
    expect(isFull()).toBe(true);
    await click(button('Student view'));
    await esc();
    expect(root!.querySelector('.student-view')).toBeNull();
    expect(isFull()).toBe(true);
    await act(() => q<HTMLButtonElement>('button[aria-label="About Feedback"]').focus());
    expect(q<HTMLButtonElement>('button[aria-label="About Feedback"]').nextElementSibling).toBeNull(); // open: at the end of the page
    await esc();
    expect(q<HTMLButtonElement>('button[aria-label="About Feedback"]').nextElementSibling).not.toBeNull();
    expect(isFull()).toBe(true);
    await esc();
    expect(isFull()).toBe(false);
  });

  it('keeps Return marks and Student view above the marking panel, and the toolbar to marking and saving', async () => {
    await marks();
    const block = q('.return-block');
    expect(block.nextElementSibling!.classList.contains('marking')).toBe(true);
    expect(block.querySelector('button.btn.big')!.textContent).toBe('Return marks to 1 student');
    expect(block.querySelector('.hint-btn')!.getAttribute('aria-label')).toBe('What returning marks does');
    const tools = q('.marking .marks-bar').textContent!;
    for (const gone of ['Preview', 'Return marks', 'Student view', 'Next unmarked', 'Open all teams', 'From the submission']) expect(tools).not.toContain(gone);
  });

  it('folds the submission columns from their own heading', async () => {
    await marks();
    const fold = () => q<HTMLButtonElement>('thead th .th-fold');
    expect([fold().textContent, fold().getAttribute('aria-expanded')]).toEqual(['From the submission ▾', 'true']);
    await click(fold());
    expect([fold().textContent, fold().getAttribute('aria-expanded')]).toEqual(['From the submission ▸', 'false']);
    expect(q('thead').textContent).not.toContain('Days late');
    expect(q('thead').textContent).toContain('Score'); // every other column stays
  });

  it('shows a student’s marks page as it will be in a dialog, never the private notes', async () => {
    await marks();
    await click(button('Student view'));
    const dialog = q('[role="dialog"]');
    expect(dialog.querySelector('#modal-title')!.textContent).toBe('Student view');
    await act(() => {
      const s = q<HTMLSelectElement>('#student-pick');
      s.value = 'ada-l';
      s.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const view = q('.student-view');
    expect(view.textContent).toContain('ada-l · Assignment 2: Regression');
    expect([...view.querySelectorAll('th')].map((t) => t.textContent)).toEqual(['Q1', 'Q2', 'Score', 'Submitted', 'Late', 'Final grade']);
    expect(view.textContent).toContain('17 / 20');
    expect([...view.querySelectorAll('td')].map((t) => t.textContent)).toContain('27 Sep 20:00'); // as the grid's Submitted
    expect(view.textContent).toContain('Good.');
    expect(view.textContent).not.toContain('Check the plot.');
    await act(() => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(root!.querySelector('.student-view')).toBeNull();
  });

  it('keeps the grid to marks and numbers: feedback and notes are one line that opens the row’s side panel', async () => {
    const k = `dsl-console-marks-draft:${COHORT_ORG}:grading_sheets/assignment-2.yml`;
    await marks(files(SHEET.replace('    feedback_individual: Good.\n', '    feedback_individual: Good.\n    feedback_per_question:\n      Q2: Tidy.\n')));
    expect(root!.querySelector('table.marks textarea')).toBeNull();
    const ada = () => q<HTMLButtonElement>('button[aria-label="Open feedback for ada-l"]');
    expect(ada().querySelector('.md-line')!.textContent).toBe('Good.');
    expect(ada().querySelector('.qfb-count')!.textContent).toBe('✎ 1');
    expect(root!.querySelector('aside.marks-drawer')).toBeNull();
    await click(ada());
    const panel = q('aside.marks-drawer');
    expect([...panel.querySelectorAll('textarea')].map((t) => t.getAttribute('aria-label'))).toEqual(['Feedback for ada-l', 'Feedback on Q1 for ada-l', 'Feedback on Q2 for ada-l', 'Private notes for ada-l']);
    expect(panel.querySelector<HTMLTextAreaElement>('textarea[aria-label="Feedback on Q2 for ada-l"]')!.value).toBe('Tidy.');
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Feedback for ada-l');
    expect([...panel.querySelectorAll<HTMLAnchorElement>('.md-links a')].map((l) => [l.textContent, l.getAttribute('href')])).toEqual([
      ['Submission repo', `https://github.com/${COHORT_ORG}/assignment-2-ada-l`],
      ['Test results (8/10)', `https://github.com/${COHORT_ORG}/semester-config/blob/HEAD/.system/autograde/assignment-2/ada-l.json`],
    ]);
    expect(panel.querySelector('details.md-view summary')!.textContent).toBe('Student view');
    expect(panel.querySelector('.student-view')!.textContent).not.toContain('Check the plot.');
    // A box in the panel is the same cell edit as the grid's: the preview, the badge and the draft follow it.
    await type('textarea[aria-label="Feedback for ada-l"]', 'Good.\nThe plot needs labels.');
    expect(ada().querySelector('.md-line')!.textContent).toBe('Good.');
    expect(ada().title).toBe('Good.\nThe plot needs labels.');
    expect(root!.textContent).toContain('1 student changed');
    await act(() => void q('textarea[aria-label="Feedback for ada-l"]').dispatchEvent(new FocusEvent('focusout', { bubbles: true }))); // kept at once when a box loses focus
    expect(JSON.parse(localStorage.getItem(k)!).edits['["submissions","ada-l","feedback_individual"]']).toBe('Good.\nThe plot needs labels.');
    // Escape closes it, and focus goes back to the line that opened it.
    await act(() => void q('textarea[aria-label="Feedback for ada-l"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(root!.querySelector('aside.marks-drawer')).toBeNull();
    expect(document.activeElement).toBe(ada());
  });

  it('moves the side panel with focus in the grid, leaving focus where it is', async () => {
    await marks();
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    const box = q<HTMLInputElement>('input[aria-label="Q1 (out of 10) for ben-k"]');
    await act(() => box.focus());
    expect(q('#marks-drawer-title').textContent).toBe('ben-k');
    expect(document.activeElement).toBe(box);
    expect(q('tr.md-here').getAttribute('data-unit')).toBe('ben-k');
    await click(q('button[aria-label="Close the side panel"]'));
    expect(root!.querySelector('aside.marks-drawer')).toBeNull();
  });

  it('closes the side panel before full screen on Escape', async () => {
    await marks();
    await click(q('button.full-toggle'));
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    expect(q('.marking.full aside.marks-drawer')).not.toBeNull();
    await act(() => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(root!.querySelector('aside.marks-drawer')).toBeNull();
    expect(q('.marking').classList.contains('full')).toBe(true);
    await act(() => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(q('.marking').classList.contains('full')).toBe(false);
  });

  it('names the submission from status.json’s semester-side name, and links the Completion cell to the executed notebook', async () => {
    const renamed: Loaded = { ...ready, status: { ...ready.status!, assignments: [{ ...solo, semester_name: 'assignment-2-resit', submit_shape: 'shared-dropbox-repo' }] } } as Loaded;
    const nb = SHEET.replace('      autograde: 8/10\n', '      autograde: 8/10\n      completion: errors:2\n').replace('      autograde: 5/10\n', '      autograde: 5/10\n      completion: not-attempted\n');
    const f = files(nb, CONFIG.replace('formats: [py]', 'formats: [ipynb]'));
    await mount(envOf(github(), f), <AssignmentScreen {...props(f, { loaded: renamed })} />);
    const nbUrl = `https://github.com/${COHORT_ORG}/semester-config/blob/HEAD/.system/autograde/assignment-2/ada-l.ipynb`;
    const ada = [...root!.querySelectorAll('tr.unit-row')][0];
    expect(ada.querySelectorAll('td.sys')[3].querySelector('a')!.getAttribute('href')).toBe(nbUrl);
    // Nothing was run for a submission never attempted: no notebook to link.
    expect([...root!.querySelectorAll('tr.unit-row')][1].querySelectorAll('td.sys')[3].querySelector('a')).toBeNull();
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    expect([...root!.querySelectorAll<HTMLAnchorElement>('.md-links a')].map((l) => [l.textContent, l.getAttribute('href')])).toEqual([
      ['Submission folder', `https://github.com/${COHORT_ORG}/assignment-2-resit-submissions/tree/HEAD/ada-l`],
      ['Test results (8/10)', `https://github.com/${COHORT_ORG}/semester-config/blob/HEAD/.system/autograde/assignment-2/ada-l.json`],
      ['Executed notebook', nbUrl],
    ]);
  });

  it('links the Tests cell to the per-test results the cutoff archived', async () => {
    await marks();
    const link = q<HTMLAnchorElement>('td.sys.auto a');
    expect([link.textContent, link.getAttribute('href')]).toEqual(['8/10', `https://github.com/${COHORT_ORG}/semester-config/blob/HEAD/.system/autograde/assignment-2/ada-l.json`]);
    // A unit with no count yet has no link.
    const cy = [...root!.querySelectorAll('tr.unit-row')].find((r) => r.textContent?.includes('cy-d'))!;
    expect(cy.querySelector('td.sys a')).toBeNull();
  });
});

describe('Return marks from the marking screen', () => {
  it('counts who gets marks, who is skipped and who is held, and gives the final grades', async () => {
    const gh = github(), f = files(), env = envOf(gh, f);
    await mount(env, <><AssignmentScreen {...props(f)} /><OpPanel /></>);
    expect(button('Return marks to 1 student')).toBeDefined();
    await click(button('Return marks to 1 student'));
    const facts = [...root!.querySelectorAll('.op-facts p')].map((p) => p.textContent);
    expect(facts).toEqual(['1 student gets marks · 1 not marked yet (skipped) · 1 held (fix first)', 'Final: median 17, range 17–17, 1 held']);
  });

  it('says unsaved rows are not counted until saved', async () => {
    const gh = github(), f = files(), env = envOf(gh, f);
    await mount(env, <><AssignmentScreen {...props(f)} /><OpPanel /></>);
    await type('input[aria-label="Q1 (out of 10) for cy-d"]', '6');
    await click(button('Return marks to 1 student'));
    expect(q('.op-facts p').textContent).toBe('1 unsaved row is not counted until you save');
  });

  it('opens from the Overview on the Marks tab, through the same panel with its figures', async () => {
    const gh = github(), f = files(), env = envOf(gh, f);
    const live: Loaded = { ...ready, status: { ...ready.status!, assignments: [{ ...solo, marks: { filled: 1, total: 3 } }] } } as Loaded;
    await mount(env, <AssignmentScreen {...props(f, { tab: 'overview', loaded: live })} />);
    const verb = [...root!.querySelectorAll<HTMLAnchorElement>('a.btn')].find((x) => x.textContent === 'Return marks')!;
    expect(verb.getAttribute('href')).toBe('#assignment-assignment-2/marks/return-run');
    await click(verb);
    expect(env.ops.current.value).toBeNull(); // nothing opens until the Marks tab has read the sheet
    render(null, root!);
    await act(() => render(<EnvCtx.Provider value={env}><AssignmentScreen {...props(f, { loaded: live, open: 'return-run' })} /><OpPanel /></EnvCtx.Provider>, root!));
    expect(env.ops.current.value?.def.verb).toBe('Return marks to 1 student');
    expect(q('.op-facts').textContent).toContain('1 held (fix first)');
  });

  it('opens once per arrival: the address drops the open, so a reload or Back does not preview again', async () => {
    const gh = github(), f = files(), env = envOf(gh, f);
    const opened = vi.spyOn(env.ops, 'open').mockImplementation(() => {});
    location.hash = '#assignment-assignment-2/marks/return-preview';
    const arrive = () => act(() => render(<EnvCtx.Provider value={env}><AssignmentScreen {...props(f, { open: parseHash(location.hash).open })} /></EnvCtx.Provider>, root!));
    root = document.createElement('div');
    document.body.appendChild(root);
    await arrive();
    expect(opened).toHaveBeenCalledTimes(1);
    expect(location.hash).toBe('#assignment-assignment-2/marks');
    render(null, root);
    await arrive();
    expect(opened).toHaveBeenCalledTimes(1);
    location.hash = '';
  });

  it('finds the preview issue past the first page of open issues, and says so when there is none', async () => {
    const PREVIEW = { title: 'Distribute grades preview', body: '### Students who would be emailed (1)\n- `cy-d` - their first grades email.' };
    const page = (n: number, extra: object[] = []) => [...Array.from({ length: n }, (_, i) => ({ number: i + 1, title: `Question ${i}` })), ...extra];
    const shown = async (pages: Record<string, object[]>) => {
      const gh = new FakeGitHub().on('GET', /\/semester-config\/issues\?/, (r: Seen) => json(pages[new URL(r.url).searchParams.get('page') ?? '1'] ?? []));
      const env = envOf(gh, files());
      await mount(env, <PreviewEmails org={COHORT_ORG} slug="assignment-2" />);
      await settle(() => !root!.textContent!.includes('Reading'));
      const said = root!.textContent;
      render(null, root!);
      root!.remove();
      return said;
    };
    expect(await shown({ 1: page(100), 2: page(3, [PREVIEW]) })).toBe('Email to: cy-d (first marks email).');
    expect(await shown({ 1: page(100), 2: page(3) })).toBe('Could not find the preview issue in semester-config, so who would be emailed is not shown.');
  });

  it('names who a preview would email, from the private preview issue', () => {
    const def = returnMarks({ courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' }, { slug: 'a', title: 'A', template: 't', units: 1, group: false, when: 'Marking' }, 1, 'a', null, [], 'extra');
    expect(def.dryExtra).toBe('extra');
  });
});

describe('the Submitted column', () => {
  const submitted = () => [...root!.querySelectorAll('tr.unit-row')].map((r) => r.querySelector('td.sys')!.textContent);
  it('shows a recorded time short and as written (no timezone shift), a date alone as a day, and anything else as written', async () => {
    await marks(files(SHEET.replace('submitted: 2026-09-27T20:00', "submitted: '2026-10-13 22:41:05+02:00'").replace('submitted: 2026-09-29T20:00', "submitted: '2026-10-03'")));
    expect(submitted()).toEqual(['13 Oct 22:41', '3 Oct', 'Nothing']);
    root!.remove();
    await marks(files(SHEET.replace('submitted: 2026-09-27T20:00', 'submitted: by email')));
    expect(submitted()).toEqual(['by email', '29 Sep 20:00', 'Nothing']);
  });
});

/** The Marks tab on freshly loaded modules, as a browser without field-sizing loads them (Firefox, Safari). */
async function marksNoFieldSizing(f = files()) {
  vi.stubGlobal('CSS', { supports: () => false, escape: (s: string) => s });
  vi.resetModules();
  const fresh = await import('../src/screens/Assignments');
  const { EnvCtx: FreshCtx } = await import('../src/env');
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<FreshCtx.Provider value={envOf(github(), f)}><fresh.AssignmentScreen {...props(f)} /></FreshCtx.Provider>, root!));
}

describe('growing text boxes', () => {
  it('grows a textarea with its text where the browser has no field-sizing', async () => {
    // As Firefox and Safari answer: no field-sizing (asked once, when the side panel's module loads).
    await marksNoFieldSizing();
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    const fb = q<HTMLTextAreaElement>('textarea[aria-label="Feedback for ada-l"]');
    Object.defineProperty(fb, 'scrollHeight', { configurable: true, get: () => 90 });
    await act(() => {
      fb.value = 'A\nlonger\nnote';
      fb.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(fb.style.height).toBe('92px');
    vi.unstubAllGlobals();
  });
});

describe('the text box sweep', () => {
  it('measures only the box typed in on a keystroke, and a box a snippet changed', async () => {
    await marksNoFieldSizing(files(SHEET.replace('submissions:\n', 'snippets:\n  - Label your axes.\nsubmissions:\n')));
    await click(q('button[aria-label="Open feedback for ada-l"]'));
    const read: string[] = [];
    for (const el of root!.querySelectorAll<HTMLTextAreaElement>('aside.marks-drawer textarea')) {
      Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => (read.push(el.getAttribute('aria-label')!), 60) });
    }
    await type('textarea[aria-label="Feedback for ada-l"]', 'Good. More');
    expect(read).toEqual(['Feedback for ada-l']);
    read.length = 0;
    await act(() => q<HTMLTextAreaElement>('textarea[aria-label="Feedback on Q1 for ada-l"]').focus());
    await click(q('.md-snip'));
    expect(read).toEqual(['Feedback on Q1 for ada-l']);
    vi.unstubAllGlobals();
  });
});

describe('unsaved marks', () => {
  it('offers a kept draft instead of replaying it, and Restore puts it over the sheet', async () => {
    localStorage.setItem(`dsl-console-marks-draft:${COHORT_ORG}:grading_sheets/assignment-2.yml`, JSON.stringify({ sha: 'older', at: Date.parse('2026-10-08T09:00:00Z'), edits: { '["submissions","cy-d","score_individual","Q1"]': 6 } }));
    await marks();
    expect(q<HTMLInputElement>('input[aria-label="Q1 (out of 10) for cy-d"]').value).toBe('');
    expect(q('.draft-offer').textContent).toContain('Unsaved draft from Thu 8 Oct 11:00 (1 row), made before the sheet last changed');
    await click(button('Restore'));
    expect(q<HTMLInputElement>('input[aria-label="Q1 (out of 10) for cy-d"]').value).toBe('6');
    expect(root!.textContent).toContain('1 student changed');
  });

  it('keeps marks typed while a draft is still offered, and the offer with them', async () => {
    const k = `dsl-console-marks-draft:${COHORT_ORG}:grading_sheets/assignment-2.yml`;
    localStorage.setItem(k, JSON.stringify({ sha: 'static', at: 0, edits: { '["submissions","cy-d","score_individual","Q1"]': 6 } }));
    await marks();
    await type('input[aria-label="Q2 (out of 10) for cy-d"]', '7');
    await act(() => void window.dispatchEvent(new Event('pagehide')));
    expect(JSON.parse(localStorage.getItem(k)!).edits).toEqual({ '["submissions","cy-d","score_individual","Q1"]': 6, '["submissions","cy-d","score_individual","Q2"]': 7 });
    await click(button('Restore'));
    expect(q<HTMLInputElement>('input[aria-label="Q1 (out of 10) for cy-d"]').value).toBe('6');
    expect(q<HTMLInputElement>('input[aria-label="Q2 (out of 10) for cy-d"]').value).toBe('7');
  });

  it('Discard drops the draft', async () => {
    const k = `dsl-console-marks-draft:${COHORT_ORG}:grading_sheets/assignment-2.yml`;
    localStorage.setItem(k, JSON.stringify({ sha: 'static', at: 0, edits: { '["submissions","cy-d","score_individual","Q1"]': 6 } }));
    await marks();
    await click(button('Discard'));
    expect(root!.querySelector('.draft-offer')).toBeNull();
    expect(localStorage.getItem(k)).toBeNull();
  });

  it('keeps typed marks in the browser until they are saved', async () => {
    const k = `dsl-console-marks-draft:${COHORT_ORG}:grading_sheets/assignment-2.yml`;
    const gh = github();
    await marks(files(), gh);
    await type('input[aria-label="Q1 (out of 10) for cy-d"]', '6');
    expect(localStorage.getItem(k)).toBeNull(); // kept once typing pauses, or the box loses focus
    await act(() => void q('input[aria-label="Q1 (out of 10) for cy-d"]').dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
    expect(JSON.parse(localStorage.getItem(k)!)).toMatchObject({ sha: 'static', edits: { '["submissions","cy-d","score_individual","Q1"]': 6 } });
    await click(button('Save marks'));
    await settle(() => puts(gh).length > 0 && localStorage.getItem(k) === null);
    expect(localStorage.getItem(k)).toBeNull();
  });

  it('saves over someone else’s change: reads the sheet again, keeps both, and says how many rows moved', async () => {
    const theirs = SHEET.replace('      Q1: 8\n', '      Q1: 9\n');
    const gh = github(true, theirs);
    await marks(files(), gh);
    await type('input[aria-label="Q1 (out of 10) for cy-d"]', '6');
    await click(button('Save marks'));
    await settle(() => puts(gh).length > 1 && !root!.textContent!.includes('Checking'));
    const second = puts(gh)[1];
    expect((second.body as { sha: string }).sha).toBe('fresh-sha');
    const saved = parse(body(second)).submissions;
    expect(saved['ada-l'].score_individual.Q1).toBe(9); // theirs
    expect(saved['cy-d'].score_individual.Q1).toBe(6); // mine
    expect(root!.textContent).toContain('1 row changed by someone else; your edits kept.');
  });
});

describe('teams and people on the grid', () => {
  // The names from students.csv, the handles from the engine's roster, joined on email.
  const roster = rosterFiles(COHORT_ORG, 'email,name,role,github_handle\nana@x.org,Ana Lopez,enrolled,ana-l\nben@x.org,Ben Okafor,enrolled,ben-k\n');
  const TEAMS = [
    'teams:',
    '  geneva:',
    '    info:',
    '      submitted: 2026-09-27T20:00',
    '      days_late: 0',
    '    feedback_group:',
    '    members:',
    '      ana-l:',
    '        adjustment_individual:',
    '        feedback_individual:',
    '        notes_not_shared_with_students:',
    '      ben-k:',
    '        adjustment_individual:',
    '        feedback_individual:',
    '        notes_not_shared_with_students:',
    '      cy-d:',
    '        adjustment_individual:',
    '        feedback_individual:',
    '        notes_not_shared_with_students:',
    '    score_group:',
    '      Q1: 8',
    '      Q2: 9',
    '',
  ].join('\n');
  const team: Assignment = { ...solo, teams: 1, units: 1 };
  const loaded: Loaded = { kind: 'ready', status: { ...STATUS, assignments: [team] }, sha: 's', stale: [] };
  const teamFiles = (sheet = TEAMS) => new StaticFiles({
    [`${COURSE_ORG}/assignment-2-f2026/grading_config.yml`]: 'type: group\nquestions:\n  Q1: 10\n  Q2: 10\n',
    [SHEET_PATH]: sheet,
    ...roster,
  });
  const mountTeams = (f = teamFiles()) => mount(envOf(github(), f), <AssignmentScreen {...props(f, { loaded })} />);

  it('shows one row per team with its members’ names, and opens the members on its toggle', async () => {
    await mountTeams();
    expect(q('tr.team-row .team-names').textContent).toBe('Ana Lopez, Ben Okafor, cy-d · 3');
    expect(root!.querySelector('tr.member-row')).toBeNull();
    expect(q('thead').textContent).not.toContain('Adjustment');
    await click(q('button[aria-label="Show the members of geneva"]'));
    const members = [...root!.querySelectorAll('tr.member-row')];
    expect(members.map((r) => r.querySelector('td')!.textContent)).toEqual(['Ana Lopezana-l', 'Ben Okaforben-k', 'cy-d']);
    // The team's score on each member row, their own adjustment in the amber column.
    expect(members[0].querySelector('td.team-score')!.textContent).toBe('17');
    expect(q('thead th.adjcol').textContent).toContain('Adjustment');
    expect(q('tr.team-row td.adjcol').textContent).toBe('');
    await type('input[aria-label="Adjustment for ana-l"]', '3');
    expect([...members[0].querySelectorAll('td.calc')].pop()!.textContent).toBe('20');
  });

  it('gives a team its own Private notes on its row, members closed, and each member theirs, each opening its side panel', async () => {
    await mountTeams();
    expect(root!.querySelector('tr.member-row')).toBeNull();
    await click(q('tr.team-row button[aria-label="Open team private notes for geneva"]'));
    let panel = q('aside.marks-drawer');
    expect(panel.querySelector('.eyebrow')!.textContent).toBe('Team');
    expect([...panel.querySelectorAll('textarea')].map((t) => t.getAttribute('aria-label'))).toEqual(['Team feedback for geneva', 'Feedback on Q1 for geneva', 'Feedback on Q2 for geneva', 'Team private notes for geneva']);
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Team private notes for geneva');
    // The team's Student view shows one member at a time.
    expect([...panel.querySelectorAll<HTMLOptionElement>('select[aria-label="Member shown"] option')].map((o) => o.value)).toEqual(['ana-l', 'ben-k', 'cy-d']);
    await click(q('button[aria-label="Show the members of geneva"]'));
    for (const h of ['ana-l', 'ben-k', 'cy-d']) expect(root!.querySelector(`tr.member-row button[aria-label="Open private notes for ${h}"]`)).not.toBeNull();
    await click(q('tr.member-row button[aria-label="Open feedback for ben-k"]'));
    panel = q('aside.marks-drawer');
    expect([panel.querySelector('.eyebrow')!.textContent, q('#marks-drawer-title').textContent]).toEqual(['Member of geneva', 'Ben Okafor']);
    expect([...panel.querySelectorAll('textarea')].map((t) => t.getAttribute('aria-label'))).toEqual(['Feedback for ben-k', 'Private notes for ben-k']);
    expect(panel.querySelector('.md-links a')!.getAttribute('href')).toBe(`https://github.com/${COHORT_ORG}/assignment-2-geneva`);
  });

  it('opens a team on load where a member has an adjustment', async () => {
    await mountTeams(teamFiles(TEAMS.replace('      ben-k:\n        adjustment_individual:', '      ben-k:\n        adjustment_individual: -2')));
    expect(root!.querySelectorAll('tr.member-row')).toHaveLength(3);
    expect(button('Open all teams')).toBeUndefined();
  });

  it('names an individual student, handle under the name, and keeps Adjustment behind its chip', async () => {
    const f = files();
    Object.assign((f as unknown as { map: Record<string, string> }).map, roster);
    await marks(f);
    const ada = [...root!.querySelectorAll('tr.unit-row')][1];
    expect(ada.querySelector('td')!.textContent).toContain('Ben Okaforben-k');
    expect(root!.querySelector('input[aria-label="Adjustment for ben-k"]')).toBeNull();
    await click(q('button.adj-chip'));
    expect(root!.querySelector('input[aria-label="Adjustment for ben-k"]')).not.toBeNull();
  });
});

describe('column widths', () => {
  const widthsKey = `dsl-console-marks-widths:${COHORT_ORG}:grading_sheets/assignment-2.yml`;
  const colW = () => Object.fromEntries([...root!.querySelectorAll<HTMLElement>('table.marks colgroup col')].map((c) => [c.dataset.colKey, c.style.width]));
  const grip = (key: string) => q<HTMLElement>(`.col-grip[data-col-key="${key}"]`);
  const drag = (el: HTMLElement, dx: number) =>
    act(() => {
      el.dispatchEvent(new PointerEvent('pointerdown', { clientX: 100, pointerId: 1, bubbles: true }));
      el.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 + dx, pointerId: 1, bubbles: true }));
      el.dispatchEvent(new PointerEvent('pointerup', { clientX: 100 + dx, pointerId: 1, bubbles: true }));
    });

  it('sizes each column to its content by default, and leaves Feedback and Private notes to share the rest', async () => {
    await marks();
    const w = colW();
    expect(w).toMatchObject({ submitted: '105px', days: '56px', tests: '56px', 'q:Q1': '64px', 'q:Q2': '64px', score: '56px', penalty: '156px', final: '56px', fb: '', pn: '' });
    expect(Number.parseInt(w.who!, 10)).toBeLessThanOrEqual(220);
    expect(q('th.grow').textContent).toContain('Feedback');
    expect(root!.querySelectorAll('tr.unit-row td.grow')).toHaveLength(6);
    // The table never squeezes the two text columns under 170px each: exactly the fixed columns plus 2 x 170.
    const fixed = Object.entries(w).filter(([k]) => k !== 'fb' && k !== 'pn').reduce((n, [, v]) => n + Number.parseInt(String(v), 10), 0);
    expect(Number.parseInt(q<HTMLElement>('table.marks').style.minWidth, 10)).toBe(fixed + 2 * 170);
    const css = readFileSync(`${process.cwd()}/src/styles/console.css`, 'utf8');
    expect(css).toMatch(/table\.marks \{ table-layout: fixed;/);
    expect(css).toMatch(/table\.marks td\.pen \{[^}]*text-align: left;/);
  });

  it('drags a column wider, keeps it per sheet, and double-click or Reset widths puts it back', async () => {
    await marks();
    expect(button('Reset widths')).toBeUndefined();
    await drag(grip('penalty'), 40);
    expect(colW().penalty).toBe('196px');
    await drag(grip('final'), -100);
    expect(colW().final).toBe('48px'); // never under the minimum
    expect(JSON.parse(localStorage.getItem(widthsKey)!)).toEqual({ penalty: 196, final: 48 });
    await act(() => void grip('penalty').dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    expect(colW().penalty).toBe('156px');
    await act(() => void grip('days').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
    expect(colW().days).toBe('64px');
    expect(grip('days').getAttribute('tabindex')).toBe('0');
    await click(button('Reset widths'));
    expect(colW()).toMatchObject({ final: '56px', days: '56px' });
    expect(localStorage.getItem(widthsKey)).toBeNull();
  });

  it('starts the submission columns folded in a narrow window and open in a wide one or full screen, until the instructor chooses', async () => {
    const folded = () => q('thead th .th-fold').getAttribute('aria-expanded') === 'false';
    windowWidth(1280);
    await marks();
    expect(folded()).toBe(true);
    expect(q('thead').textContent).not.toContain('Days late');
    await click(q('button.full-toggle'));
    expect(folded()).toBe(false); // full screen counts as wide
    await click(q('button.full-toggle'));
    expect(folded()).toBe(true);
    // The instructor's choice sticks for this sheet, whatever the width, until Reset widths; it is no width, so it alone offers no Reset widths.
    await click(q('thead th .th-fold'));
    expect(folded()).toBe(false);
    expect(localStorage.getItem(`dsl-console-marks-fold:${COHORT_ORG}:grading_sheets/assignment-2.yml`)).toBe('0');
    expect(localStorage.getItem(widthsKey)).toBeNull();
    expect(button('Reset widths')).toBeUndefined();
    root!.remove();
    await marks();
    expect(folded()).toBe(false);
    await act(() => void grip('days').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
    await click(button('Reset widths'));
    expect(folded()).toBe(true);
    expect(localStorage.getItem(`dsl-console-marks-fold:${COHORT_ORG}:grading_sheets/assignment-2.yml`)).toBeNull();
    root!.remove();
    localStorage.clear();
    windowWidth(1440);
    await marks();
    expect(folded()).toBe(false);
    windowWidth(1200);
    await act(() => void window.dispatchEvent(new Event('resize')));
    expect(folded()).toBe(true);
  });

  it('opens with the widths kept from an earlier visit', async () => {
    localStorage.setItem(widthsKey, JSON.stringify({ fb: 300, who: 150 }));
    await marks();
    expect(colW()).toMatchObject({ fb: '300px', who: '150px', pn: '' });
  });
});

describe('text boxes', () => {
  it('gives every row a Private notes line: a student, a team with its members closed, and each member', async () => {
    await marks();
    for (const h of ['ada-l', 'ben-k', 'cy-d']) expect(root!.querySelector(`button[aria-label="Open private notes for ${h}"]`)).not.toBeNull();
    expect(q('button[aria-label="Open private notes for ada-l"]').textContent).toBe('Check the plot.');
  });
});

describe('feedback snippets', () => {
  it('adds a snippet in the side panel, puts it into the last feedback box with one click, and saves both', async () => {
    const gh = github();
    await marks(files(SHEET.replace('submissions:\n', 'snippets:\n  - Label your axes.\nsubmissions:\n')), gh);
    await click(q('button[aria-label="Open feedback for ben-k"]'));
    const chips = () => [...root!.querySelectorAll<HTMLButtonElement>('.md-snip')].map((b) => b.textContent);
    expect(chips()).toEqual(['Label your axes.']);
    await click(button('Edit snippets'));
    await type('input[aria-label="New snippet"]', 'See the worked example.');
    await click(button('Add'));
    await click(button('Done'));
    expect(chips()).toEqual(['Label your axes.', 'See the worked example.']);
    expect(root!.textContent).toContain('snippets changed');
    // Into the box last focused: feedback on Q1 here, not the overall feedback.
    await act(() => q<HTMLTextAreaElement>('textarea[aria-label="Feedback on Q1 for ben-k"]').focus());
    await click([...root!.querySelectorAll('.md-snip')][1]);
    expect(q<HTMLTextAreaElement>('textarea[aria-label="Feedback on Q1 for ben-k"]').value).toBe('See the worked example.');
    await click(button('Save marks'));
    await settle(() => puts(gh).length > 0);
    const saved = parse(body(puts(gh)[0]));
    expect(saved.snippets).toEqual(['Label your axes.', 'See the worked example.']);
    expect(saved.submissions['ben-k'].feedback_per_question).toEqual({ Q1: 'See the worked example.' });
    expect(saved.submissions['ben-k'].marked_by).toBeUndefined(); // feedback only: no mark was saved
  });
});

describe('moderation', () => {
  const MARKED = SHEET.replace('    notes_not_shared_with_students: Check the plot.\n', '    notes_not_shared_with_students: Check the plot.\n    marked_by: jan\n')
    .replace('      Q1: pass\n', '      Q1: 4\n').replace('  ben-k:\n    info:', '  ben-k:\n    marked_by: kim\n    info:');

  it('writes the marker on each unit whose marks a save changes', async () => {
    const gh = github();
    await marks(files(), gh);
    await type('input[aria-label="Q1 (out of 10) for cy-d"]', '6');
    await type('input[aria-label="Late penalty override for ben-k, as a percentage"]', '15');
    await click(button('Save marks'));
    await settle(() => puts(gh).length > 0);
    const saved = parse(body(puts(gh)[0])).submissions;
    expect(saved['cy-d'].marked_by).toBe(USER.login);
    expect(saved['ben-k'].marked_by).toBeUndefined();
    expect(saved['ada-l'].marked_by).toBeUndefined();
  });

  it('shows each marker’s count, mean and median, and filters the grid to one marker', async () => {
    await marks(files(MARKED));
    const rows = [...root!.querySelectorAll('section.markers tbody tr')].map((r) => [...r.querySelectorAll('td')].map((d) => d.textContent));
    expect(rows).toEqual([['jan', '1', '17', '17'], ['kim', '1', '4', '4']]);
    const pick = async (value: string) => act(() => {
      const s = q<HTMLSelectElement>('select[aria-label="Marked by"]');
      s.value = value;
      s.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const keys = () => [...root!.querySelectorAll('tr.unit-row')].map((r) => r.getAttribute('data-unit'));
    expect(keys()).toEqual(['ada-l', 'ben-k', 'cy-d']);
    await pick('kim');
    expect(keys()).toEqual(['ben-k']);
    await pick('-');
    expect(keys()).toEqual(['cy-d']);
    await pick('*');
    expect(keys()).toHaveLength(3);
  });

  it('shows no Markers table and no Marked by filter before any marker is recorded', async () => {
    await marks();
    expect(root!.querySelector('section.markers')).toBeNull();
    expect(root!.querySelector('select[aria-label="Marked by"]')).toBeNull();
  });
});
