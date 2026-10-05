// Set up: working on the materials and the assignments on the student's own machine
// (decision 0027 rule 4). The console checks, with the student's token, whether they have
// forked each materials repo (`GET /repos/{login}/{repo}`, which the public site never
// could); each fork and each of the student's assignment repos then gets the same Open
// button the instructors have. The folder and editor come from Profile, one for both roles:
// a fork goes in its semester's folder, beside the assignment repos. While a read says a repo
// is not forked yet, the check runs again by itself (`poll`): on coming back to the tab, every
// 10 s and every 30 s after the first minute, for up to 5 minutes of the tab being shown;
// "Check again" checks now and starts that again. A repo of the name that is not the fork, or
// a read that failed, waits for "Check again".

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { GitHubClient } from '../github/client';
import { poll } from '../github/poll';
import type { Mine } from '../model/mine';
import { yourSetup } from '../model/prefs';
import type { SemesterFacts } from '../model/student';
import { CheckLine, Loading, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';

export type ForkState = { kind: 'forked'; url: string } | { kind: 'none' } | { kind: 'other'; url: string };

/** Whether `login` has a fork of `org/repo` under the same name: a repo of that name that is not its fork is `other`. */
export async function forkOf(client: GitHubClient, login: string, org: string, repo: string): Promise<ForkState> {
  const r = await client.getRepo(login, repo);
  if (!r) return { kind: 'none' };
  return r.fork && r.parent?.full_name.toLowerCase() === `${org}/${repo}`.toLowerCase() ? { kind: 'forked', url: r.html_url } : { kind: 'other', url: r.html_url };
}

/** How often the fork check runs again while a repo is not forked, slower after a minute, and for how long. */
export const RECHECK_MS = 10000;
const RECHECK_LATER = { after: 60 * 1000, every: 30 * 1000 };
export const RECHECK_FOR_MS = 5 * 60 * 1000;

export function SetupView({ org, facts, mine, studentView }: { org: string; facts: SemesterFacts; mine: Mine | null; studentView: boolean }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  // Each press of Check again checks now and starts a new 5-minute round of re-checks.
  const [round, setRound] = useState(0);
  const repos = facts.materialsRepos;
  // The last answer stays shown while the check runs again, so a re-check does not flash.
  const [forks, setForks] = useState<ForkState[] | null>(null);
  useEffect(() => {
    if (!env || studentView) return;
    const stop = new AbortController();
    void poll(async () => {
      const v = await Promise.all(repos.map((r) => forkOf(env.client, login, org, r)));
      if (stop.signal.aborted) return true;
      setForks(v);
      return !v.some((f) => f.kind === 'none');
    }, {
      every: RECHECK_MS, backoff: RECHECK_LATER, maxMs: RECHECK_FOR_MS, maxMisses: 1, signal: stop.signal,
      onMiss: () => setForks((v) => v ?? repos.map(() => ({ kind: 'none' as const }))),
    });
    return () => stop.abort();
  }, [org, repos.join(','), round, !!env]);
  const checkAgain = () => setRound((r) => r + 1);
  if (studentView) return <p class="footnote">A student checks here that they have forked each materials repo. Each fork and assignment repo then gets an Open button, using the folder and editor from their Profile.</p>;
  const own = mine ? Object.values(mine.units).filter((u) => u.repo) : [];
  return (
    <div class="stack">
      {yourSetup(login)?.folder.trim() ? null : <p><a href="?#profile">Set your folder and editor in Profile</a></p>}
      {repos.map((repo, i) => {
        const f = forks?.[i] ?? null;
        const upstream = ghUrl(org, repo);
        return (
          <section class="panel section" aria-label={repo}>
            <h2>{repo}</h2>
            {!forks ? <Loading what="Checking your fork" />
              : f?.kind === 'forked' ? (
                <>
                  <CheckLine cls="ok">
                    You have forked it: <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a>
                    <Hint small label="About new materials">Each week, press Sync fork on your fork’s GitHub page, then pull in your clone.</Hint>
                  </CheckLine>
                  <p class="actions"><OpenButton org={login} repo={repo} home={org} /></p>
                </>
              ) : (
                <>
                  {f?.kind === 'other' ? <CheckLine cls="warn">You have a repo named <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a> that is not a fork of this semester’s; fork under another name, or rename that one.</CheckLine> : null}
                  <p class="actions">
                    <a class="btn small" href={`${upstream}/fork`} target="_blank" rel="noopener">Fork {repo} <Ext /></a>
                    <button class="textlink" type="button" onClick={checkAgain}>Check again</button>
                  </p>
                </>
              )}
          </section>
        );
      })}
      {own.length ? (
        <section class="panel section" aria-labelledby="h-own">
          <h2 id="h-own">Your assignment repos</h2>
          <ul class="rows">
            {own.map((u) => (
              <li>
                <span><b>{u.repo}</b>{u.shared ? <span class="footnote"> (shared: your work goes in your {u.team ? 'team’s' : 'own'} folder)</span> : null}</span>
                <OpenButton org={org} repo={u.repo!} home={org} small />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
