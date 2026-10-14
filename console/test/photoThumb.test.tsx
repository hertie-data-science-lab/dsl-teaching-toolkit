// @vitest-environment happy-dom
// The Instructors tab's Photo column: the photo as a small round thumbnail, else the initials.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { StatusStore } from '../src/model/status';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { Avatar } from '../src/ui/Avatar';
import { FakeGitHub, fileBody } from './fake';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function thumb(photo: string, gh = new FakeGitHub()) {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: new StaticFiles(), pollMs: 0 };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}><Avatar small picture={photo} name="Ana Lopez" org={ORG} /></EnvCtx.Provider>, root!));
  for (let i = 0; i < 20; i++) await act(() => new Promise((r) => setTimeout(r, 0)));
  return root.firstElementChild as HTMLElement;
}

describe('the Instructors tab’s photo thumbnail', () => {
  it('shows an absolute URL as is, a site path read from the site repo, and the muted initials otherwise', async () => {
    const abs = await thumb('https://avatars.githubusercontent.com/u/1');
    expect([abs.tagName, abs.className, abs.getAttribute('src'), abs.getAttribute('alt')]).toEqual(['IMG', 'p-avatar small', 'https://avatars.githubusercontent.com/u/1', 'Ana Lopez']);
    // A broken image falls back to the initials.
    await act(() => void abs.dispatchEvent(new Event('error')));
    expect([root!.firstElementChild!.tagName, root!.firstElementChild!.textContent]).toEqual(['SPAN', 'AL']);
    root!.remove();

    const gh = new FakeGitHub().on('GET', `/repos/${ORG}/${ORG}.github.io/contents/_images/pp/ana.jpg`, fileBody('_images/pp/ana.jpg', 'jpegbytes'));
    const site = await thumb('ana.jpg', gh);
    expect(site.tagName).toBe('IMG');
    expect(site.getAttribute('src')).toMatch(/^data:image\/jpeg;base64,/);
    root!.remove();

    const none = await thumb('');
    expect([none.tagName, none.className, none.textContent, none.getAttribute('aria-label')]).toEqual(['SPAN', 'p-avatar small none', 'AL', 'Ana Lopez: no photo']);
  });
});
