// Set up: working on the materials and the assignments on the student's own machine
// (decision 0027 rule 4). The console checks, with the student's token, whether they have
// forked each materials repo (`GET /repos/{login}/{repo}`, which the public site never
// could); each fork and each of the student's assignment repos then gets the same Open
// button the instructors have. The folder and editor come from Profile, one for both roles:
// a fork goes in its semester's folder, beside the assignment repos.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { GitHubClient } from '../github/client';
import type { Mine } from '../model/mine';
import { yourSetup } from '../model/prefs';
import type { SemesterFacts } from '../model/student';
import { Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';
import { OpenButton } from '../ui/OpenButton';

export type ForkState = { kind: 'forked'; url: string } | { kind: 'none' } | { kind: 'other'; url: string };

/** Whether `login` has a fork of `org/repo` under the same name: a repo of that name that is not its fork is `other`. */
export async function forkOf(client: GitHubClient, login: string, org: string, repo: string): Promise<ForkState> {
  const r = await client.getRepo(login, repo);
  if (!r) return { kind: 'none' };
  return r.fork && r.parent?.full_name.toLowerCase() === `${org}/${repo}`.toLowerCase() ? { kind: 'forked', url: r.html_url } : { kind: 'other', url: r.html_url };
}

export function SetupView({ org, facts, mine, studentView }: { org: string; facts: SemesterFacts; mine: Mine | null; studentView: boolean }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [tick, setTick] = useState(0);
  const repos = facts.materialsRepos;
  const forks = useLoad(env && !studentView ? () => Promise.all(repos.map((r) => forkOf(env.client, login, org, r))) : null, [org, repos.join(','), tick]);
  if (studentView) return <p class="footnote">A student checks here that they have forked each materials repo. Each fork and assignment repo then gets an Open button, using the folder and editor from their Profile.</p>;
  const own = mine ? Object.values(mine.units).filter((u) => u.repo) : [];
  return (
    <div class="stack">
      {yourSetup(login)?.folder.trim() ? null : <p><a href="?#profile">Set your folder and editor in Profile</a></p>}
      {repos.map((repo, i) => {
        const f = forks.kind === 'ready' ? forks.value[i] : null;
        const upstream = `https://github.com/${org}/${repo}`;
        return (
          <section class="panel section" aria-label={repo}>
            <h2>{repo}</h2>
            {forks.kind === 'loading' ? <Loading what="Checking your fork" />
              : f?.kind === 'forked' ? (
                <>
                  <p class="check-line ok">
                    <span>You have forked it: <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a></span>
                    <Hint small label="About new materials">Each week, press Sync fork on your fork’s GitHub page, then pull in your clone.</Hint>
                  </p>
                  <p class="actions"><OpenButton org={login} repo={repo} home={org} /></p>
                </>
              ) : (
                <>
                  {f?.kind === 'other' ? <p class="check-line warn"><span>You have a repo named <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a> that is not a fork of this semester’s; fork under another name, or rename that one.</span></p> : null}
                  <p class="actions">
                    <a class="btn small" href={`${upstream}/fork`} target="_blank" rel="noopener">Fork {repo} <Ext /></a>
                    <button class="textlink" type="button" onClick={() => setTick(tick + 1)}>Check again</button>
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
