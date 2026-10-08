// Where a course repo opens: on GitHub, on github.dev, in VS Code, in GitHub Desktop, in
// another editor by its link scheme. One Open button for both
// roles (decisions 0017, 0027): instructors on their course screens, students in Profile. A
// released file's `online` and `local` buttons (decision 0035 rule 10) come from here too. The
// folder and editor come from Profile (`model/prefs.ts`); nothing here reads storage.

import { ghUrl } from '../ui/bits';

// `vscode` opens the local folder (or, with no folder set up, clones); `vsclone` clones with a
// folder set up, beside it (decision 0023).
export type OpenChoice = 'github' | 'githubdev' | 'vscode' | 'desktop' | 'editor' | 'vsclone';
export const OPEN_CHOICES: OpenChoice[] = ['github', 'githubdev', 'vscode', 'desktop', 'editor', 'vsclone'];

export type Editor = 'vscode' | 'desktop' | 'other';
export const EDITORS: Editor[] = ['vscode', 'desktop', 'other'];

/** Profile: the root folder the course repos live under, the editor, and the Open button's last choice. */
export interface Setup {
  folder: string;
  editor: Editor;
  /** Another editor's link, with `{path}` where the folder goes (`myeditor://open/{path}`). */
  scheme?: string;
  lastOpen?: OpenChoice;
  /** A course's own folder instead of `<folder>/<org>`, by lower-case org (decision 0027 rule 1). */
  overrides?: Record<string, string>;
}

/** A repo to open, and optionally a branch and a path inside it. */
export interface RepoRef {
  org: string;
  repo: string;
  branch?: string;
  path?: string;
  /**
   * The semester whose folder the clone goes in, set on the student screens: a student's fork
   * sits with its semester's repos. Unset, the folder is `org`'s course folder.
   */
  home?: string;
}

/** `folder` + `name`, with the folder's own separator (a Windows folder keeps its backslashes). */
export function joinPath(folder: string, name: string): string {
  const f = folder.trim().replace(/[\\/]+$/, '');
  if (!f) return name;
  return `${f}${f.includes('\\') && !f.includes('/') ? '\\' : '/'}${name}`;
}

/** A local path as a URL path: forward slashes, no leading one, each segment encoded, a drive letter's colon kept. */
// UNC paths (\\server\share) are not supported, as on the live site's profile.
function urlPath(path: string): string {
  return path.replace(/\\/g, '/').split('/').filter(Boolean).map((s) => encodeURIComponent(s).replace(/%3A/gi, ':')).join('/');
}

/** VS Code's link to open a local folder: forward slashes, one after `file`, a drive letter kept. */
export const vscodeFolder = (path: string) => `vscode://file/${urlPath(path)}`;

export const cloneCommand = (url: string, folder: string, name: string) => `git clone ${url}.git${folder.trim() ? ` "${joinPath(folder, name)}"` : ''}`;

/** An editor link can take a folder only when `{path}` follows a slash or a colon, as in `vscode://file/{path}`. */
export const schemeOk = (scheme: string) => /[/:]\{path\}/.test(scheme.trim());

/**
 * `<root>/<org>`: a folder of the course org's name inside the root folder, so two courses
 * with a `materials` repo each do not share one clone. A root that already ends in the org's
 * name is used as it is. Empty when no root is set.
 */
export function orgFolder(root: string, org: string): string {
  const f = root.trim().replace(/[\\/]+$/, '');
  if (!f) return '';
  return lastSegment(f).toLowerCase() === org.toLowerCase() ? f : joinPath(f, org);
}

/** The course's own folder, if Profile names one. */
export const overrideOf = (setup: Setup | null, org: string) => (setup?.overrides?.[org.toLowerCase()] ?? '').trim().replace(/[\\/]+$/, '');

/** The folder a course's repos go in: its override, else `<root>/<org>`. Empty when neither is set. */
export const courseFolder = (setup: Setup | null, org: string) => overrideOf(setup, org) || orgFolder(setup?.folder ?? '', org);

/** `setup` with `org`'s override set to `folder`, or dropped when `folder` is empty or the default. */
export function withOverride(setup: Setup, org: string, folder: string): Setup {
  const overrides = { ...setup.overrides };
  delete overrides[org.toLowerCase()];
  const f = folder.trim().replace(/[\\/]+$/, '');
  if (f && f !== orgFolder(setup.folder, org)) overrides[org.toLowerCase()] = f;
  const next: Setup = { ...setup };
  if (Object.keys(overrides).length) next.overrides = overrides;
  else delete next.overrides;
  return next;
}

