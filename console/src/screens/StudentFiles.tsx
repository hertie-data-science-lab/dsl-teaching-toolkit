// How a released file is linked on the student screens (decision 0035 rule 10): the name opens
// it inside the console when it lives in a materials repo, else on GitHub; then the button row
// (`.file-btns`): `source` (the GitHub blob), `online` (github.dev, in the student's fork when
// they forked the repo, else the org's) and `local` (the editor Profile names, in the
// semester's folder). `online` and `local` only for a file an editor opens; `local` only once
// Profile has a folder and an editor that takes a path. Whether the student forked a repo is
// read once per session, when a row first needs it (`model/fork.ts`). Every file link the
// student screens draw renders through here; the Updates box keeps names only (`inline`).

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { cachedFork, type ForkState } from '../model/fork';
import { editableFile, fileLocal, fileOnline, isWeb, type Setup } from '../model/open';
import { yourSetup } from '../model/prefs';
import { repoPath, type FileLink } from '../model/student';
import { studentHref } from '../router';
import { ghUrl } from '../ui/bits';

/** The route entry for a file: `<repo>/<path>`. */
export const materialHref = (org: string, repo: string, path: string) => `${studentHref(org, 'materials')}-${encodeURIComponent(`${repo}/${path}`)}`;

/** The signed-in person's Profile, read once by a list for all its rows. */
export function useYourSetup(): Setup | null {
  const login = useEnv()?.user.login ?? '';
  return login ? yourSetup(login) : null;
}

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

/** A link that leaves the console opens in a new tab. */
export const newTabIf = (leaves: boolean) => (leaves ? { target: '_blank', rel: 'noopener' } : {});

/** The buttons after a file's name: source, online, local; `setup` is the Profile the list read. */
export function FileButtons({ org, link, setup }: { org: string; link: FileLink; setup: Setup | null }) {
  const env = useEnv();
  const at = inOrg(org, link);
  const edit = !!at && editableFile(at.path);
  const fork = useFork(org, at?.repo ?? '', edit);
  if (!at) return null;
  const name = link.name || at.path.split('/').pop() || 'file';
  const login = env?.user.login ?? '';
  const forked = fork?.kind === 'forked';
  const local = edit ? fileLocal(setup, org, at.repo, at.path) : null;
  return (
    <span class="file-btns">
      <a class="file-btn" href={link.url || ghUrl(org, at.repo, at.path, 'HEAD')} target="_blank" rel="noopener" title={`${name} on GitHub`}>source</a>
      {edit ? (
        <a class="file-btn" href={fileOnline(forked ? login : org, at.repo, at.path, link.url)} target="_blank" rel="noopener" title={forked ? `Edit ${name} in your fork, in the browser` : `Edit ${name} in the browser`}>online</a>
      ) : null}
      {local ? <a class="file-btn" href={local} {...newTabIf(isWeb(local))} title={`Open ${name} in your editor`}>local</a> : null}
    </span>
  );
}

/** A file's name, linked (with the class `cls`; `current` for the file open beside it), then its button row: the one file link of the student screens. */
export function FileLinkItem({ org, repos, link, setup, cls, current }: { org: string; repos: string[]; link: FileLink; setup: Setup | null; cls?: string; current?: boolean }) {
  const h = fileHref(org, repos, link);
  return (
    <span class="file-link">
      <a class={cls} href={h.href} {...newTabIf(h.ext)} {...(current ? { 'aria-current': 'page' as const } : {})}>{link.name || 'file'}</a>
      <FileButtons org={org} link={link} setup={setup} />
    </span>
  );
}

const linkKey = (l: FileLink) => l.url || `${l.repo}/${l.path}`;

/** A row's files as chips, each with its button row (the schedule). */
export function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  const setup = useYourSetup();
  return <>{links.map((l) => <FileLinkItem key={linkKey(l)} org={org} repos={repos} link={l} setup={setup} cls="st-chip" />)}</>;
}

/** A row's files as a list, the site's `session-files`, each with its button row. */
export function FileList({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  const setup = useYourSetup();
  return (
    <ul class="session-files">
      {links.map((l) => <li key={linkKey(l)}><FileLinkItem org={org} repos={repos} link={l} setup={setup} /></li>)}
    </ul>
  );
}
