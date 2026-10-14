// @vitest-environment happy-dom
// The Students tab: its header line (with the role split), its action row, and Edit roster's
// add and remove, saved as one students.csv commit.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { pendingSummary, roleSplit } from '../src/model/people';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpPanel } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { StudentsScreen } from '../src/screens/People';
import type { CohortProps } from '../src/screens/types';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';
import { StaticFiles } from './staticFiles';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const STATUS = { ...(example as unknown as Status), students: { rows: 3, codes_sent: 2, joined: 1 } };
const ready: Loaded = { kind: 'ready', status: STATUS, sha: 's', stale: [] };

const ROSTER =
  'hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\n' +
  'anna@x.org,Anna Adams,enrolled,anna-a,101,SECRET1,2026-09-02T09:30:00Z\n' +
  'ben@x.org,Ben Baker,enrolled,,,SECRET2,2026-09-02T09:30:00Z\n' +
  'cara@x.org,Cara Cole,Auditor,,,,\n';

const files = () => new StaticFiles({ [`${COHORT_ORG}/semester-config/students.csv`]: ROSTER });
const github = () => new FakeGitHub().on('PUT', /\/contents\//, json({ content: { sha: 'b1' }, commit: { sha: 'c1' } })).on('GET', /\/check-runs/, { check_runs: [] });

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

async function mount() {
  const gh = github(), f = files();
  const props: CohortProps = { course, cohort, loaded: ready, files: f, now: Date.parse('2026-09-23T10:00:00+02:00') };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={envOf(gh, f)}><StudentsScreen {...props} /><OpPanel /></EnvCtx.Provider>, root!));
  return gh;
}

const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
async function click(el: Element) {
  await act(() => (el as HTMLElement).click());
}
async function type(sel: string, value: string) {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith('/contents/students.csv'));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);
const note = () => q('.savebar').closest('.panel')!.textContent!;

describe('the Students roster meter', () => {
  it('splits the roster by role inside the meter panel, and the header says no counts', async () => {
    await mount();
    expect(root!.querySelector('.page-head .lede')).toBeNull();
    expect(q('.roster-split').textContent).toBe('2 students, 1 auditor');
    expect(q('.roster-split').closest('.panel')!.querySelector('.meter')).not.toBeNull();
  });

  it('reads a blank or unknown role as a student', () => {
    expect(roleSplit([{ role: '' }, { role: 'enroled' }, { role: ' AUDITOR ' }, {}])).toEqual({ students: 3, auditors: 1 });
  });
});

describe('the Students action row', () => {
  it('leads with Send codes on its own row, then Replace from CSV and Sync now, with no Preview of its own', async () => {
    await mount();
    const main = q('.students-actions .main-action');
    expect(main.querySelector('button.btn.big')!.textContent).toBe('Send codes');
    expect(main.querySelector('.hint-btn')!.getAttribute('aria-label')).toBe('About Send codes');
    expect(main.querySelector('.hint-pop')!.textContent).toBe('Every student on the roster gets a private enrolment code by email the moment their row is saved; they paste it on the semester’s join form to get their repos. This button sends a NEW code to everyone who has not joined yet (their old code stops working) and emails it again. Preview first to see how many that is.');
    const row = q('.students-actions .actions');
    expect([...row.querySelectorAll('button.btn, a.btn')].map((c) => c.textContent?.trim())).toEqual(['Replace from CSV', 'Sync now']);
    expect(row.querySelector('.hint-btn')!.getAttribute('aria-label')).toBe('About Replace from CSV');
    expect(row.querySelector('.hint-pop')!.textContent).toBe('Upload a CSV from the registrar to replace the roster in one go: you see a diff of added, removed and changed rows before anything is saved. Columns the toolkit fills (GitHub handle, code, when it was sent) are kept for rows that stay.');
    expect(button('Preview')).toBeUndefined();
  });

  it('puts Edit roster as a pencil on the table’s corner, a tick while editing', async () => {
    await mount();
    const icon = () => q<HTMLButtonElement>('.roster-box > button.roster-edit');
    expect([icon().getAttribute('aria-label'), icon().title, icon().textContent, icon().getAttribute('aria-pressed')]).toEqual(['Edit roster', 'Edit roster', '✎', 'false']);
    expect(root!.querySelector('.page-head button[aria-label="Edit roster"]')).toBeNull();
    await click(icon());
    expect([icon().getAttribute('aria-label'), icon().textContent, icon().getAttribute('aria-pressed')]).toEqual(['Done editing', '✓', 'true']);
    await click(icon());
    expect(icon().getAttribute('aria-label')).toBe('Edit roster');
  });

  it('opens the send-new-codes panel, where Preview and the verb live', async () => {
    await mount();
    await click(button('Send codes'));
    const drawer = q('aside.drawer');
    expect(drawer.querySelector('#drawer-title')!.textContent).toBe('Students who have not joined');
    expect([...drawer.querySelectorAll('.drawer-foot button')].map((b) => b.textContent)).toEqual(['Preview', 'Send 1 new code']);
  });

  it('opens Sync membership on GitHub for Sync now', async () => {
    await mount();
    const a = [...root!.querySelectorAll('a')].find((x) => x.textContent === 'Sync now')!;
    expect(a.getAttribute('href')).toBe(`https://github.com/${COURSE_ORG}/.github/actions/workflows/sync-membership.yml`);
    expect(a.getAttribute('target')).toBe('_blank');
  });
});

