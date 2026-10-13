// How a released file is linked on the student screens (decision 0035 rule 10): the name opens
// it inside the console when it lives in a materials repo (in All materials; in a kind tab's
// list, in that tab), else on GitHub; then one chevron, "More ways to open" (`.file-menu`), the
// site's file menu: one list, no headings, the same four one-line rows on every file in the same
// order, each an icon, a label and a short muted status on the right - `View here` (a drawn view
// in a new tab), `View on GitHub` (the blob), a hairline, `Edit in browser` (github.dev, in the
// student's fork when they forked the repo, else the org's) and `Edit in <editor>`, the editor
// Profile names (`Edit locally` when it has no name; VS Code, the preset, before a Profile), in
// the semester's folder. A row a file lacks is still listed, greyed (`aria-disabled`, no href)
// with its reason as the status, worded as the site words it. (The site lists `View here` only
// for a hosted copy; the console always can say why it draws nothing, so its four are fixed.)
// `View here` opens the console's viewer for a file the name opens in the console and the
// viewer draws (markdown, notebook, html, pdf, image); for any other file in the org, GitHub's
// blob page when GitHub draws it (markdown, notebook, pdf, image; not html), and `View on GitHub`
// then takes `?plain=1` for markdown and notebooks so the two differ; any other file's `View
// here` is greyed, "no preview for this file". Both Edit rows are greyed "not an editable
// format" for a file no editor opens; the editor row is live only once Profile has a folder and
// an editor that takes a path, and until then greyed "set up in Profile", Profile linked. The menu is the instructor Open button's
// (`ui/OpenButton.tsx` `useMenu`). Whether the student forked a repo is read once per session,
// when a row first needs it (`model/fork.ts`). Every file link the student screens draw renders
// through here, the Home syllabus and Updates box (`inline`: the name in brackets, no menu) and
// the assignment page's CONTRIBUTIONS.md included.

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { cachedFork, type ForkState } from '../model/fork';
import { editableFile, editorName, fileLocal, fileOnline, isWeb, profileHref, type Setup } from '../model/open';
import { viewKind, type ViewKind } from '../model/viewer';
import { yourSetup } from '../model/prefs';
import { repoPath, type FileLink } from '../model/student';
import { studentHref } from '../router';
import { ghUrl } from '../ui/bits';
import { Code, Eye, Laptop, Pencil } from '../ui/icons';
import { useMenu } from '../ui/OpenButton';

/** The route that opens a file, `<repo>/<path>`: in All materials, or after a slash in the kind tab `tab` (`kind-<key>`). */
export const materialHref = (org: string, repo: string, path: string, tab?: string) => {
  const entry = encodeURIComponent(`${repo}/${path}`);
  return tab ? `${studentHref(org, tab)}/${entry}` : `${studentHref(org, 'materials')}-${entry}`;
};

/** The signed-in person's Profile, read once by a list for all its rows. */
export function useYourSetup(): Setup | null {
  const login = useEnv()?.user.login ?? '';
  return login ? yourSetup(login) : null;
}

/** Where a released file opens: inside the console when it is in a materials repo (in the kind tab `tab` when one is named), else on GitHub. */
export function fileHref(org: string, repos: string[], l: FileLink, tab?: string): { href: string; ext: boolean } {
  return l.repo && l.path && repos.includes(l.repo) ? { href: materialHref(org, l.repo, l.path, tab), ext: false } : { href: l.url, ext: true };
}

/** The kinds the console's viewer draws, so a file of one it opens gets `view`. */
const DRAWN: ViewKind[] = ['markdown', 'notebook', 'html', 'pdf', 'image'];
/** The kinds GitHub's blob page draws (an html file shows as source there), so a file the console cannot open gets `view` there. */
const GH_DRAWN: ViewKind[] = ['markdown', 'notebook', 'pdf', 'image'];

/** The repo and path of a file in `org`, or null for a link elsewhere (no menu then). */
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

/** Why a row is greyed - the site's own words (templates/site/_includes/open_in.html `WHY`), but for `View here`, which the console draws itself rather than publishes. */
const WHY = {
  view: 'no preview for this file',
  notEditable: 'not an editable format',
  profile: 'set up in ',
};

type MenuItem =
  | {
      href: string;
      icon: () => preact.JSX.Element;
      label: string;
      /** Where it goes, on the right. */
      status: string;
      /** Opens in a new tab: every item but an app's own scheme. */
      tab: boolean;
    }
  | {
      /** Greyed: listed so every menu reads the same, with the reason it is not live. */
      off: string;
      icon: () => preact.JSX.Element;
      label: string;
      /** The reason ends in a link to Profile, the step that makes the item live. */
      profile?: string;
    };

