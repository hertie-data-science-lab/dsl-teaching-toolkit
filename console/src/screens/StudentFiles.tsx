// How a released file is linked on the student screens (decision 0035 rule 10): the name opens
// it inside the console when it lives in a materials repo, else on GitHub; then the button row
// (`.file-btns`): `source` (the GitHub blob), `online` (github.dev, in the student's fork when
// they forked the repo, else the org's) and `local` (the editor Profile names, in the
// semester's folder). `online` and `local` only for a file an editor opens; `local` only once
// Profile has a folder and an editor that takes a path. Whether the student forked a repo is
// read once per session, when a row first needs it (`cachedFork`). Every file link the
// student screens draw renders through here; the Updates box keeps names only (`inline`).

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { editableFile, fileLocal, fileOnline, isWeb } from '../model/open';
import { yourSetup } from '../model/prefs';
import { repoPath, type FileLink } from '../model/student';
import { materialHref } from './StudentMaterials';
import { cachedFork, type ForkState } from './StudentSetup';

/** Where a released file opens: inside the console when it is in a materials repo, else on GitHub. */
export function fileHref(org: string, repos: string[], l: FileLink): { href: string; ext: boolean } {
  return l.repo && l.path && repos.includes(l.repo) ? { href: materialHref(org, l.repo, l.path), ext: false } : { href: l.url, ext: true };
}

/** The repo and path of a file in `org`, or null for a link elsewhere (no button row then). */
function inOrg(org: string, l: FileLink): { repo: string; path: string } | null {
  if (!l.repo || !l.path) return null;
  return !l.url || repoPath(l.url, org) ? { repo: l.repo, path: l.path } : null;
}

/** Whether the signed-in student forked `org/repo`: undefined until known, or without an account. */
function useFork(org: string, repo: string, want: boolean): ForkState | undefined {
  const env = useEnv();
  const [fork, setFork] = useState<{ key: string; state: ForkState } | null>(null);
  const key = `${org}/${repo}`;
  useEffect(() => {
    if (!env || !want) return;
    let live = true;
    void cachedFork(env.client, env.user.login, org, repo).then((state) => live && setFork({ key, state }), () => undefined);
    return () => {
      live = false;
    };
  }, [key, want, !!env]);
  return fork?.key === key ? fork.state : undefined;
}

const newTab = (href: string) => (isWeb(href) ? { target: '_blank', rel: 'noopener' } : {});

/** The buttons after a file's name: source, online, local. */
export function FileButtons({ org, link }: { org: string; link: FileLink }) {
  const env = useEnv();
  const at = inOrg(org, link);
  const edit = !!at && editableFile(at.path);
  const fork = useFork(org, at?.repo ?? '', edit);
  if (!at) return null;
  const name = link.name || at.path.split('/').pop() || 'file';
  const login = env?.user.login ?? '';
  const forked = fork?.kind === 'forked';
  const local = edit ? fileLocal(login ? yourSetup(login) : null, org, at.repo, at.path) : null;
  return (
    <span class="file-btns">
      <a class="file-btn" href={link.url} target="_blank" rel="noopener" title={`${name} on GitHub`}>source</a>
      {edit ? (
        <a class="file-btn" href={fileOnline(forked ? login : org, at.repo, at.path)} target="_blank" rel="noopener" title={forked ? `Edit ${name} in your fork, in the browser` : `Edit ${name} in the browser`}>online</a>
      ) : null}
      {local ? <a class="file-btn" href={local} {...newTab(local)} title={`Open ${name} in your editor`}>local</a> : null}
    </span>
  );
}

/** A file's name, linked (with the class `cls`; `current` for the file open beside it), then its button row: the one file link of the student screens. */
export function FileLinkItem({ org, repos, link, cls, current }: { org: string; repos: string[]; link: FileLink; cls?: string; current?: boolean }) {
  const h = fileHref(org, repos, link);
  return (
    <span class="file-link">
      <a class={cls} href={h.href} {...(h.ext ? { target: '_blank', rel: 'noopener' } : {})} {...(current ? { 'aria-current': 'page' as const } : {})}>{link.name || 'file'}</a>
      <FileButtons org={org} link={link} />
    </span>
  );
}

/** A row's files as chips, each with its button row (the schedule). */
export function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  return <>{links.map((l) => <FileLinkItem org={org} repos={repos} link={l} cls="st-chip" />)}</>;
}

/** A row's files as a list, the site's `session-files`, each with its button row. */
export function FileList({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  return (
    <ul class="session-files">
      {links.map((l) => <li><FileLinkItem org={org} repos={repos} link={l} /></li>)}
    </ul>
  );
}
