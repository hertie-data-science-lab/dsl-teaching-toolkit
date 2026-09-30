// Where a course repo opens: on GitHub, on github.dev, in VS Code, in GitHub Desktop, in
// another editor by its link scheme, or from a clone command. One set of rules for the
// student Set up screen and the instructors' Open button (decision 0017). The local folder
// and editor come from Your setup (`model/prefs.ts`); nothing here reads storage.

// `vscode` opens the local folder (or, with no folder set up, clones); `vsclone` clones with a
// folder set up, beside it (decision 0023).
export type OpenChoice = 'github' | 'githubdev' | 'vscode' | 'desktop' | 'editor' | 'clone' | 'vsclone';
export const OPEN_CHOICES: OpenChoice[] = ['github', 'githubdev', 'vscode', 'desktop', 'editor', 'clone', 'vsclone'];

export type Editor = 'vscode' | 'desktop' | 'other';
export const EDITORS: Editor[] = ['vscode', 'desktop', 'other'];

/** Your setup: the folder the course repos live under, the editor, and the Open button's last choice. */
export interface Setup {
  folder: string;
  editor: Editor;
  /** Another editor's link, with `{path}` where the folder goes (`myeditor://open/{path}`). */
  scheme?: string;
  lastOpen?: OpenChoice;
}

/** A repo to open, and optionally a branch and a path inside it. */
export interface RepoRef {
  org: string;
  repo: string;
  branch?: string;
  path?: string;
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
 * The folder a course's repos go in: a folder of the course org's name inside Your setup's
 * folder, so two courses with a `materials` repo each do not share one clone. A folder that
 * already ends in the org's name is used as it is. Empty when no folder is set up.
 */
export function courseFolder(folder: string, org: string): string {
  const f = folder.trim().replace(/[\\/]+$/, '');
  if (!f) return '';
  return lastSegment(f).toLowerCase() === org.toLowerCase() ? f : joinPath(f, org);
}

/** The last segment of a typed folder (`courseFolder`'s test, and the picked folder's name to compare). */
export const lastSegment = (folder: string) => folder.trim().replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? '';

/** `/tree/<branch>/<path>` inside a repo on github.com or github.dev; nothing for the repo's root on its default branch. */
const inRepo = (r: RepoRef) => (r.path || r.branch ? `/tree/${r.branch ?? 'main'}${r.path ? `/${r.path.split('/').filter(Boolean).map(encodeURIComponent).join('/')}` : ''}` : '');

export const repoUrl = (r: RepoRef) => `https://github.com/${r.org}/${r.repo}`;

/** One entry of the Open menu: a link, or a command to copy. */
export interface OpenItem {
  choice: OpenChoice;
  label: string;
  href?: string;
  /** The text to copy, for the clone command. */
  copy?: string;
  /** Where it sits in the menu. */
  group: 'online' | 'local';
}

/** Profile, about `org`'s course, so its clone block lists the repos of the course the person came from. */
export const profileHref = (org: string) => `?course=${encodeURIComponent(org)}#profile`;

/**
 * Every way to open `r` with this setup, in menu order. With a folder set up, VS Code both
 * opens it and clones (decision 0023); `cloned` (from the folder check) keeps only the one
 * that applies: Open when the repo is there, Clone when it is not. Undefined keeps both.
 */
export function openItems(r: RepoRef, setup: Setup | null, cloned?: boolean): OpenItem[] {
  const url = repoUrl(r);
  const parent = courseFolder(setup?.folder ?? '', r.org);
  const local = parent ? joinPath(parent, r.repo) : '';
  const inside = local ? (r.path ?? '').split('/').filter(Boolean).reduce(joinPath, local) : '';
  const vsClone = `vscode://vscode.git/clone?url=${encodeURIComponent(url)}${r.branch ? `&ref=${encodeURIComponent(r.branch)}` : ''}`;
  const canOpen = !!local && cloned !== false;
  const canClone = !local || cloned !== true;
  const items: OpenItem[] = [
    { choice: 'github', label: 'Open on GitHub', href: `${url}${inRepo(r)}`, group: 'online' },
    { choice: 'githubdev', label: 'Open on github.dev', href: `https://github.dev/${r.org}/${r.repo}${inRepo(r)}`, group: 'online' },
  ];
  if (!local) items.push({ choice: 'vscode', label: 'Clone in VS Code', href: vsClone, group: 'local' });
  if (canOpen) items.push({ choice: 'vscode', label: 'Open in VS Code', href: vscodeFolder(inside), group: 'local' });
  if (local && canClone) items.push({ choice: 'vsclone', label: 'Clone in VS Code', href: vsClone, group: 'local' });
  items.push({ choice: 'desktop', label: 'Open in GitHub Desktop', href: `x-github-client://openRepo/${url}${r.branch ? `?branch=${encodeURIComponent(r.branch)}` : ''}`, group: 'local' });
  const scheme = setup?.editor === 'other' ? (setup.scheme ?? '').trim() : '';
  // Another editor opens a folder or nothing: with no folder set up, the menu's last line
  // ("Set up a local folder") is its way in.
  if (canOpen && schemeOk(scheme)) items.push({ choice: 'editor', label: 'Open in your editor', href: scheme.replace('{path}', urlPath(inside)), group: 'local' });
  if (canClone) items.push({ choice: 'clone', label: 'Copy the clone command', copy: cloneCommand(url, parent, r.repo), group: 'local' });
  return items;
}

const EDITOR_CHOICE: Record<Editor, OpenChoice> = { vscode: 'vscode', desktop: 'desktop', other: 'editor' };

/**
 * The button's own action: the last choice, else the editor once a folder is set up, else
 * GitHub. When the folder check took that away, its opposite: a clone for an open; VS Code
 * for a VS Code clone; the editor, else VS Code, for the clone command.
 */
export function defaultItem(items: OpenItem[], setup: Setup | null): OpenItem {
  const find = (c: OpenChoice | undefined) => items.find((i) => i.choice === c);
  const opener = setup ? EDITOR_CHOICE[setup.editor] : 'vscode';
  const folder = !!setup?.folder.trim();
  const want = setup?.lastOpen ?? (folder ? opener : undefined);
  const opposite: OpenChoice[] = want === 'vscode' || want === 'editor' ? ['vsclone'] : want === 'vsclone' ? ['vscode'] : want === 'clone' ? [opener, 'vscode'] : [];
  return find(want) ?? opposite.map(find).find(Boolean) ?? (folder ? find(opener) : undefined) ?? items[0];
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