/** The chevron after a file's name and its menu, the two View rows, a hairline, the two Edit rows; `setup` is the Profile the list read. */
function FileMenu({ org, repos, link, setup, tab }: { org: string; repos: string[]; link: FileLink; setup: Setup | null; tab?: string }) {
  const env = useEnv();
  const at = inOrg(org, link);
  // The same format test the semester site makes once per link (templates/site/_includes/edit_why.html).
  const edit = !!at && editableFile(at.path);
  const fork = useFork(org, at?.repo ?? '', edit);
  const menu = useMenu<HTMLSpanElement>();
  if (!at) return null;
  const name = link.name || at.path.split('/').pop() || 'file';
  const login = env?.user.login ?? '';
  const forked = fork?.kind === 'forked';
  const local = edit ? fileLocal(setup, org, at.repo, at.path) : null;
  const shown = fileHref(org, repos, link, tab);
  const kind = viewKind(at.path);
  const blob = link.url || ghUrl(org, at.repo, at.path, 'HEAD');
  const drawn = !shown.ext ? (DRAWN.includes(kind) ? shown.href : null) : GH_DRAWN.includes(kind) ? blob : null;
  // Where `view` is GitHub's own blob page, markdown and notebooks show their text with `?plain=1`.
  const source = shown.ext && (kind === 'markdown' || kind === 'notebook') ? `${blob}${blob.includes('?') ? '&' : '?'}plain=1` : blob;
  // Before a Profile, the editor it presets, as the site names it.
  const named = editorName(setup ?? { folder: '', editor: 'vscode' });
  const editor = named ? `Edit in ${named}` : 'Edit locally';
  const opens: MenuItem[] = [
    drawn
      ? { href: drawn, icon: Eye, label: 'View here', status: shown.ext ? 'on GitHub' : 'rendered here', tab: true }
      : { off: WHY.view, icon: Eye, label: 'View here' },
    { href: source, icon: Code, label: 'View on GitHub', status: 'course repo', tab: true },
  ];
  const edits: MenuItem[] = !edit
    ? [{ off: WHY.notEditable, icon: Pencil, label: 'Edit in browser' }, { off: WHY.notEditable, icon: Laptop, label: editor }]
    : [
        { href: fileOnline(forked ? login : org, at.repo, at.path, link.url), icon: Pencil, label: 'Edit in browser', status: forked ? 'your fork' : 'course repo', tab: true },
        local
          ? { href: local, icon: Laptop, label: editor, status: 'your clone', tab: isWeb(local) }
          : { off: WHY.profile, icon: Laptop, label: editor, profile: profileHref(org, true) },
      ];
  const item = (i: MenuItem) =>
    'off' in i ? (
      <div class="fm-off">
        <span class="fm-lbl" role="menuitem" aria-disabled="true" tabIndex={-1}><i.icon />{i.label}</span>
        <small>
          {i.off}
          {i.profile ? <a href={i.profile} role="menuitem" tabIndex={-1} onClick={() => menu.setOpen(false)}>Profile</a> : null}
        </small>
      </div>
    ) : (
      <a href={i.href} role="menuitem" tabIndex={-1} {...newTabIf(i.tab)} onClick={() => menu.setOpen(false)}>
        <span class="fm-lbl"><i.icon />{i.label}</span>
        <small>{i.status}</small>
      </a>
    );
  return (
    <span class="file-menu" ref={menu.wrap}>
      <button ref={menu.caret} type="button" class="file-chev" title="More ways to open" aria-label={`More ways to open ${name}`} aria-haspopup="menu" aria-expanded={menu.open} aria-controls={menu.id}
        onClick={() => (menu.open ? menu.setOpen(false) : menu.show('first'))} onKeyDown={menu.onCaretKey} onKeyUp={menu.onCaretKeyUp}>▾</button>
      <div class="popmenu file-menu-list" id={menu.id} role="menu" aria-label={`Open ${name}`} hidden={!menu.open} onKeyDown={menu.onMenuKey}>
        {opens.map(item)}
        <div class="fm-sep" role="separator" />
        {edits.map(item)}
      </div>
    </span>
  );
}

interface ItemProps {
  org: string;
  repos: string[];
  link: FileLink;
  setup: Setup | null;
  /** The name's class. */
  cls?: string;
  /** The file is open beside the list. */
  current?: boolean;
  /** The kind tab the file is listed in. */
  tab?: string;
  /** In a sentence (the Updates box): the name in brackets, no menu. */
  inline?: boolean;
  /** What the link says instead of the file's name (the Home syllabus button). */
  label?: ComponentChildren;
}

/** A file's name, linked, then its menu: the one file link of the student screens. */
export function FileLinkItem({ org, repos, link, setup, cls, current, tab, inline, label }: ItemProps) {
  const h = fileHref(org, repos, link, tab);
  const a = <a class={cls} href={h.href} {...newTabIf(h.ext)} {...(current ? { 'aria-current': 'page' as const } : {})}>{label ?? (link.name || 'file')}</a>;
  return (
    <span class="file-link">
      {inline ? <>[{a}]</> : <>{a}<FileMenu org={org} repos={repos} link={link} setup={setup} tab={tab} /></>}
    </span>
  );
}

const linkKey = (l: FileLink) => l.url || `${l.repo}/${l.path}`;

/** A row's files as chips, each with its menu (the schedule). */
export function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  const setup = useYourSetup();
  return <>{links.map((l) => <FileLinkItem key={linkKey(l)} org={org} repos={repos} link={l} setup={setup} cls="st-chip" />)}</>;
}

/** A row's files as a list, the site's `session-files`, each with its menu; in a kind tab (`tab`) they open there, `current` (`<repo>/<path>`) marked. */
export function FileList({ org, repos, links, tab, current }: { org: string; repos: string[]; links: FileLink[]; tab?: string; current?: string }) {
  const setup = useYourSetup();
  return (
    <ul class="session-files">
      {links.map((l) => <li key={linkKey(l)}><FileLinkItem org={org} repos={repos} link={l} setup={setup} tab={tab} current={!!current && `${l.repo}/${l.path}` === current} /></li>)}
    </ul>
  );
}
