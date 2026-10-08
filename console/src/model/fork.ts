// Whether a student forked a semester's materials repo (decisions 0027 rule 4, 0035 rules 9
// and 10): one read of `GET /repos/{login}/{repo}`, kept for the session per sign-in. Profile's
// Your repos check re-reads it and keeps the fresh answer; the file button row reads the kept
// one, lazily, when a row first needs it.

import type { GitHubClient } from '../github/client';

export type ForkState = { kind: 'forked'; url: string } | { kind: 'none' } | { kind: 'other'; url: string };

/** Whether `login` has a fork of `org/repo` under the same name: a repo of that name that is not its fork is `other`. */
export async function forkOf(client: GitHubClient, login: string, org: string, repo: string): Promise<ForkState> {
  const r = await client.getRepo(login, repo);
  if (!r) return { kind: 'none' };
  return r.fork && r.parent?.full_name.toLowerCase() === `${org}/${repo}`.toLowerCase() ? { kind: 'forked', url: r.html_url } : { kind: 'other', url: r.html_url };
}

/** This session's fork answers, per client (one per sign-in), by `<login>/<org>/<repo>`. */
const forks = new WeakMap<GitHubClient, Map<string, Promise<ForkState>>>();
const forkKey = (login: string, org: string, repo: string) => `${login}/${org}/${repo}`.toLowerCase();
const forksOf = (client: GitHubClient) => {
  let m = forks.get(client);
  if (!m) forks.set(client, (m = new Map()));
  return m;
};

/** `forkOf`, read once per session; a read that failed is not kept, so the next row asks again. */
export function cachedFork(client: GitHubClient, login: string, org: string, repo: string): Promise<ForkState> {
  const m = forksOf(client);
  const k = forkKey(login, org, repo);
  let p = m.get(k);
  if (!p) {
    p = forkOf(client, login, org, repo);
    m.set(k, p);
    p.catch(() => m.delete(k));
  }
  return p;
}

/** Keep a fresh answer (the fork check's re-reads), so the button rows opened after it follow. */
export const noteFork = (client: GitHubClient, login: string, org: string, repo: string, f: ForkState) => forksOf(client).set(forkKey(login, org, repo), Promise.resolve(f));