/** The last segment of a typed folder (`orgFolder`'s test, and the picked folder's name to compare). */
export const lastSegment = (folder: string) => folder.trim().replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? '';

/** `/tree/<branch>/<path>` inside a repo on github.com or github.dev; nothing for the repo's root on its default branch. */
const inRepo = (r: RepoRef) => (r.path || r.branch ? `/tree/${r.branch ?? 'main'}${r.path ? `/${r.path.split('/').filter(Boolean).map(encodeURIComponent).join('/')}` : ''}` : '');

export const repoUrl = (r: RepoRef) => ghUrl(r.org, r.repo);

/** One entry of the Open menu. */
export interface OpenItem {
  choice: OpenChoice;
  label: string;
  href: string;
  /** Where it sits in the menu. */
  group: 'online' | 'local';
  /** Which `?` the menu gives it: VS Code's clone or its open (decision 0024 rule 7). */
  hint?: 'clone' | 'open';
}

/**
 * Profile, about the org the person came from, so its folder check knows it: `?course=` for
 * a course, `?cohort=` for a semester (the student screens), which the App maps to its course.
 */
export const profileHref = (org: string, semester = false) => `?${semester ? 'cohort' : 'course'}=${encodeURIComponent(org)}#profile`;

/**
 * Every way to open `r` with this setup, in menu order. With a folder set up, VS Code both
 * opens it and clones (decision 0023); `cloned` (from the folder check) keeps only the one
 * that applies: Open when the repo is there, Clone when it is not. Undefined keeps both.
 */
export function openItems(r: RepoRef, setup: Setup | null, cloned?: boolean): OpenItem[] {
  const url = repoUrl(r);
  const parent = courseFolder(setup, r.home ?? r.org);
  const local = parent ? joinPath(parent, r.repo) : '';
  const inside = local ? (r.path ?? '').split('/').filter(Boolean).reduce(joinPath, local) : '';
  const vsClone = `vscode://vscode.git/clone?url=${encodeURIComponent(url)}${r.branch ? `&ref=${encodeURIComponent(r.branch)}` : ''}`;
  const canOpen = !!local && cloned !== false;
  const canClone = !local || cloned !== true;
  const items: OpenItem[] = [
    { choice: 'github', label: 'Open on GitHub', href: `${url}${inRepo(r)}`, group: 'online' },
    { choice: 'githubdev', label: 'Open on github.dev', href: `https://github.dev/${r.org}/${r.repo}${inRepo(r)}`, group: 'online' },
  ];
  // Clone before Open: the order the person goes through them (decision 0024 rule 7).
  if (!local) items.push({ choice: 'vscode', label: 'Clone in VS Code', href: vsClone, group: 'local', hint: 'clone' });
  if (local && canClone) items.push({ choice: 'vsclone', label: 'Clone in VS Code', href: vsClone, group: 'local', hint: 'clone' });
  if (canOpen) items.push({ choice: 'vscode', label: 'Open in VS Code', href: vscodeFolder(inside), group: 'local', hint: 'open' });
  items.push({ choice: 'desktop', label: 'Open in GitHub Desktop', href: `x-github-client://openRepo/${url}${r.branch ? `?branch=${encodeURIComponent(r.branch)}` : ''}`, group: 'local' });
  const scheme = setup?.editor === 'other' ? (setup.scheme ?? '').trim() : '';
  // Another editor opens a folder or nothing: with no folder set up, the menu's last line
  // ("Set up a local folder") is its way in.
  if (canOpen && schemeOk(scheme)) items.push({ choice: 'editor', label: 'Open in your editor', href: scheme.replace('{path}', urlPath(inside)), group: 'local' });
  return items;
}

/** The clone command for `r` into its course folder, which Clone's `?` gives (decisions 0024 rule 7, 0027 rule 2). */
export const repoCloneCommand = (r: RepoRef, setup: Setup | null) => cloneCommand(repoUrl(r), courseFolder(setup, r.home ?? r.org), r.repo);

const EDITOR_CHOICE: Record<Editor, OpenChoice> = { vscode: 'vscode', desktop: 'desktop', other: 'editor' };

const CLONES: OpenChoice[] = ['vsclone', 'desktop'];
const OPENS: OpenChoice[] = ['vscode', 'desktop', 'editor'];

/**
 * The button's own action (decision 0027 rule 2). A remembered web choice (GitHub,
 * github.dev) stays the action in every state. Where the folder check tells the repo is
 * not there: a clone, the remembered one if it clones, else the same tool's (a VS Code open
 * becomes a VS Code clone), else the editor's (GitHub Desktop clones on its own; anything
 * else clones in VS Code). Where it is there: an open, likewise. Where it cannot tell: the
 * last choice, else the editor once a folder is set up, else GitHub.
 */
