// The one Open control of both consoles (decisions 0017, 0027, 0035): a split button modelled on
// GitHub's Code button, its main part opening the primary target, its arrow a menu of every way
// to open in three sections - In the browser, On your computer, and a last line to Profile.
//
// `kind: 'repo'` (the course and semester screens, the template and settings page heads, the
// student's repos): the main part does the last choice made (remembered per login in this
// browser); the menu lists On GitHub and On github.dev, then Clone in VS Code / Open in VS Code
// (each with its `?`, decision 0024 rule 7), Open in GitHub Desktop and the editor Profile names.
// Where the folder check can tell (decision 0023) it keeps Open or Clone, whichever applies, and
// the main part reads "Clone" or "Open"; it renders "Open or clone" until it knows, and the
// arrow's click asks for read permission after a reload. The last line is "Change your profile",
// or "Set up a local folder" before Profile has one.
//
// The file variant (the default; the differing-files panel, and every file row through
// `ui/FileRow.tsx`): the main part opens the file on GitHub in a new tab; the menu lists, in the
// browser, the console's own view when the caller has one, On GitHub, and On github.dev (in the
// person's fork when the caller says so) or, for a document no editor opens (pdf, docx, ...),
// "Not an editable format" greyed; on the computer, the editor Profile names ("In VS Code"),
// "Set up your editor in Profile" until Profile has a folder and an editor that takes a path, or
// the same greyed line. `caretOnly` (a file row, where the name is the main part) draws the
// row's small chevron and the menu without the main part.
//
// A menu button in the WAI-ARIA sense (`useMenu`): the arrow opens it from the keyboard (Enter,
// Space, Down, Up), arrows move through its items, Escape closes it and gives focus back, Tab
// and a click outside close it, which keeps one open at a time. A `?` is not an item: off the
// arrow keys and the tab order, it opens on hover or a tap.

import { signal } from '@preact/signals';
import type { ComponentChildren, RefObject } from 'preact';
import { useEffect, useId, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { courseFolder, defaultItem, editableFile, editorName, fileLocal, fileOnline, isWeb, mainLabel, menuLabel, openItems, profileHref, repoCloneCommand, type OpenItem, type RepoRef, type Setup } from '../model/open';
import { askFolderOnce, folderChanged, isCloned } from '../model/localFolder';
import { rememberOpen, yourSetup } from '../model/prefs';
import { ghUrl } from './bits';
import { Hint } from './Hint';
import { Ext } from './icons';

/** A link that leaves the console opens in a new tab. */
export const newTabIf = (leaves: boolean) => (leaves ? { target: '_blank', rel: 'noopener' } : {});

/** A menu button's menu: open state, focus, and the keys (Enter, Space, Down, Up on the button; arrows, Home, End, Escape, Tab in the menu). */
export interface Menu<W extends HTMLElement = HTMLDivElement> {
  open: boolean;
  setOpen: (open: boolean) => void;
  id: string;
  wrap: RefObject<W>;
  caret: RefObject<HTMLButtonElement>;
  show: (at: 'first' | 'last') => void;
  shut: () => void;
  onCaretKey: (e: KeyboardEvent) => void;
  onCaretKeyUp: (e: KeyboardEvent) => void;
  onMenuKey: (e: KeyboardEvent) => void;
}

/** The menu's behaviour: closes on a click outside `wrap`, which is also what keeps one open at a time; `onShow` runs as it opens. */
export function useMenu<W extends HTMLElement = HTMLDivElement>(onShow?: () => void): Menu<W> {
  const [open, setOpen] = useState(false);
  const [focusAt, setFocusAt] = useState<'first' | 'last' | null>(null);
  const id = useId();
  const wrap = useRef<W>(null);
  const caret = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [open]);
  useEffect(() => {
    if (!open || !focusAt) return;
    const all = menuItems();
    (focusAt === 'first' ? all[0] : all[all.length - 1])?.focus();
    setFocusAt(null);
  }, [open, focusAt]);

  const menuItems = () => [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
  const show = (at: 'first' | 'last') => {
    onShow?.();
    setOpen(true);
    setFocusAt(at);
  };
  const shut = () => {
    setOpen(false);
    caret.current?.focus();
  };
  const onCaretKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      show('first');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      show('last');
    }
  };
  // Firefox toggles a button on Space's keyup as well: the keydown already opened it.
  const onCaretKeyUp = (e: KeyboardEvent) => {
    if (e.key === ' ') e.preventDefault();
  };
  const onMenuKey = (e: KeyboardEvent) => {
    const all = menuItems();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const go = (i: number) => {
      e.preventDefault();
      all[(i + all.length) % all.length]?.focus();
    };
    if (e.key === 'ArrowDown') go(at + 1);
    else if (e.key === 'ArrowUp') go(at - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(all.length - 1);
    else if (e.key === 'Escape') {
      e.preventDefault();
      shut();
    } else if (e.key === 'Tab') setOpen(false);
  };
  return { open, setOpen, id, wrap, caret, show, shut, onCaretKey, onCaretKeyUp, onMenuKey };
}

