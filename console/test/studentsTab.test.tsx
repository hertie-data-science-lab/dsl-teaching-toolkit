// @vitest-environment happy-dom
// The Students tab: its header line (with the role split), its action row, and Edit roster's
// add and remove, saved as one students.csv commit.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { roleSplit } from '../src/model/people';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpPanel } from '../src/ops/Panel';
import { OpsSession } from '../src/ops/session';
import { StudentsScreen } from '../src/screens/People';
import type { CohortProps } from '../src/screens/types';
import example from './fixtures/status.example.json';
import { FakeGitHub, json } from './fake';
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

describe('the Students header line', () => {
  it('splits the roster by role, as the engine reads it, beside the codes sent and joined', async () => {
    await mount();
    expect(q('.page-head .lede').textContent).toBe('3 on the roster: 2 students, 1 auditor. 2 codes sent; 1 joined.');
  });

  it('reads a blank or unknown role as a student', () => {
    expect(roleSplit([{ role: '' }, { role: 'enroled' }, { role: ' AUDITOR ' }, {}])).toEqual({ students: 3, auditors: 1 });
  });
});

describe('the Students action row', () => {
  it('is Edit roster, Replace from CSV, Send codes, Sync now, with no Preview of its own', async () => {
    await mount();
    const row = [...q('.page-head .actions').children].map((c) => c.textContent?.trim());
    expect(row).toEqual(['Edit roster', 'Replace from CSV', 'Send codes', 'Sync now']);
    expect(button('Preview')).toBeUndefined();
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
