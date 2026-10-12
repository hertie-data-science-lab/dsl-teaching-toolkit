// @vitest-environment happy-dom
// Decision 0035 (WP SC-H, from the branch code review): an assignment's own page reads only
// that assignment's Submission receipts thread, the list reads every one; the Schedule row's
// state chip links the assignment's page.

import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Semester } from '../src/model/discovery';
import { STUDENT_STATUS_PATH } from '../src/model/names';
import type { Mine } from '../src/model/mine';
import { factsFromStatus } from '../src/model/student';
import { StudentScreen } from '../src/screens/Student';
import { ScheduleView } from '../src/screens/StudentSchedule';
import { FakeGitHub, fileBody } from './fake';
import FILE from './fixtures/student-status.json?raw';

const ORG = 'hertie-dsl-demo-f2026';
const LOGIN = 'octo-student';
const NOW = Date.parse('2026-09-23T12:00:00Z');
const semester: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-dsl-demo-course-e1234', courseName: 'Deep Learning', archived: false, role: 'student' };
const user = { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

/** The status file and two private repos of the student's; every issues read lands in `seen`. */
const fake = () => new FakeGitHub()
  .on('GET', `/repos/${ORG}/.github/contents/.system/student-status.json`, fileBody(STUDENT_STATUS_PATH, FILE))
  .on('GET', new RegExp(`^/orgs/${ORG}/repos`), [{ name: `assignment-1-${LOGIN}` }, { name: `assignment-3-${LOGIN}` }])
  .on('GET', /\/issues\?/, []);

async function mount(v: preact.VNode, f: FakeGitHub) {
  // A fresh client per mount: the student's own reads are reused per client for a minute.
  const env = { user, client: new GitHubClient({ token: () => 't', fetch: f.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{v}</EnvCtx.Provider>, root!));
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

/** The repos whose issues were read. */
const threadsRead = (f: FakeGitHub) => [...new Set(f.seen.filter((c) => c.url.includes('/issues?')).map((c) => c.url.split('/')[5]))].sort();

describe('the Submission receipts threads read', () => {
  it('on an assignment’s page are that assignment’s only; the list reads every private repo’s', async () => {
    const one = fake();
    await mount(<StudentScreen semester={semester} screen="assignment" entry="assignment-1" studentView={false} now={NOW} />, one);
    expect(threadsRead(one)).toEqual([`assignment-1-${LOGIN}`]);
    render(null, root!);
    const all = fake();
    await mount(<StudentScreen semester={semester} screen="assignments" studentView={false} now={NOW} />, all);
    expect(threadsRead(all)).toEqual([`assignment-1-${LOGIN}`, `assignment-3-${LOGIN}`]);
  });
});

describe('the Schedule', () => {
  it('links an assignment row’s state chip to the assignment’s page', () => {
    const facts = factsFromStatus(JSON.parse(FILE));
    const mine: Mine = { units: { 'assignment-1': { slug: 'assignment-1', repo: `assignment-1-${LOGIN}`, team: null, members: null, shared: false } }, gradebook: null, auditor: false };
    const chips = [...html(<ScheduleView facts={facts} mine={mine} now={NOW} org={ORG} />).matchAll(/<a class="st-chip" href="([^"]+)">/g)].map((m) => m[1]);
    expect(chips).toContain(`?semester=${ORG}#assignment-assignment-1`);
    expect(chips).not.toContain(`?semester=${ORG}#assignments`);
  });
});
