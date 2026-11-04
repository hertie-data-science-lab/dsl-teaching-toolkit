// The one file row both consoles draw (decision 0035 rule 10, decision 0040 rule 6): the file's
// name, opening it inside the console (`here`, the route the caller's viewer answers) where it
// can, else on GitHub; then one chevron, "More ways to open", the Open split button's menu
// (`ui/OpenSplit.tsx`, `caretOnly`): In the browser - In the console (the console's viewer in a
// new tab, for a file the name opens in the console and the viewer draws: markdown, notebook,
// html, pdf, image), On GitHub, On github.dev (in the person's fork when they forked the repo,
// else the org's); On your computer - the editor Profile names, in the org's folder; then
// Change your profile. Whether the person forked a repo is read once per session, when a row
// first needs it (`model/fork.ts`).

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { cachedFork, type ForkState } from '../model/fork';
import { editableFile, type Setup } from '../model/open';
import { yourSetup } from '../model/prefs';
import { repoPath, type FileLink } from '../model/student';
import { viewKind, type ViewKind } from '../model/viewer';
import { newTabIf, OpenSplit } from './OpenSplit';

/** The signed-in person's Profile, read once by a list for all its rows. */
export function useYourSetup(): Setup | null {
  const login = useEnv()?.user.login ?? '';
  return login ? yourSetup(login) : null;
}

/** Where a row's name goes: `here` (inside the console) when the caller gives one, else the file's own link. */
const shownAt = (link: FileLink, here?: string): { href: string; ext: boolean } => (here ? { href: here, ext: false } : { href: link.url, ext: true });

/** The kinds the console's viewer draws, so a file of one it opens gets "In the console". */
const DRAWN: ViewKind[] = ['markdown', 'notebook', 'html', 'pdf', 'image'];

/** The repo and path of a file in `org`, or null for a link elsewhere (no menu then). */
function inOrg(org: string, l: FileLink): { repo: string; path: string } | null {
  if (!l.repo || !l.path) return null;
  return !l.url || repoPath(l.url, org) ? { repo: l.repo, path: l.path } : null;
}

/** Whether the signed-in person forked `org/repo`: undefined until known, or without an account. */
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

/** The chevron after a file's name and its menu; `setup` is the Profile the list read. */
function FileMenu({ org, link, here, setup, profile }: { org: string; link: FileLink; here?: string; setup: Setup | null; profile: string }) {
  const env = useEnv();
  const at = inOrg(org, link);
  // The same format test the semester site makes once per link (templates/site/_includes/edit_why.html).
  const edit = !!at && editableFile(at.path);
  const fork = useFork(org, at?.repo ?? '', edit);
  if (!at) return null;
  const name = link.name || at.path.split('/').pop() || 'file';
  const view = here && DRAWN.includes(viewKind(at.path)) ? here : undefined;
  const forkOwner = fork?.kind === 'forked' ? env?.user.login : undefined;
  return <OpenSplit org={org} repo={at.repo} path={at.path} url={link.url || undefined} setup={setup} profile={profile} view={view} forkOwner={forkOwner} name={name} caretOnly />;
}

export interface FileRowProps {
  org: string;
  link: FileLink;
  /** The route that opens the file inside the console; without one the name goes to `link.url`. */
  here?: string;
  setup: Setup | null;
  /** Profile, where "Set up your editor in Profile" points. */
  profile: string;
  /** The name's class. */
  cls?: string;
  /** The file is open beside the list. */
  current?: boolean;
  /** In a sentence (the Updates box): the name in brackets, no menu. */
  inline?: boolean;
  /** What the link says instead of the file's name (the Home syllabus button). */
  label?: ComponentChildren;
}

/** A file's name, linked, then its menu. */
export function FileRow({ org, link, here, setup, profile, cls, current, inline, label }: FileRowProps) {
  const h = shownAt(link, here);
  const a = <a class={cls} href={h.href} {...newTabIf(h.ext)} {...(current ? { 'aria-current': 'page' as const } : {})}>{label ?? (link.name || 'file')}</a>;
  return (
    <span class="file-link">
      {inline ? <>[{a}]</> : <>{a}<FileMenu org={org} link={link} here={here} setup={setup} profile={profile} /></>}
    </span>
  );
}