/** Bumped when a choice is remembered, so every Open button on the page takes the new default. */
const changed = signal(0);
function useSetup(login: string): [Setup | null, (item: OpenItem) => void] {
  void changed.value;
  const setup = yourSetup(login);
  return [setup, (item) => {
    rememberOpen(login, item.choice);
    changed.value++;
  }];
}

/**
 * Whether the folder check finds `repo` in `home`'s course folder; undefined until it first
 * answers, or when it cannot tell. A recheck of the same repo keeps the last answer until the
 * new one comes.
 */
function useCloned(login: string, home: string, repo: string, setup: Setup | null): boolean | undefined {
  const key = `${login}/${home}/${repo}`;
  const [answer, setAnswer] = useState<{ key: string; cloned?: boolean }>({ key });
  const version = folderChanged.value;
  const folder = courseFolder(setup, home);
  useEffect(() => {
    if (!login || !folder) return setAnswer({ key });
    let live = true;
    void isCloned(login, home, repo, setup).then((cloned) => live && setAnswer({ key, cloned }));
    return () => {
      live = false;
    };
  }, [key, folder, setup?.folder, version]);
  // An answer about another repo (the props changed) is no answer.
  return answer.key === key ? answer.cloned : undefined;
}

/** The arrow and its menu, shared by both variants: `main` before the arrow unless the row's name is the main part (`chevron`). */
function Shell({ menu, main, chevron, small, cls, what, children }: {
  menu: Menu<HTMLElement>;
  main?: ComponentChildren;
  /** A file row's small chevron in place of the split's arrow (`.file-menu`). */
  chevron?: boolean;
  small?: boolean;
  /** The arrow's button classes. */
  cls: string;
  /** What it opens, for the arrow's and the menu's names. */
  what: string;
  children: ComponentChildren;
}) {
  const { open, setOpen, id, wrap, caret, show, onCaretKey, onCaretKeyUp, onMenuKey } = menu;
  const arrow = {
    ref: caret, type: 'button' as const, 'aria-haspopup': 'menu' as const, 'aria-expanded': open, 'aria-controls': id, 'aria-label': `More ways to open ${what}`,
    onClick: () => (open ? setOpen(false) : show('first')), onKeyDown: onCaretKey, onKeyUp: onCaretKeyUp,
  };
  const list = <div class="popmenu open-menu" id={id} role="menu" aria-label={`Open ${what}`} hidden={!open} onKeyDown={onMenuKey}>{children}</div>;
  if (chevron) {
    return (
      <span class="file-menu" ref={wrap as RefObject<HTMLSpanElement>}>
        <button {...arrow} class="file-chev" title="More ways to open">▾</button>
        {list}
      </span>
    );
  }
  return (
    <div class={`split${small ? ' small' : ''}`} ref={wrap as RefObject<HTMLDivElement>}>
      {main}
      <button {...arrow} class={`${cls} split-caret`}><span class="caret" aria-hidden="true" /></button>
      {list}
    </div>
  );
}

const heading = (text: string) => <div class="pm-h" role="presentation">{text}</div>;
const greyed = (text: string) => <span class="pm-sub pm-off">{text}</span>;

export type RepoSplitProps = RepoRef & { kind: 'repo'; small?: boolean; quiet?: boolean };

