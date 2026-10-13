// @vitest-environment happy-dom
// Instructors > Photo (D7): a link or a path typed in, or an image dropped on the field that is
// uploaded to the site repo's photo folder and its path filled in; a wrong file says why inline.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, fromBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { InstructorsScreen } from '../src/screens/People';
import { StaticFiles } from './staticFiles';
import example from './fixtures/status.example.json';
import { FakeGitHub, json } from './fake';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const loaded: Loaded = { kind: 'ready', status: example as unknown as Status, sha: 's', stale: [] };
const files = new StaticFiles({ [`${ORG}/semester-config/instructors.yml`]: 'instructors:\n  - github_handle: a-example\n    role: instructor\n    email: a@staff.example.org\n    name: Dr A. Example\n' }, { [`${ORG}/${ORG}.github.io/_images/pp`]: ['jane.jpg'] });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(gh: FakeGitHub) {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files, pollMs: 0 };
  root = document.body.appendChild(document.createElement('div'));
  await act(() => render(<EnvCtx.Provider value={env}><InstructorsScreen course={course} cohort={cohort} loaded={loaded} files={files} now={0} /></EnvCtx.Provider>, root!));
  const edit = [...root.querySelectorAll('button')].find((b) => b.textContent === 'Edit')!;
  await act(() => edit.click());
  return root;
}
async function drop(el: Element, f: File) {
  // happy-dom has no `ondrop`, so Preact listens for the event under its prop's own case.
  const e = new Event('ondrop' in el ? 'drop' : 'Drop', { bubbles: true, cancelable: true });
  Object.defineProperty(e, 'dataTransfer', { value: { files: [f] } });
  await act(() => { el.dispatchEvent(e); });
  for (let i = 0; i < 20; i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}

it('links the photo folder, uploads a dropped image there and fills in its path', async () => {
  const gh = new FakeGitHub().on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } }));
  const el = await mount(gh);
  const link = [...el.querySelectorAll('.photo-drop a')].find((a) => a.textContent === 'photo folder')!;
  expect(link.getAttribute('href')).toBe(`https://github.com/${ORG}/${ORG}.github.io/tree/main/_images/pp`);
  await drop(el.querySelector('.photo-drop')!, new File([new Uint8Array([1, 2, 3])], 'Jane Doe.PNG', { type: 'image/png' }));
  const put = gh.seen.find((s) => s.method === 'PUT')!;
  expect(put.url).toBe(`https://api.github.com/repos/${ORG}/${ORG}.github.io/contents/_images/pp/jane-doe.png`);
  expect([...fromBase64((put.body as { content: string }).content)]).toEqual([1, 2, 3]);
  expect(el.querySelector<HTMLInputElement>('#p-photo')!.value).toBe('/_images/pp/jane-doe.png');
});

it('refuses a file that is not a PNG, JPEG or WebP image, inline, and uploads nothing', async () => {
  const gh = new FakeGitHub();
  const el = await mount(gh);
  await drop(el.querySelector('.photo-drop')!, new File(['x'], 'cv.pdf', { type: 'application/pdf' }));
  expect(el.querySelector('.photo-drop')!.textContent).toContain('Not uploaded: a photo must be a PNG, JPEG or WebP image.');
  expect(gh.seen.some((s) => s.method === 'PUT')).toBe(false);
});

it('says the Photo field takes a URL link or a path', async () => {
  const el = await mount(new FakeGitHub());
  expect(el.textContent).toContain('A URL link, or a path in the student site repo.');
});
