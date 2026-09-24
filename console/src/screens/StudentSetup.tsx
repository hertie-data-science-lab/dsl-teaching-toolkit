// Set up: working on the materials and the assignments on the student's own machine. The
// console checks, with the student's token, whether they have forked each materials repo
// (`GET /repos/{login}/{repo}`, which the public site never could), then gives the clone
// command, the "open in" links and the command that keeps a fork up to date. The local
// folders it writes the commands for are remembered in this browser only.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { GitHubClient } from '../github/client';
import type { Mine } from '../model/mine';
import { localPaths, saveLocalPaths, type LocalPaths } from '../model/prefs';
import type { SemesterFacts } from '../model/student';
import { Loading } from '../ui/bits';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';

export type ForkState = { kind: 'forked'; url: string } | { kind: 'none' } | { kind: 'other'; url: string };

/** Whether `login` has a fork of `org/repo` under the same name: a repo of that name that is not its fork is `other`. */
export async function forkOf(client: GitHubClient, login: string, org: string, repo: string): Promise<ForkState> {
  const r = await client.getRepo(login, repo);
  if (!r) return { kind: 'none' };
  return r.fork && r.parent?.full_name.toLowerCase() === `${org}/${repo}`.toLowerCase() ? { kind: 'forked', url: r.html_url } : { kind: 'other', url: r.html_url };
}

/** `folder` + `name`, with the folder's own separator (a Windows folder keeps its backslashes). */
export function joinPath(folder: string, name: string): string {
  const f = folder.trim().replace(/[\\/]+$/, '');
  if (!f) return name;
  return `${f}${f.includes('\\') && !f.includes('/') ? '\\' : '/'}${name}`;
}

/** VS Code's link to open a local folder: forward slashes, one after `file`, a drive letter kept. */
export const vscodeFolder = (path: string) => `vscode://file/${path.replace(/\\/g, '/').replace(/^\/+/, '')}`;

export const cloneCommand = (url: string, folder: string, name: string) => `git clone ${url}.git${folder.trim() ? ` "${joinPath(folder, name)}"` : ''}`;

function Cmd({ text }: { text: string }) {
  return <pre class="file-text cmd">{text}</pre>;
}

export function SetupView({ org, facts, mine, studentView }: { org: string; facts: SemesterFacts; mine: Mine | null; studentView: boolean }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [paths, setPaths] = useState<LocalPaths>(() => localPaths(login));
  const [draft, setDraft] = useState<LocalPaths>(paths);
  const [tick, setTick] = useState(0);
  const repos = facts.materialsRepos;
  const forks = useLoad(env && !studentView ? () => Promise.all(repos.map((r) => forkOf(env.client, login, org, r))) : null, [org, repos.join(','), tick]);
  if (studentView) return <p class="footnote">A student sets up their fork and local copy here: whether they have forked the materials, the clone command, and links to open it in an editor.</p>;
  const save = () => {
    saveLocalPaths(login, draft);
    setPaths(draft);
  };
  const own = mine ? Object.values(mine.units).filter((u) => u.repo) : [];
  return (
    <div class="stack">
      <section class="panel section" aria-labelledby="h-folders">
        <h2 id="h-folders">Your local folders</h2>
        <div class="field">
          <label for="s-mat">Materials folder</label>
          <input type="text" id="s-mat" value={draft.materials} spellcheck={false} autocomplete="off" placeholder={`/Users/you/${org}`} onInput={(e) => setDraft({ ...draft, materials: (e.target as HTMLInputElement).value })} />
        </div>
        <div class="field">
          <label for="s-asg">Assignments folder</label>
          <input type="text" id="s-asg" value={draft.assignments} spellcheck={false} autocomplete="off" placeholder="the materials folder" onInput={(e) => setDraft({ ...draft, assignments: (e.target as HTMLInputElement).value })} />
          <p class="hint">Where your clones go; the commands below use them. Kept in this browser only.</p>
        </div>
        <div class="actions"><button class="btn outline small" type="button" onClick={save} disabled={draft.materials === paths.materials && draft.assignments === paths.assignments}>Save the folders</button></div>
      </section>
      {repos.map((repo, i) => {
        const f = forks.kind === 'ready' ? forks.value[i] : null;
        const upstream = `https://github.com/${org}/${repo}`;
        const mineUrl = `https://github.com/${login}/${repo}`;
        const local = paths.materials.trim() ? joinPath(paths.materials, repo) : '';
        return (
          <section class="panel section" aria-label={repo}>
            <h2>{repo}</h2>
            <h3>1. Fork it</h3>
            {forks.kind === 'loading' ? <Loading what="Checking your fork" />
              : f?.kind === 'forked' ? <p class="check-line ok"><span>You have forked it: <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a></span></p>
              : (
                <>
                  {f?.kind === 'other' ? <p class="check-line warn"><span>You have a repo named <a href={f.url} target="_blank" rel="noopener">{login}/{repo} <Ext /></a> that is not a fork of this semester’s; fork under another name, or rename that one.</span></p> : null}
                  <p class="actions">
                    <a class="btn small" href={`${upstream}/fork`} target="_blank" rel="noopener">Fork {repo} <Ext /></a>
                    <button class="textlink" type="button" onClick={() => setTick(tick + 1)}>Check again</button>
                  </p>
                </>
              )}
            <h3>2. Clone your fork</h3>
            <Cmd text={cloneCommand(mineUrl, paths.materials, repo)} />
            <p class="actions"><a class="btn outline small" href={`vscode://vscode.git/clone?url=${encodeURIComponent(mineUrl)}`}>Clone in VS Code</a></p>
            <h3>3. Open it</h3>
            <p class="actions">
              <a class="btn outline small" href={`https://github.dev/${login}/${repo}`} target="_blank" rel="noopener">Open on github.dev <Ext /></a>
              {local ? <a class="btn outline small" href={vscodeFolder(local)}>Open {local} in VS Code</a> : <span class="footnote">Save a materials folder above to open your clone locally.</span>}
            </p>
            <h3>New materials each week</h3>
            <Cmd text={`git remote add upstream ${upstream}.git   # once\ngit pull upstream main`} />
          </section>
        );
      })}
      {own.length ? (
        <section class="panel section" aria-labelledby="h-own">
          <h2 id="h-own">Your assignment repos</h2>
          <ul class="plain-list">
            {own.map((u) => (
              <li>
                <b>{u.repo}</b>{u.shared ? <span class="footnote"> (shared: your work goes in your {u.team ? 'team’s' : 'own'} folder)</span> : null}
                <Cmd text={cloneCommand(`https://github.com/${org}/${u.repo}`, paths.assignments || paths.materials, u.repo!)} />
                <p class="actions"><a class="btn outline small" href={`https://github.dev/${org}/${u.repo}`} target="_blank" rel="noopener">Open on github.dev <Ext /></a></p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