function RepoSplit({ small, quiet, kind: _kind, ...ref }: RepoSplitProps) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [setup, remember] = useSetup(login);
  const home = ref.home ?? ref.org;
  const cloned = useCloned(login, home, ref.repo, setup);
  const items = openItems(ref, setup, cloned);
  const main = defaultItem(items, setup, cloned);
  const label = mainLabel(main, cloned);
  const menu = useMenu<HTMLElement>(() => {
    if (login) void askFolderOnce(login);
  });
  const { open, setOpen, wrap, shut } = menu;
  // A `?` in the menu is not one of its items (decision 0024): out of the tab order, its text
  // the description of the item beside it.
  useEffect(() => {
    wrap.current?.querySelectorAll<HTMLElement>('.open-menu .pm-row').forEach((row) => {
      const btn = row.querySelector<HTMLElement>('.hint-btn');
      const pop = row.querySelector<HTMLElement>('.hint-pop');
      if (btn) btn.tabIndex = -1;
      if (pop) row.querySelector('[role="menuitem"]')?.setAttribute('aria-describedby', pop.id);
    });
  });
  const choose = (item: OpenItem) => {
    remember(item);
    // From the menu, focus goes back to the arrow rather than to the page.
    if (open) shut();
  };
  const cls = `btn${quiet ? ' quiet' : ''}${small ? ' small' : ''}`;
  const link = (item: OpenItem, extra: Record<string, unknown>, children: ComponentChildren) => (
    <a href={item.href} {...newTabIf(isWeb(item.href))} onClick={() => choose(item)} {...extra}>{children}</a>
  );
  const folder = !!courseFolder(setup, home);
  const hints = {
    open: <Hint label="About opening in VS Code">Opens the repo’s folder on your computer. Clone it first if it is not there yet.</Hint>,
    clone: <Hint label="About cloning in VS Code">VS Code asks where to put it{folder ? '; choose your course folder' : ''}. Or run: <code class="pm-cmd">{repoCloneCommand(ref, setup)}</code></Hint>,
  };
  const entry = (item: OpenItem) => {
    const row = link(item, { role: 'menuitem', tabIndex: -1 }, <><span>{menuLabel(item)}</span>{isWeb(item.href) ? <Ext /> : null}</>);
    return item.hint ? <div class="pm-row" role="none">{row}{hints[item.hint]}</div> : row;
  };
  // The small button keeps one short word; its title and name carry the whole label.
  const mainPart = link(main, { class: `${cls} split-main`, ...(small ? { title: label, 'aria-label': label } : {}) }, <>{small ? (cloned === false ? 'Clone' : 'Open') : label}{isWeb(main.href) ? <Ext /> : null}</>);
  return (
    <Shell menu={menu} main={mainPart} small={small} cls={cls} what={ref.repo}>
      {heading('In the browser')}
      {items.filter((i) => i.group === 'online').map(entry)}
      <hr />
      {heading('On your computer')}
      {items.filter((i) => i.group === 'local').map(entry)}
      <hr />
      <a href={profileHref(home, !!ref.home)} role="menuitem" tabIndex={-1} onClick={() => setOpen(false)}>
        <span>{folder ? 'Change your profile' : 'Set up a local folder'}</span>
      </a>
    </Shell>
  );
}

export interface FileSplitProps {
  kind?: 'file';
  org: string;
  repo: string;
  path: string;
  /** The signed-in person's Profile (`useYourSetup`). */
  setup: Setup | null;
  /** Profile, where "Set up your editor in Profile" and "Change your profile" go. */
  profile: string;
  /** The file's GitHub page, when the caller has one (its branch is github.dev's too); else the blob at HEAD. */
  url?: string;
  /** The console's own view of the file, opened in a new tab ("In the console"). */
  view?: string;
  /** Whose copy github.dev opens: the person's fork (`forked`), else the org's. */
  forkOwner?: string;
  /** The file's name in the arrow's and the menu's names; the path when not given. */
  name?: string;
  /** A file row's chevron and menu, without the main part (`ui/FileRow.tsx`). */
  caretOnly?: boolean;
}

function FileSplit({ org, repo, path, setup, profile, url, view, forkOwner, name, caretOnly }: FileSplitProps) {
  const menu = useMenu<HTMLElement>();
  const blob = url || ghUrl(org, repo, path, 'HEAD');
  const edit = editableFile(path);
  const local = edit ? fileLocal(setup, org, repo, path) : null;
  // Before a Profile, the editor it presets, as the site names it.
  const named = editorName(setup ?? { folder: '', editor: 'vscode' });
  const what = name ?? path;
  const close = () => menu.setOpen(false);
  const item = (href: string, label: string) => (
    <a href={href} role="menuitem" tabIndex={-1} {...newTabIf(isWeb(href) || href === view)} onClick={close}><span>{label}</span>{isWeb(href) ? <Ext /> : null}</a>
  );
  const setUp = edit && !local;
  const main = <a class="btn quiet small split-main" href={blob} target="_blank" rel="noopener" title="Open on GitHub" aria-label={`Open ${what} on GitHub`}>Open</a>;
  return (
    <Shell menu={menu} main={main} chevron={caretOnly} small cls="btn quiet small" what={what}>
      {heading('In the browser')}
      {view ? item(view, 'In the console') : null}
      {item(blob, 'On GitHub')}
      {edit ? item(fileOnline(forkOwner ?? org, repo, path, url), forkOwner ? 'On github.dev, in your fork' : 'On github.dev') : greyed('Not an editable format')}
      <hr />
      {heading('On your computer')}
      {!edit ? greyed('Not an editable format') : local ? item(local, named ? `In ${named}` : 'In your editor') : item(profile, 'Set up your editor in Profile')}
      {setUp ? null : <><hr />{item(profile, 'Change your profile')}</>}
    </Shell>
  );
}

export type OpenSplitProps = FileSplitProps | RepoSplitProps;

/** The Open split button: a file's (the default) or a repo's (`kind: 'repo'`). */
export function OpenSplit(p: OpenSplitProps) {
  return p.kind === 'repo' ? <RepoSplit {...p} /> : <FileSplit {...p} />;
}