export function defaultItem(items: OpenItem[], setup: Setup | null, cloned?: boolean): OpenItem {
  const find = (c: OpenChoice | undefined) => items.find((i) => i.choice === c);
  const opener = setup ? EDITOR_CHOICE[setup.editor] : 'vscode';
  const last = setup?.lastOpen;
  if (last === 'github' || last === 'githubdev') return find(last) ?? items[0];
  const vs = last === 'vscode' || last === 'vsclone';
  if (cloned === false) return find(last && CLONES.includes(last) ? last : vs ? 'vsclone' : opener === 'desktop' ? 'desktop' : 'vsclone') ?? find('vscode') ?? items[0];
  if (cloned === true) return find(last && OPENS.includes(last) ? last : vs ? 'vscode' : opener) ?? find('vscode') ?? items[0];
  // A folder is set up for this repo's course when VS Code can open it.
  const folder = items.some((i) => i.hint === 'open');
  const want = last ?? (folder ? opener : undefined);
  return find(want) ?? (want === 'vsclone' ? find('vscode') : undefined) ?? (folder ? find(opener) : undefined) ?? items[0];
}

/**
 * The words on the button: Clone or Open when the folder check tells, "Open or clone" when
 * it cannot and the action is on this computer; a web page's own label.
 */
export function mainLabel(item: OpenItem, cloned?: boolean): string {
  if (item.group === 'online') return item.label;
  return cloned === false ? 'Clone' : cloned === true ? 'Open' : 'Open or clone';
}

/** Whether a link leaves for a web page (a new tab) rather than an app's own scheme (no empty tab left behind). */
export const isWeb = (href: string) => /^https?:/i.test(href);

export type Platform = 'mac' | 'win' | 'linux';

/** The reader's platform as far as the browser says, else macOS. */
export function platformOf(nav: { platform?: string; userAgentData?: { platform?: string } } | undefined = globalThis.navigator): Platform {
  const p = nav?.userAgentData?.platform || nav?.platform || '';
  if (/^win/i.test(p)) return 'win';
  if (/^(linux|x11|.*bsd|cros|chrome ?os)/i.test(p)) return 'linux';
  return 'mac';
}

/** An example folder for the course repos, spelt the way the reader's platform spells a home folder. */
export function folderExample(p: Platform): string {
  return p === 'win' ? 'C:\\Users\\you\\Documents\\repositories' : p === 'linux' ? '/home/you/repositories' : '/Users/you/Documents/repositories';
}

// --------------------------------------------------------------------------- one file (decision 0035 rule 10)

/** Files an editor opens: everything but the documents and archives the 0.9.0 site left out. */
const NOT_EDITABLE = ['pdf', 'pptx', 'docx', 'xlsx', 'zip'];
export const editableFile = (path: string) => {
  const name = path.split('/').pop() ?? '';
  const dot = name.lastIndexOf('.');
  return !NOT_EDITABLE.includes(dot > 0 ? name.slice(dot + 1).toLowerCase() : '');
};

const encPath = (path: string) => path.split('/').filter(Boolean).map(encodeURIComponent).join('/');

/** The branch a GitHub blob or tree URL names, else HEAD (the repo's default). */
export const branchOf = (url: string | undefined) => /^https:\/\/github\.com\/[^/]+\/[^/]+\/(?:blob|tree)\/([^/]+)\//.exec(url ?? '')?.[1] ?? 'HEAD';

/** A file on github.dev: in `owner`'s copy (the student's fork, or the org's own repo), on the branch its GitHub `url` names. */
export const fileOnline = (owner: string, repo: string, path: string, url?: string) => `https://github.dev/${owner}/${repo}/blob/${branchOf(url)}/${encPath(path)}`;

/**
 * A file in the person's editor: `<course folder of home>/<repo>/<path>` through the editor
 * Profile names, VS Code's `vscode://file/` or another editor's scheme. Null without a folder,
 * and for GitHub Desktop, which opens repos, not files.
 */
export function fileLocal(setup: Setup | null, home: string, repo: string, path: string): string | null {
  const parent = courseFolder(setup, home);
  if (!setup || !parent) return null;
  const local = path.split('/').filter(Boolean).reduce(joinPath, joinPath(parent, repo));
  if (setup.editor === 'vscode') return vscodeFolder(local);
  const scheme = setup.editor === 'other' ? (setup.scheme ?? '').trim() : '';
  return schemeOk(scheme) ? scheme.replace('{path}', urlPath(local)) : null;
}