describe('Edit roster: add and remove', () => {
  const SENTENCE = 'Added students are mailed a code on save. A removed student who had joined loses access at the next sync; their repos are kept.';

  it('marks a removed row struck through, with Undo, and deletes nothing until Save', async () => {
    const gh = await mount();
    expect(root!.querySelector('button[aria-label="Remove line 3"]')).toBeNull(); // read view: no control
    await click(q('button[aria-label="Edit roster"]'));
    await click(q('button[aria-label="Remove line 3"]'));
    expect(q('#line-3').className).toContain('removed');
    // Its cells are text now, not inputs.
    expect(root!.querySelector('input[aria-label="Name, line 3"]')).toBeNull();
    expect(note()).toContain(`1 removed. ${SENTENCE}`);
    expect(puts(gh)).toEqual([]);
    await click(q('button[aria-label="Undo removing line 3"]'));
    expect(q('#line-3').className).not.toContain('removed');
    expect(note()).toContain('No unsaved changes');
  });

  it('keeps the blank Add a student row at the bottom, and drops one at once', async () => {
    await mount();
    await click(button('Add a student'));
    const ids = [...root!.querySelectorAll('.roster-table tbody tr')].map((tr) => tr.id);
    expect(ids).toEqual(['line-2', 'line-3', 'line-4', 'line-5']);
    await click(q('button[aria-label="Remove line 5"]'));
    expect(root!.querySelector('#line-5')).toBeNull();
  });

  it('sums the pending commit and saves it as one students.csv, the removed rows dropped', async () => {
    const gh = await mount();
    await click(button('Add a student'));
    await type('input[aria-label="Email, line 5"]', 'dan@x.org');
    await type('input[aria-label="Name, line 5"]', 'Dan Dorn');
    await type('input[aria-label="Name, line 3"]', 'Ben B. Baker');
    await click(q('button[aria-label="Remove line 2"]'));
    expect(note()).toContain(`1 added, 1 removed, 1 changed. ${SENTENCE}`);
    await click(button('Save and send 1 code'));
    await settle(() => puts(gh).length > 0);
    expect(puts(gh)).toHaveLength(1);
    const text = body(puts(gh)[0]);
    expect(text).not.toContain('anna@x.org');
    expect(text).toContain('ben@x.org,Ben B. Baker,enrolled,');
    expect(text).toContain('cara@x.org,Cara Cole,Auditor,');
    expect(text).toContain('dan@x.org,Dan Dorn,enrolled,');
    expect((puts(gh)[0].body as { message: string }).message).toBe('roster: 1 added, 1 removed, 1 changed, from the DSL Teaching Console');
  });

  it('refuses an added row without a name, or with an email that is not an address', async () => {
    const gh = await mount();
    await click(button('Add a student'));
    await type('input[aria-label="Email, line 5"]', 'dan@x.org');
    await click(button('Save and send 1 code'));
    expect(root!.textContent).toContain('1 added row has no name.');
    await type('input[aria-label="Name, line 5"]', 'Dan Dorn');
    await type('input[aria-label="Email, line 5"]', 'dan');
    await click(button('Save 1 change'));
    expect(root!.textContent).toContain('1 row has an email that is not an address');
    expect(puts(gh)).toEqual([]);
  });

  it('words the summary without the parts that are none', () => {
    expect(pendingSummary({ added: 2, removed: 1, changed: 3 })).toBe('2 added, 1 removed, 3 changed');
    expect(pendingSummary({ added: 0, removed: 2, changed: 0 })).toBe('2 removed');
  });
});
