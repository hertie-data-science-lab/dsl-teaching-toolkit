// Your repos, one section of Profile per live semester the person studies (decision 0035
// rule 9; before it, the student Set up page of decision 0027 rule 4). The console checks, with
// the student's token, whether they have forked each materials repo (`GET /repos/{login}/{repo}`,
// which the public site never could); each fork and each of the student's assignment repos
// then gets the same Open button the instructors have, in the semester's folder from Profile.
// While a read says a repo is not forked yet, the check runs again by itself (`poll`): on
// coming back to the tab, every 10 s and every 30 s after the first minute, for up to 5 minutes
// of the tab being shown; "Check again" checks now and starts that again. A repo of the name
// that is not the fork, or a read that failed, waits for "Check again". A section reads its
// semester (facts, then the student's own repos) only once it is opened. The fork answers are
// kept for the session (`model/fork.ts`), which the file button row reads too (rule 10).

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { poll } from '../github/poll';
import { semesterName, type Semester } from '../model/discovery';
import { forkOf, noteFork, type ForkState } from '../model/fork';
import { readMine, type Mine } from '../model/mine';
import type { SemesterFacts } from '../model/student';
import { CheckLine, Loading, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';
import { OpenButton } from '../ui/OpenButton';
import { studentData } from './Student';

/** How often the fork check runs again while a repo is not forked, slower after a minute, and for how long. */
export const RECHECK_MS = 10000;
const RECHECK_LATER = { after: 60 * 1000, every: 30 * 1000 };
export const RECHECK_FOR_MS = 5 * 60 * 1000;

/** The fork check of each materials repo, then the Open button of each assignment repo. */
export function RepoChecks({ org, facts, mine }: { org: string; facts: Pick<SemesterFacts, 'materialsRepos'>; mine: Mine | null }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  // Each press of Check again checks now and starts a new 5-minute round of re-checks.
  const [round, setRound] = useState(0);
  const repos = facts.materialsRepos;
  // The last answer stays shown while the check runs again, so a re-check does not flash.
  const [found, setFound] = useState<ForkState[] | null>(null);
  useEffect(() => {
    if (!env) return;
    const stop = new AbortController();
    void poll(async () => {
      const v = await Promise.all(repos.map((r) => forkOf(env.client, login, org, r)));
      if (stop.signal.aborted) return true;
      repos.forEach((r, i) => noteFork(env.client, login, org, r, v[i]));
      setFound(v);
      return !v.some((f) => f.kind === 'none');
    }, {
      every: RECHECK_MS, backoff: RECHECK_LATER, maxMs: RECHECK_FOR_MS, maxMisses: 1, signal: stop.signal,
      onMiss: () => setFound((v) => v ?? repos.map(() => ({ kind: 'none' as const }))),
    });
    return () => stop.abort();
  }, [org, repos.join(','), round, !!env]);
  const checkAgain = () => setRound((r) => r + 1);
  const own = mine ? Object.values(mine.units).filter((u) => u.repo) : [];
  return (
    <div class="stack">
      {repos.map((repo, i) => {
        const f = found?.[i] ?? null;
        return (
          <section class="your-repo" aria-label={repo}>
            <h3>{repo}</h3>
            {!found ? <Loading what="Checking your fork" />
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
                    <a class="btn small" href={`${ghUrl(org, repo)}/fork`} target="_blank" rel="noopener">Fork {repo} <Ext /></a>
                    <button class="textlink" type="button" onClick={checkAgain}>Check again</button>
                  </p>
                </>
              )}
          </section>
        );
      })}
      {own.length ? (
        <section class="your-repo" aria-labelledby={`h-own-${org}`}>
          <h3 id={`h-own-${org}`}>Your assignment repos</h3>
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

/** One semester's repos: read once the section is opened. */
function SemesterRepos({ org }: { org: string }) {
  const env = useEnv();
  const load = useLoad(
    env
      ? async () => {
          const f = await studentData(env.client).facts(org);
          // A role that cannot be read still leaves the fork check.
          return { f, m: f ? await readMine(env.client, org, env.user.login, f.assignments).catch(() => null) : null };
        }
      : null,
    [org],
  );
  if (load.kind === 'loading') return <Loading what="Reading your repos" />;
  if (load.kind === 'failed') return <CheckLine cls="bad">The semester could not be read: {load.error}</CheckLine>;
  if (!load.value.f) return <p class="footnote">This semester publishes no materials yet.</p>;
  return <RepoChecks org={org} facts={load.value.f} mine={load.value.m} />;
}

/** "Your repos in <course>, <semester>": folded until opened, `open` for the first one. */
export function YourRepos({ semester, open = false }: { semester: Semester; open?: boolean }) {
  const [shown, setShown] = useState(open);
  return (
    <details class="panel section fold your-repos" open={open} onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && setShown(true)}>
      <summary><h2>Your repos in {semesterName(semester)}</h2></summary>
      {shown ? <SemesterRepos org={semester.org} /> : null}
    </details>
  );
}
