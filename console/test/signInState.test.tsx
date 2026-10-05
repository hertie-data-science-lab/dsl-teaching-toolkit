// @vitest-environment happy-dom
// The App around sign-in: a saved token GitHub does not answer for is kept and the screen says
// it is retrying, and sign-out forgets this session's runs and previews.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, it } from 'vitest';
import { App, OFFLINE_RETRY, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth, TOKEN_KEY } from '../src/auth/pat';
import type { KeyStore } from '../src/auth/types';
import { GitHubClient } from '../src/github/client';
import { FakeGitHub } from './fake';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

const store = (token: string): KeyStore => {
  const map = new Map([[TOKEN_KEY, token]]);
  return { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => void map.set(k, v), removeItem: (k) => void map.delete(k) };
};

it('says it is retrying while GitHub does not answer for the saved token, and keeps it', async () => {
  const s0 = store('saved');
  const offline = async (): Promise<Response> => {
    throw new TypeError('Failed to fetch');
  };
  const pat = new PatAuth({ fetch: offline, store: s0, sleep: () => new Promise(() => {}) });
  const s = createState({ auth: new ConsoleAuth(pat, null), client: new GitHubClient({ token: () => null, fetch: new FakeGitHub().fetch }) });
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(async () => render(<App state={s} />, root!));
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(root.textContent).toContain(OFFLINE_RETRY);
  expect(s0.getItem(TOKEN_KEY)).toBe('saved');
});

it('forgets the runs and previews of this session on sign-out', () => {
  const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }) });
  s.ops.runs.value = [{ run_id: 1, op: 'check.now', conclusion: 'done', summary: '', finished: 'f', course: 'c' }];
  s.ops.previewed.value = { 'roster.send_codes|o|k': '[]' };
  s.signOut();
  expect([s.ops.runs.value, s.ops.previewed.value, s.ops.current.value]).toEqual([[], {}, null]);
});
